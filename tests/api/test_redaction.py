"""Values that act like passwords must not leak to roles or logs that don't need them.

The incoming autopatch PIN is an access control, and DTMF digits keyed on the
air can be one-time codes. Read-only accounts (and the API tokens handed to
helpers) can read the dashboard's config and logs, so neither may show them."""
import base64
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import AuditLog
from api.live_audio import describe_opening
from api.service import RepeaterService
from api.users import UserStore
from controller.events import DTMFDigit

PIN = "48151623"


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


ADMIN = basic("alice", "password1")
VIEWER = basic("bob", "password2")


def make():
    tmp = Path(tempfile.mkdtemp())
    users = UserStore()
    users.add("alice", "password1", "admin")
    users.add("bob", "password2", "viewer")
    audit = AuditLog()
    service = RepeaterService()
    app = create_app(service=service, start_background_tick=False, log_path=tmp / "t.log", users=users, audit=audit)
    return TestClient(app), audit, service, tmp / "t.log"


def set_pin(client):
    response = client.put(
        "/api/config", headers=ADMIN, json={"autopatch_incoming_enabled": True, "autopatch_incoming_pin": PIN}
    )
    assert response.status_code == 200, response.text
    return response


def test_pin_change_is_not_written_to_the_log_or_the_audit_record():
    client, audit, _, log_path = make()
    set_pin(client)
    log = log_path.read_text()
    assert PIN not in log
    assert "autopatch_incoming_pin" in log  # the field name stays, so troubleshooting still works
    details = " ".join(entry.detail for entry in audit.recent())
    assert PIN not in details
    assert "autopatch_incoming_pin" in details


def test_other_changes_keep_their_values_in_the_log():
    client, _, _, log_path = make()
    client.put("/api/config", headers=ADMIN, json={"hang_time": 7.5})
    assert "'hang_time': 7.5" in log_path.read_text()


def test_admin_sees_the_pin_and_a_viewer_does_not():
    client, _, _, _ = make()
    set_pin(client)
    assert client.get("/api/config", headers=ADMIN).json()["autopatch_incoming_pin"] == PIN
    viewer_view = client.get("/api/config", headers=VIEWER).json()
    assert PIN not in str(viewer_view)
    assert viewer_view["autopatch_incoming_pin"]  # still non-empty, so the page can say "PIN set"


def test_a_viewer_with_no_pin_set_sees_it_empty():
    client, _, _, _ = make()
    assert client.get("/api/config", headers=VIEWER).json()["autopatch_incoming_pin"] == ""


def test_dtmf_digit_values_are_not_logged():
    _, _, service, log_path = make()
    for digit in "7391":
        service.handle_audio_events([DTMFDigit(digit)])
    log = log_path.read_text()
    assert "DTMF digit" in log
    for digit in "7391":
        assert f"'{digit}'" not in log


def test_receiver_opening_log_text_hides_the_digits():
    class Opening:
        duration = 1.0
        open_db = -30.0
        peak_db = -20.0
        strongest_hz = None
        tone_share = 0.0
        ctcss_hz = None
        dtmf_digits = "5551212"
        after_tx = None

    text = describe_opening(Opening())
    assert "5551212" not in text
    assert "7 DTMF digits" in text
