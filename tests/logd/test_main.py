"""Running the collector: settings, a damaged file, and the whole service started and stopped (#221, #229)."""
import asyncio
import json
import logging
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from logd import healthcheck
from logd.main import Settings, open_store, serve, settings_from_env


@pytest.fixture
def run_dir():
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_the_defaults_are_the_agreed_design():
    s = settings_from_env({})
    assert (s.max_bytes, s.routine_days, s.critical_days) == (64 * 1024 * 1024, 14, 30)
    assert str(s.events_socket) == "/run/logd/events.sock" and str(s.data_dir) == "/var/lib/logd"


def test_settings_come_from_the_environment():
    s = settings_from_env({
        "MOREOPENREPEATER_LOGD_SOCKET": "/x/e.sock", "MOREOPENREPEATER_LOGD_QUERY_SOCKET": "/x/q.sock",
        "MOREOPENREPEATER_LOGD_DIR": "/x/data", "MOREOPENREPEATER_LOGD_MAX_MB": "8",
        "MOREOPENREPEATER_LOGD_ROUTINE_DAYS": "3", "MOREOPENREPEATER_LOGD_CRITICAL_DAYS": "9",
    })
    assert (str(s.events_socket), str(s.query_socket), str(s.data_dir)) == ("/x/e.sock", "/x/q.sock", "/x/data")
    assert (s.max_bytes, s.routine_days, s.critical_days) == (8 * 1024 * 1024, 3, 9)


@pytest.mark.parametrize("name,value", [
    ("MOREOPENREPEATER_LOGD_MAX_MB", "lots"), ("MOREOPENREPEATER_LOGD_MAX_MB", "0"), ("MOREOPENREPEATER_LOGD_MAX_MB", "-5"),
    ("MOREOPENREPEATER_LOGD_ROUTINE_DAYS", "nan"), ("MOREOPENREPEATER_LOGD_ROUTINE_DAYS", "99999"),
])
def test_a_setting_that_makes_no_sense_means_the_default_and_a_warning(name, value, caplog):
    caplog.set_level(logging.WARNING, logger="moreopenrepeater.logd")
    s = settings_from_env({name: value})
    assert (s.max_bytes, s.routine_days) == (64 * 1024 * 1024, 14)
    assert name in caplog.text


def test_errors_are_never_kept_for_less_time_than_routine_events():
    s = settings_from_env({"MOREOPENREPEATER_LOGD_ROUTINE_DAYS": "20", "MOREOPENREPEATER_LOGD_CRITICAL_DAYS": "5"})
    assert (s.routine_days, s.critical_days) == (20, 20)


def settings_in(run_dir):
    return Settings(run_dir / "events.sock", run_dir / "query.sock", run_dir / "data", 8 * 1024 * 1024, 14, 30)


def test_a_damaged_event_file_is_set_aside_not_deleted_and_a_new_one_is_started(run_dir):
    s = settings_in(run_dir)
    s.data_dir.mkdir()
    (s.data_dir / "events.db").write_bytes(b"this is not a database" * 100)
    store = open_store(s)
    store.close()
    kept = [p.name for p in s.data_dir.iterdir() if p.name.startswith("events.db.damaged-")]
    assert len(kept) == 1 and (s.data_dir / "events.db").exists()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(s.data_dir / kept[0]).execute("select * from events").fetchall()  # untouched, still not a database


