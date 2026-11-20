import base64
import json

import fake_tailscaled as fake
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthSettings
from api.service import RepeaterService
from api.tailscale import (
    TailscaleControl,
    TailscaleSettings,
    dashboard_port,
    probe_tailscale,
    read_log,
    tailscale_settings_from_env,
    validate_auth_key,
)
from api.tailscale_localapi import LocalApiError
from api.users import UserStore


@pytest.fixture(autouse=True)
def _no_real_tailscaled(monkeypatch):
    """Without the CLI the API looks for tailscaled's socket; a developer's own must not leak into these tests."""
    monkeypatch.setenv("MOREOPENREPEATER_TAILSCALE_SOCKET", str(fake.short_dir() / "none.sock"))


def test_settings_come_from_the_service_environment(tmp_path):
    assert tailscale_settings_from_env({}) is None
    assert tailscale_settings_from_env({"MOREOPENREPEATER_TAILSCALE": str(tmp_path)}) is None
    settings = tailscale_settings_from_env(
        {
            "MOREOPENREPEATER_TAILSCALE": str(tmp_path),
            "MOREOPENREPEATER_TAILSCALE_REQUEST": str(tmp_path / "req"),
        }
    )
    assert settings == TailscaleSettings(tmp_path, tmp_path / "req")


def test_dashboard_port_defaults_and_rejects_junk():
    assert dashboard_port({}) == 8000
    assert dashboard_port({"MOREOPENREPEATER_PORT": "9000"}) == 9000
    assert dashboard_port({"MOREOPENREPEATER_PORT": "nope"}) == 8000
    assert dashboard_port({"MOREOPENREPEATER_PORT": "99999"}) == 8000


def test_auth_key_shape():
    assert validate_auth_key(None) is None
    assert validate_auth_key("  ") is None
    assert validate_auth_key("tskey-auth-abc123") == "tskey-auth-abc123"
    with pytest.raises(ValueError):
        validate_auth_key("not-a-key")
    with pytest.raises(ValueError):
        validate_auth_key("tskey-" + ("x" * 300))


def make_control(tmp_path, clock=None, run_json=None):
    settings = TailscaleSettings(tmp_path, tmp_path / "data" / "tailscale-request")
    settings.request_path.parent.mkdir(parents=True, exist_ok=True)
    now = [1_700_000_000.0]
    control = TailscaleControl(
        settings,
        clock=clock or (lambda: now[0]),
        run_json=run_json or (lambda _args: None),
    )
    return control, settings, now


def test_request_writes_json_without_logging_the_key(tmp_path):
    control, settings, _now = make_control(tmp_path)
    control.request("up", "tskey-auth-secretvalue")
    payload = json.loads(settings.request_path.read_text())
    assert payload == {"action": "up", "auth_key": "tskey-auth-secretvalue"}
    assert control.helper_status()["state"] == "requested"
    assert "secretvalue" not in json.dumps(control.snapshot())


def test_request_refuses_unknown_actions_and_keys_on_other_actions(tmp_path):
    control, _settings, _now = make_control(tmp_path)
    with pytest.raises(ValueError):
        control.request("funnel")
    with pytest.raises(ValueError):
        control.request("install", "tskey-auth-abc")
    with pytest.raises(RuntimeError):
        TailscaleControl(None).request("install")


def test_helper_status_times_out_when_the_path_unit_is_missing(tmp_path):
    control, _settings, now = make_control(tmp_path)
    control.request("install")
    now[0] += 120
    status = control.helper_status()
    assert status["state"] == "failed"
    assert "moreopenrepeater-tailscale.path" in status["message"]


