"""The Winlink status probes and route (ADR 0006): real sockets on 127.0.0.1, nothing mocked but the clock and one slow connect."""
import asyncio
import importlib
import socket
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import winlink as winlink_module
from api.app import create_app
from api.auth import AuthSettings
from api.service import RepeaterService
from api.winlink import (
    CHECK_IDS,
    GATEWAY_ADMIN,
    GATEWAY_TELNET,
    MODEM_KISS,
    WinlinkSettings,
    WinlinkStatus,
    settings_from_env,
)
from controller.config import RepeaterConfig

LOCAL = "127.0.0.1"


class Server:
    """A listening port on 127.0.0.1 that counts the connections it gets and answers them one way."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.connections = 0
        self.port = 0
        self._writers: list = []
        self._server = None

    async def _handle(self, reader, writer):
        self.connections += 1
        self._writers.append(writer)
        if self.behaviour == "accept":
            writer.close()
        elif self.behaviour == "http":
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Basic realm=x\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
        elif self.behaviour == "http-200":
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
        elif self.behaviour == "garbage":
            writer.write(b"SSH-2.0-hello\r\n")
            await writer.drain()
            writer.close()
        # "silent": accepts the connection and never says anything

    async def start(self) -> "Server":
        self._server = await asyncio.start_server(self._handle, LOCAL, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        self._server.close()
        for writer in self._writers:
            writer.close()


@asynccontextmanager
async def serving(**behaviours: str):
    servers = {name: await Server(behaviour).start() for name, behaviour in behaviours.items()}
    try:
        yield servers
    finally:
        for server in servers.values():
            await server.stop()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOCAL, 0))
        return probe.getsockname()[1]


def settings_for(admin=None, telnet=None, kiss=None, **extra) -> WinlinkSettings:
    """Point each check at a server, or at a closed port when given None."""
    return WinlinkSettings(
        gateway_host=LOCAL,
        modem_host=LOCAL,
        admin_port=admin.port if admin else closed_port(),
        telnet_port=telnet.port if telnet else closed_port(),
        kiss_port=kiss.port if kiss else closed_port(),
        **extra,
    )


def by_id(result: dict) -> dict:
    return {check["id"]: check for check in result["checks"]}


# -- the probes ----------------------------------------------------------------------------------------------------------


def test_everything_answering_is_enabled_with_no_problems():
    async def main():
        async with serving(admin="http-200", telnet="accept", kiss="accept") as s:
            return await WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"], callsign="W1AW")).status()

    result = asyncio.run(main())
    assert result["enabled"] is True and result["problems"] == [] and result["callsign"] == "W1AW"
    assert [check["id"] for check in result["checks"]] == list(CHECK_IDS)
    assert all(check["ok"] for check in result["checks"])


def test_a_401_from_the_admin_page_counts_as_answering():
    async def main():
        async with serving(admin="http", telnet="accept", kiss="accept") as s:
            return await WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"])).status()

    assert by_id(asyncio.run(main()))[GATEWAY_ADMIN]["ok"] is True


def test_something_on_the_admin_port_that_is_not_http_does_not_count():
    async def main():
        async with serving(admin="garbage", telnet="accept", kiss="accept") as s:
            return await WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"])).status()

    assert by_id(asyncio.run(main()))[GATEWAY_ADMIN]["ok"] is False


def test_a_closed_port_fails_with_a_plain_reason_and_a_problem_is_listed():
    async def main():
        async with serving(admin="http", telnet="accept") as s:
            return await WinlinkStatus(settings_for(s["admin"], s["telnet"], None)).status()

    result = asyncio.run(main())
    checks = by_id(result)
    assert checks[MODEM_KISS]["ok"] is False and "refused" in checks[MODEM_KISS]["reason"]
    assert checks[GATEWAY_ADMIN]["ok"] and checks[GATEWAY_TELNET]["ok"]
    assert result["enabled"] is True  # something answers, so the profile is running and one part is down
    assert len(result["problems"]) == 1 and "see How to set this up below" in result["problems"][0]
    assert "docker compose" not in result["problems"][0]  # the command lives in the page's setup section, not in the status text


@pytest.mark.parametrize("up", [GATEWAY_ADMIN, GATEWAY_TELNET, MODEM_KISS])
def test_any_one_part_answering_means_it_is_running_and_the_others_are_problems(up):
    async def main():
        async with serving(**{up: "http-200" if up == GATEWAY_ADMIN else "accept"}) as s:
            ports = {GATEWAY_ADMIN: None, GATEWAY_TELNET: None, MODEM_KISS: None}
            ports[up] = s[up]
            return await WinlinkStatus(settings_for(ports[GATEWAY_ADMIN], ports[GATEWAY_TELNET], ports[MODEM_KISS])).status()

    result = asyncio.run(main())
    assert result["enabled"] is True
    assert [check["id"] for check in result["checks"] if check["ok"]] == [up]
    assert len(result["problems"]) == 2


def test_the_modem_and_the_gateway_are_looked_for_at_their_own_hosts():
    async def main(gateway_host, modem_host):
        async with serving(admin="http-200", telnet="accept", kiss="accept") as s:
            settings = WinlinkSettings(
                gateway_host=gateway_host, modem_host=modem_host,
                admin_port=s["admin"].port, telnet_port=s["telnet"].port, kiss_port=s["kiss"].port,
            )
            return by_id(await WinlinkStatus(settings, timeout=2.0).status())

    modem_only = asyncio.run(main("no-such-service.invalid", LOCAL))
    assert modem_only[MODEM_KISS]["ok"] and not modem_only[GATEWAY_ADMIN]["ok"] and not modem_only[GATEWAY_TELNET]["ok"]
    gateway_only = asyncio.run(main(LOCAL, "no-such-service.invalid"))
    assert not gateway_only[MODEM_KISS]["ok"] and gateway_only[GATEWAY_ADMIN]["ok"] and gateway_only[GATEWAY_TELNET]["ok"]


def test_nothing_answering_is_not_enabled_and_lists_no_problems():
    result = asyncio.run(WinlinkStatus(settings_for()).status())
    assert result["enabled"] is False and result["problems"] == []
    assert not any(check["ok"] for check in result["checks"])


def test_an_unset_host_is_not_enabled_and_never_connects():
    result = asyncio.run(WinlinkStatus(WinlinkSettings()).status())
    assert result["enabled"] is False
    assert all(check["reason"] == "Not set up on this install." for check in result["checks"])


def test_a_host_that_does_not_resolve_is_not_enabled():
    settings = WinlinkSettings(gateway_host="no-such-service.invalid", modem_host="no-such-service.invalid")
    result = asyncio.run(WinlinkStatus(settings, timeout=2.0).status())
    assert result["enabled"] is False and not any(check["ok"] for check in result["checks"])


def test_a_gateway_that_accepts_and_never_answers_times_out_instead_of_hanging():
    async def main():
        async with serving(admin="silent", telnet="accept", kiss="accept") as s:
            started = time.monotonic()
            result = await WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"]), timeout=0.3).status()
            return result, time.monotonic() - started

    result, elapsed = asyncio.run(main())
    admin = by_id(result)[GATEWAY_ADMIN]
    assert admin["ok"] is False and admin["reason"] == "No answer."
    assert 0.25 <= elapsed < 1.0  # the timeout, not the 2 s default and not forever
    assert by_id(result)[GATEWAY_TELNET]["ok"] and by_id(result)[MODEM_KISS]["ok"]  # the others are not held up


def test_the_default_hard_timeout_is_two_seconds():
    assert winlink_module.PROBE_TIMEOUT_SECONDS == 2.0
    assert WinlinkStatus(WinlinkSettings()).timeout == 2.0
    assert WinlinkStatus(WinlinkSettings()).cache_seconds == 15.0


@pytest.fixture
def slow_connect(monkeypatch):
    """Every connection attempt takes `delay` seconds before it is really made, like a host that does not answer."""
    real = asyncio.open_connection

    def install(delay: float):
        async def slow(*args, **kwargs):
            await asyncio.sleep(delay)
            return await real(*args, **kwargs)

        monkeypatch.setattr(asyncio, "open_connection", slow)

    return install


def test_the_three_probes_run_together_not_one_after_another(slow_connect):
    slow_connect(5.0)  # all three would take 5 s; the timeout is 0.3 s each, so one after another would be 0.9 s

    async def main():
        started = time.monotonic()
        result = await WinlinkStatus(settings_for(), timeout=0.3).status()
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(main())
    assert not any(check["ok"] for check in result["checks"])
    assert all(check["reason"] == "No answer." for check in result["checks"])
    assert 0.25 <= elapsed < 0.7


def test_slow_probes_do_not_stall_the_event_loop(slow_connect):
    slow_connect(5.0)

    async def main():
        gaps = []

        async def heartbeat():
            last = time.monotonic()
            while True:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        ticker = asyncio.ensure_future(heartbeat())
        await WinlinkStatus(settings_for(), timeout=0.3).status()
        ticker.cancel()
        return gaps

    gaps = asyncio.run(main())
    assert len(gaps) > 10 and max(gaps) < 0.1


# -- the cache -----------------------------------------------------------------------------------------------------------


def test_a_second_read_inside_the_cache_time_does_not_connect_again():
    async def main():
        async with serving(admin="http-200", telnet="accept", kiss="accept") as s:
            now = [100.0]
            status = WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"]), clock=lambda: now[0])
            first = await status.status()
            now[0] += 14.0
            second = await status.status()
            counts_inside = [server.connections for server in s.values()]
            now[0] += 2.0  # 16 s since the probe: stale
            third = await status.status()
            return first, second, third, counts_inside, [server.connections for server in s.values()]

    first, second, third, inside, after = asyncio.run(main())
    assert second is first and inside == [1, 1, 1]
    assert third is not first and after == [2, 2, 2]


def test_callers_that_arrive_together_share_one_probe():
    async def main():
        async with serving(admin="http-200", telnet="accept", kiss="accept") as s:
            status = WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"]))
            await asyncio.gather(*(status.status() for _ in range(8)))
            return [server.connections for server in s.values()]

    assert asyncio.run(main()) == [1, 1, 1]


def test_a_caller_that_gives_up_does_not_cancel_the_probe_for_the_rest():
    async def main():
        async with serving(admin="silent", telnet="accept", kiss="accept") as s:
            status = WinlinkStatus(settings_for(s["admin"], s["telnet"], s["kiss"]), timeout=0.3)
            impatient = asyncio.ensure_future(status.status())
            patient = asyncio.ensure_future(status.status())
            await asyncio.sleep(0.05)
            impatient.cancel()
            return await patient, [server.connections for server in s.values()]

    result, counts = asyncio.run(main())
    assert by_id(result)[GATEWAY_ADMIN]["reason"] == "No answer." and counts == [1, 1, 1]


# -- settings and logging ------------------------------------------------------------------------------------------------


def test_settings_come_from_the_environment_with_the_usual_ports():
    settings = settings_from_env({"MOREOPENREPEATER_WINLINK_HOST": "winlink", "MOREOPENREPEATER_WINLINK_MODEM_HOST": "172.28.0.40"})
    assert (settings.gateway_host, settings.modem_host) == ("winlink", "172.28.0.40")
    assert (settings.admin_port, settings.telnet_port, settings.kiss_port) == (8080, 8010, 8001)
    assert settings_from_env({}).gateway_host is None and settings_from_env({}).modem_host is None
    custom = settings_from_env(
        {
            "MOREOPENREPEATER_WINLINK_ADMIN_PORT": "9080",
            "MOREOPENREPEATER_WINLINK_TELNET_PORT": "nope",
            "MOREOPENREPEATER_WINLINK_KISS_PORT": "70000",
        }
    )
    assert (custom.admin_port, custom.telnet_port, custom.kiss_port) == (9080, 8010, 8001)


def test_the_callsign_is_shown_only_when_it_looks_like_one():
    assert settings_from_env({"MOREOPENREPEATER_WINLINK_CALL": " w1aw "}).callsign == "W1AW"
    assert settings_from_env({"MOREOPENREPEATER_WINLINK_CALL": ""}).callsign is None
    assert settings_from_env({"MOREOPENREPEATER_WINLINK_CALL": "W1AW\nUSER=x"}).callsign is None


def test_a_change_is_logged_once_with_a_stable_name_and_never_when_it_was_never_on(structured_events):
    async def main():
        async with serving(admin="http-200", telnet="accept", kiss="accept") as s:
            now = [0.0]
            status = WinlinkStatus(settings_for(s["admin"], s["telnet"], None), clock=lambda: now[0])
            await status.status()  # up, the modem is down: logged
            now[0] += 20
            await status.status()  # the same picture: silent
            return status

    asyncio.run(main())
    events = structured_events.named("winlink.status_changed")
    assert len(events) == 1
    assert events[0].severity.name == "WARNING" and events[0].context["failing"] == MODEM_KISS

    structured_events.clear()
    asyncio.run(WinlinkStatus(settings_for()).status())  # never turned on
    assert structured_events.named("winlink.status_changed") == []


# -- the route -----------------------------------------------------------------------------------------------------------


def make_client(monkeypatch, settings: WinlinkSettings, auth: bool = False, clock=None) -> TestClient:
    """The real app, told where the services are the way compose tells it: through the environment."""
    for name in ("MOREOPENREPEATER_AUTH_USER", "MOREOPENREPEATER_AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MOREOPENREPEATER_WINLINK_HOST", settings.gateway_host or "")
    monkeypatch.setenv("MOREOPENREPEATER_WINLINK_MODEM_HOST", settings.modem_host or "")
    monkeypatch.setenv("MOREOPENREPEATER_WINLINK_ADMIN_PORT", str(settings.admin_port))
    monkeypatch.setenv("MOREOPENREPEATER_WINLINK_TELNET_PORT", str(settings.telnet_port))
    monkeypatch.setenv("MOREOPENREPEATER_WINLINK_KISS_PORT", str(settings.kiss_port))
    monkeypatch.setenv("MOREOPENREPEATER_WINLINK_CALL", settings.callsign or "")
    if clock is not None:
        app_module = importlib.import_module("api.app")
        monkeypatch.setattr(app_module, "WinlinkStatus", lambda s: WinlinkStatus(s, clock=clock))
    extra = {"auth_settings": AuthSettings(username="admin", password="hunter2")} if auth else {}
    app = create_app(
        service=RepeaterService(config=RepeaterConfig()),
        start_background_tick=False,
        log_path=Path(tempfile.mkdtemp()) / "test.log",
        **extra,
    )
    return TestClient(app)


def test_the_route_requires_a_login_like_its_sibling_routes(monkeypatch):
    client = make_client(monkeypatch, settings_for(), auth=True)
    assert client.get("/api/winlink").status_code == 401
    assert client.get("/api/allstar").status_code == 401
    assert client.get("/api/winlink", auth=("admin", "hunter2")).status_code == 200


def test_the_route_says_not_enabled_when_nothing_answers(monkeypatch):
    response = make_client(monkeypatch, settings_for()).get("/api/winlink")
    body = response.json()
    assert response.status_code == 200
    assert body["enabled"] is False and body["problems"] == [] and body["callsign"] is None
    assert [check["id"] for check in body["checks"]] == list(CHECK_IDS)
    assert set(body["checks"][0]) == {"id", "ok", "reason"}


def test_the_route_reports_what_answers_and_is_cached_between_reads(monkeypatch):
    holder: dict = {}

    async def start():
        holder["servers"] = {
            "admin": await Server("http").start(),
            "telnet": await Server("accept").start(),
            "kiss": await Server("accept").start(),
        }

    # The servers live on their own loop in a thread so the TestClient's own loops cannot starve them.
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(start(), loop).result(5)
    servers = holder["servers"]
    try:
        now = [0.0]
        client = make_client(
            monkeypatch,
            settings_for(servers["admin"], servers["telnet"], servers["kiss"], callsign="W1AW"),
            clock=lambda: now[0],
        )
        body = client.get("/api/winlink").json()
        assert body["enabled"] is True and body["callsign"] == "W1AW" and body["problems"] == []
        assert all(check["ok"] for check in body["checks"])
        client.get("/api/winlink")
        now[0] += 5.0
        client.get("/api/winlink")
        assert [server.connections for server in servers.values()] == [1, 1, 1]  # three reads, one set of probes
        now[0] += 20.0
        client.get("/api/winlink")
        assert [server.connections for server in servers.values()] == [2, 2, 2]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


def test_the_response_never_carries_a_password_or_an_address(monkeypatch):
    body = make_client(monkeypatch, settings_for(callsign="W1AW")).get("/api/winlink").text.lower()
    for word in ("password", "127.0.0.1", "secret"):
        assert word not in body