def test_the_whole_service_starts_takes_an_event_answers_and_stops_cleanly(run_dir):
    s = settings_in(run_dir)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(serve(s, stop))
        for _ in range(200):
            if s.query_socket.exists() and s.events_socket.exists():
                break
            await asyncio.sleep(0.02)
        event = {
            "schema_version": 1, "time": "2026-11-18T12:00:00Z", "boot_id": "b0000000000000000000000000000001", "seq": 0,
            "severity": 20, "event": "test.hello", "module": "core", "module_version": "0.1.0", "message": "hello",
        }
        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sender.sendto(json.dumps(event).encode(), str(s.events_socket))
        sender.close()
        reader = writer = None
        for _ in range(200):  # the batch is written within the flush interval
            reader, writer = await asyncio.open_unix_connection(str(s.query_socket))
            writer.write(b"GET /events?q=hello HTTP/1.1\r\nHost: logd\r\n\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            body = json.loads(data.partition(b"\r\n\r\n")[2])
            if body["events"]:
                break
            await asyncio.sleep(0.1)
        healthy = await asyncio.get_running_loop().run_in_executor(None, healthcheck.check, s.query_socket)
        stop.set()
        await asyncio.wait_for(task, 10)
        return body, healthy

    body, healthy = asyncio.run(go())
    assert body["events"][0]["message"] == "hello" and healthy[0] is True
    assert not s.events_socket.exists() and not s.query_socket.exists()  # it cleaned up
    assert (s.data_dir / "events.db").exists()


def test_a_collector_that_is_not_there_is_unhealthy(run_dir):
    healthy, reason = healthcheck.check(run_dir / "nothing.sock")
    assert not healthy and "cannot reach" in reason


def test_the_health_check_imports_nothing_heavy():
    code = (
        "import sys, logd.healthcheck; "
        "heavy = {'asyncio', 'contracts', 'pydantic', 'sqlite3', 'logd.store', 'logd.server'} & set(sys.modules); "
        "print(sorted(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).parents[2] / "src"))
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


# -- the command line query -------------------------------------------------------------------------------


def test_the_query_command_reads_the_collector_and_prints_oldest_first(run_dir, capsys):
    import io
    from contextlib import redirect_stdout

    from logd import query

    s = settings_in(run_dir)

    def event(seq, message, severity=20, name="test.line", module="core"):
        return {
            "schema_version": 1, "time": f"2026-11-18T12:00:0{seq}Z", "boot_id": "b0000000000000000000000000000001", "seq": seq,
            "severity": severity, "event": name, "module": module, "module_version": "0.1.0", "message": message,
            "context": {"n": seq},
        }

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(serve(s, stop))
        for _ in range(200):
            if s.query_socket.exists() and s.events_socket.exists():
                break
            await asyncio.sleep(0.02)
        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        for e in (event(0, "first"), event(1, "second", severity=40, name="audio.glitch"), event(3, "third")):  # seq 2 never arrives
            sender.sendto(json.dumps(e).encode(), str(s.events_socket))
        sender.close()
        loop = asyncio.get_running_loop()
        env = {"MOREOPENREPEATER_LOGD_QUERY_SOCKET": str(s.query_socket)}
        out = {}
        for _ in range(100):
            await asyncio.sleep(0.1)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = await loop.run_in_executor(None, lambda: query.main(["--limit", "10"], env))
            out["all"] = buffer.getvalue()
            if out["all"].count("\n") >= 3:
                break
        for name, argv in (("errors", ["--min-severity", "error"]), ("prefix", ["--event", "audio.*"]), ("gaps", ["--gaps"]),
                           ("json", ["-q", "second", "--json"]), ("stats", ["--stats"])):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                await loop.run_in_executor(None, lambda argv=argv: query.main(argv, env))
            out[name] = buffer.getvalue()
        stop.set()
        await asyncio.wait_for(task, 10)
        return code, out

    code, out = asyncio.run(go())
    lines = out["all"].strip().splitlines()
    assert code == 0 and [l.split()[3] for l in lines] == ["test.line", "audio.glitch", "test.line"]  # oldest first
    assert "ERROR" in lines[1] and "n=1" in lines[1] and "second" in lines[1]
    assert out["errors"].strip().count("\n") == 0 and "second" in out["errors"] and "audio.glitch" in out["prefix"]
    assert "sequence_gap" in out["gaps"] and json.loads(out["json"])["events"][0]["message"] == "second"
    assert json.loads(out["stats"])["events"] == 3


def test_the_query_command_says_so_when_there_is_no_collector(run_dir, capsys):
    from logd import query

    code = query.main([], {"MOREOPENREPEATER_LOGD_QUERY_SOCKET": str(run_dir / "nothing.sock")})
    assert code == 1 and "cannot reach the collector" in capsys.readouterr().err


def test_the_query_command_imports_nothing_heavy():
    code = "import sys, logd.query; print(sorted({'asyncio', 'contracts', 'pydantic', 'sqlite3'} & set(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).parents[2] / "src"))
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_the_query_command_shows_whole_and_fractional_seconds_alike():
    from logd.query import line

    base = {"severity": 40, "module": "core", "event": "x.y", "message": "m", "context": {}}
    assert line({**base, "time": "2026-11-19T11:40:00+00:00"}).startswith("2026-11-19T11:40:00Z ")
    assert line({**base, "time": "2026-11-19T11:40:00.123456+00:00"}).startswith("2026-11-19T11:40:00.123Z ")