def test_probe_maps_status_and_serve_json():
    def run_json(args):
        if args[:2] == ["tailscale", "status"]:
            return {
                "BackendState": "Running",
                "Self": {"DNSName": "pi.tail123.ts.net."},
                "AuthURL": "",
            }
        if args[:2] == ["tailscale", "serve"]:
            return {"Web": {"https://pi.tail123.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}}}
        return None

    from api import tailscale as mod

    original = mod.cli_installed
    mod.cli_installed = lambda: True
    try:
        live = probe_tailscale(run_json)
    finally:
        mod.cli_installed = original
    assert live["installed"] is True
    assert live["backend_state"] == "Running"
    assert live["dns_name"] == "pi.tail123.ts.net"
    assert live["serve_enabled"] is True
    assert live["serve_url"] == "https://pi.tail123.ts.net"


def test_read_log_returns_the_tail(tmp_path):
    settings = TailscaleSettings(tmp_path, tmp_path / "req")
    assert read_log(settings) == []
    (tmp_path / "tailscale.log").write_text("\n".join(f"line {i}" for i in range(10)))
    assert read_log(settings, lines=3) == ["line 7", "line 8", "line 9"]


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def make_client(tmp_path, control=None, users=None):
    app = create_app(
        service=RepeaterService(),
        start_background_tick=False,
        log_path=tmp_path / "t.log",
        auth_settings=AuthSettings("admin", "hunter2"),
        users=users if users is not None else UserStore(),
        tailscale=control if control is not None else TailscaleControl(None),
    )
    return TestClient(app)


def test_tailscale_route_reports_unavailable_without_units(tmp_path):
    body = make_client(tmp_path).get("/api/tailscale", headers=basic("admin", "hunter2")).json()
    assert body["available"] is False
    assert body["status"] is None
    assert body["port"] == 8000


def test_install_writes_the_request(tmp_path):
    control, settings, _now = make_control(tmp_path)
    client = make_client(tmp_path, control=control)
    response = client.post("/api/tailscale/install", headers=basic("admin", "hunter2"))
    assert response.status_code == 200
    assert response.json()["status"]["state"] == "requested"
    assert json.loads(settings.request_path.read_text()) == {"action": "install"}

    again = client.post("/api/tailscale/install", headers=basic("admin", "hunter2"))
    assert again.status_code == 409
    assert "already in progress" in again.json()["detail"]


def test_up_with_auth_key_and_serve_toggle(tmp_path):
    control, settings, _now = make_control(tmp_path)
    client = make_client(tmp_path, control=control)
    response = client.post(
        "/api/tailscale/up",
        json={"auth_key": "tskey-auth-abc"},
        headers=basic("admin", "hunter2"),
    )
    assert response.status_code == 200
    assert json.loads(settings.request_path.read_text()) == {
        "action": "up",
        "auth_key": "tskey-auth-abc",
    }
    # Finish the fictional helper run so we can ask for serve.
    settings.request_path.unlink()
    (settings.status_dir / "status.json").write_text(
        json.dumps({"state": "succeeded", "action": "up", "started_at": 1.0, "finished_at": 2.0})
    )
    control._requested_at = None
    response = client.post("/api/tailscale/serve", json={"enabled": True}, headers=basic("admin", "hunter2"))
    assert response.status_code == 200
    assert json.loads(settings.request_path.read_text()) == {"action": "serve-on"}


def test_bad_auth_key_is_rejected(tmp_path):
    control, _settings, _now = make_control(tmp_path)
    client = make_client(tmp_path, control=control)
    response = client.post(
        "/api/tailscale/up",
        json={"auth_key": "password"},
        headers=basic("admin", "hunter2"),
    )
    assert response.status_code == 400


def test_needs_install_pi_and_admin(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/tailscale/install", headers=basic("admin", "hunter2")).status_code == 409
    users = UserStore()
    users.add("alice", "password1", "admin")
    users.add("olive", "password2", "operator")
    client = make_client(tmp_path, users=users)
    assert client.get("/api/tailscale", headers=basic("olive", "password2")).status_code == 403
    assert client.post("/api/tailscale/install", headers=basic("olive", "password2")).status_code == 403


def test_log_route_tails_the_helper_log(tmp_path):
    control, settings, _now = make_control(tmp_path)
    (settings.status_dir / "tailscale.log").write_text("Installing…\nDone.\n")
    client = make_client(tmp_path, control=control)
    assert client.get("/api/tailscale/log", headers=basic("admin", "hunter2")).json() == ["Installing…", "Done."]


# --- no CLI: tailscaled's socket (container install) ------------------------------------------------------------------


@pytest.fixture
def tailscaled():
    server = fake.start()
    server.routes["/localapi/v0/status"] = fake.Reply(body=json.dumps(fake.STATUS).encode())
    server.routes["/localapi/v0/serve-config"] = fake.Reply(body=json.dumps(fake.SERVE).encode())
    yield server
    server.shutdown()
    server.server_close()


def no_cli(monkeypatch, present=False):
    from api import tailscale as mod

    monkeypatch.setattr(mod, "cli_installed", lambda: present)


def test_the_cli_wins_when_it_exists_and_the_socket_is_not_even_opened(monkeypatch):
    no_cli(monkeypatch, present=True)

    def socket_must_not_be_read(_path):
        raise AssertionError("the socket was read although the CLI exists")

    def run_json(args):
        return {"BackendState": "NeedsLogin", "Self": {"DNSName": "pi.t.ts.net."}} if args[1] == "status" else {}

    live = probe_tailscale(run_json, read_socket=socket_must_not_be_read)
    assert live["source"] == "cli" and live["backend_state"] == "NeedsLogin" and live["unreachable"] is False


def test_the_cli_failing_is_not_papered_over_by_the_socket(monkeypatch):
    """A CLI that can't read status keeps its own message; the socket is only for when there is no CLI."""
    no_cli(monkeypatch, present=True)
    live = probe_tailscale(lambda _args: None, read_socket=lambda _p: pytest.fail("socket read"))
    assert live["installed"] is True and live["source"] == "cli" and "join the tailnet" in live["cli_error"]


def test_without_the_cli_the_socket_gives_the_same_answer(monkeypatch, tailscaled):
    no_cli(monkeypatch)
    live = probe_tailscale(socket_path=tailscaled.server_address)
    assert live["installed"] is True and live["source"] == "socket" and live["unreachable"] is False
    assert live["backend_state"] == "Running"
    assert live["dns_name"] == "pi.tail123.ts.net"
    assert live["serve_enabled"] is True and live["serve_url"] == "https://pi.tail123.ts.net"
    assert live["cli_error"] is None


def test_the_socket_path_is_taken_from_the_environment_when_none_is_given(monkeypatch, tailscaled):
    no_cli(monkeypatch)
    monkeypatch.setenv("MOREOPENREPEATER_TAILSCALE_SOCKET", tailscaled.server_address)
    assert probe_tailscale()["source"] == "socket"


def test_no_socket_is_not_said_to_be_not_installed(monkeypatch):
    no_cli(monkeypatch)
    live = probe_tailscale()
    assert live["installed"] is False and live["unreachable"] is True and live["source"] is None
    assert "no tailscale socket" in live["cli_error"].lower() and "not installed" not in live["cli_error"].lower()


def test_each_way_the_socket_can_fail_has_its_own_words(monkeypatch):
    no_cli(monkeypatch)
    said = {}
    for reason in ("missing", "not_readable", "not_listening", "timeout", "denied"):
        message = f"words for {reason}"

        def fail(_path, reason=reason, message=message):
            raise LocalApiError(reason, message)

        live = probe_tailscale(read_socket=fail)
        assert live["unreachable"] is True and live["cli_error"] == message and live["socket_problem"] == reason
        said[reason] = live["cli_error"]
    assert len(set(said.values())) == 5


def test_a_dashboard_that_cannot_open_the_socket_says_permission_not_missing(monkeypatch, tailscaled):
    import os

    if os.geteuid() == 0:
        pytest.skip("root can open anything")
    no_cli(monkeypatch)
    os.chmod(tailscaled.server_address, 0o000)
    control = TailscaleControl(None, socket_path=tailscaled.server_address)
    snapshot = control.snapshot()
    assert snapshot["installed"] is False and snapshot["unreachable"] is True
    assert "permission denied" in snapshot["cli_error"] and "not installed" not in snapshot["cli_error"].lower()


def test_the_route_reports_the_host_s_tailscale_read_only(tmp_path, monkeypatch, tailscaled):
    no_cli(monkeypatch)
    control = TailscaleControl(None, socket_path=tailscaled.server_address)
    body = make_client(tmp_path, control=control).get("/api/tailscale", headers=basic("admin", "hunter2")).json()
    assert body["available"] is False  # the container can't install, join or change Serve
    assert body["installed"] is True and body["source"] == "socket" and body["unreachable"] is False
    assert body["backend_state"] == "Running" and body["dns_name"] == "pi.tail123.ts.net" and body["serve_enabled"] is True
    assert {r["method"] for r in tailscaled.requests} == {"GET"}


def test_the_route_says_when_the_socket_is_missing(tmp_path, monkeypatch):
    no_cli(monkeypatch)
    body = make_client(tmp_path).get("/api/tailscale", headers=basic("admin", "hunter2")).json()
    assert body["installed"] is False and body["unreachable"] is True
    assert "no tailscale socket" in body["cli_error"].lower()


def test_the_container_s_actions_still_refuse(tmp_path, monkeypatch, tailscaled):
    """Reading over the socket adds no way to change anything: the install, join and Serve routes stay refused."""
    no_cli(monkeypatch)
    client = make_client(tmp_path, control=TailscaleControl(None, socket_path=tailscaled.server_address))
    for path, payload in (("install", {}), ("up", {"auth_key": "tskey-auth-x"}), ("serve", {"enabled": True}), ("down", {})):
        assert client.post(f"/api/tailscale/{path}", json=payload, headers=basic("admin", "hunter2")).status_code == 409, path
    assert {r["method"] for r in tailscaled.requests} <= {"GET"}


def test_a_native_install_with_no_cli_and_no_socket_still_offers_to_install(tmp_path, monkeypatch):
    """install-pi.sh installs have the helper units; there a missing socket just means Tailscale isn't installed yet."""
    no_cli(monkeypatch)
    control, _settings, _now = make_control(tmp_path)
    snapshot = control.snapshot()
    assert snapshot["available"] is True and snapshot["installed"] is False
    assert snapshot["unreachable"] is False and snapshot["cli_error"] is None


def test_a_native_install_with_a_socket_it_cannot_open_is_told_so(tmp_path, monkeypatch):
    no_cli(monkeypatch)
    control, _settings, _now = make_control(tmp_path)

    def denied(_path):
        raise LocalApiError("not_readable", "permission denied")

    control._read_socket = denied
    snapshot = control.snapshot()
    assert snapshot["unreachable"] is True and snapshot["cli_error"] == "permission denied"
