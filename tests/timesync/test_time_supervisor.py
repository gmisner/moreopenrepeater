"""The time container's supervisor: the real script, with a fake chronyd and chronyc, against files in a temp folder.

What matters most is the value it writes into chrony's configuration. It must come out of the file only if it passes the same
check the dashboard applies, because the file is not trusted whoever wrote it."""
import importlib.util
import json
import os
import random
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from api import time_sync as api_time

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docker" / "chrony-supervisor.py"

spec = importlib.util.spec_from_file_location("chrony_supervisor", SCRIPT)
sup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sup)

SYNCED = "A29FC801,162.159.200.1,3,1700000000.123456789,-0.000012345,0.000001,0.000045,12.345,0.012,0.123,0.025,0.001,64.5,Normal\n"
UNSYNCED = "00000000,,0,0.000000000,0.000000000,0.000000000,0.000000000,0.000,0.000,0.000,0.000000000,0.000000000,0.0,Not synchronised\n"

FAKE_CHRONYD = """#!/bin/sh
# argv: -d -f <config>. Keeps a copy of the configuration it was started with and a line in the log, runs until told to stop.
n=$(ls "$FAKE_DIR" | grep -c '^conf-')
cp "$3" "$FAKE_DIR/conf-$n"
echo "start $n $*" >> "$FAKE_DIR/log"
if [ -e "$FAKE_DIR/die-once" ] && [ "$n" = 0 ]; then rm "$FAKE_DIR/die-once"; exit 3; fi
trap 'echo "stop $n" >> "$FAKE_DIR/log"; exit 0' TERM INT
while :; do sleep 0.05; done
"""
FAKE_CHRONYC = """#!/bin/sh
echo "chronyc $*" >> "$FAKE_DIR/log"
[ -e "$FAKE_DIR/tracking" ] || exit 1
cat "$FAKE_DIR/tracking"
"""


class Rig:
    """The folders the container has, a fake chronyd and chronyc, and the supervisor running as a real process."""

    def __init__(self, root: Path):
        self.root = root
        self.config = root / "time-config"
        self.status = root / "time-status"
        self.run = root / "run-chrony"
        self.fake = root / "fake"
        self.bin = root / "bin"
        for folder in (self.config, self.status, self.run, self.fake, self.bin):
            folder.mkdir()
        for name, body in (("chronyd", FAKE_CHRONYD), ("chronyc", FAKE_CHRONYC)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.process = None
        self.output = ""

    def set_server(self, text):
        """What the dashboard does (atomically), or any other way a file can come to hold something."""
        temp = self.config / "ntp.json.tmp"
        temp.write_text(text)
        os.replace(temp, self.config / "ntp.json")

    def env(self, **extra):
        return {
            "PATH": os.environ["PATH"], "FAKE_DIR": str(self.fake), "MOR_TIME_CONFIG": str(self.config), "MOR_TIME_STATUS": str(self.status),
            "MOR_CHRONY_RUN": str(self.run), "MOR_CHRONYD": str(self.bin / "chronyd"), "MOR_CHRONYC": str(self.bin / "chronyc"),
            "MOR_TIME_POLL_SECONDS": "0.05", "MOR_TIME_STATUS_SECONDS": "0.2", "MOR_TIME_RESTART_SECONDS": "0.1", **extra,
        }

    def start(self, **extra):
        self.process = subprocess.Popen([sys.executable, str(SCRIPT)], env=self.env(**extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return self.process

    def stop(self):
        if self.process is None or self.process.stdout is None or self.process.stdout.closed:
            return self.output
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.output = self.process.stdout.read()
        self.process.stdout.close()
        return self.output

    def confs(self):
        return [(self.fake / name).read_text() for name in sorted(os.listdir(self.fake)) if name.startswith("conf-")]

    def log(self):
        path = self.fake / "log"
        return path.read_text().splitlines() if path.exists() else []

    def status_json(self):
        try:
            return json.loads((self.status / "status.json").read_text())
        except (OSError, ValueError):
            return None

    def wait_for(self, condition, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = condition()
            if value:
                return value
            time.sleep(0.02)
        raise AssertionError(f"timed out; log={self.log()} status={self.status_json()}")

    def wait_starts(self, count):
        return self.wait_for(lambda: len(self.confs()) >= count)

    def lines(self, index=0):
        return self.confs()[index].splitlines()


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.stop()


# -- the configuration it renders --------------------------------------------------------------------------------------------------


def test_with_no_setting_it_starts_chronyd_on_the_default_pool_and_nothing_else_is_asked_of_it(rig):
    rig.start()
    rig.wait_starts(1)
    assert rig.lines() == [
        "# Made by chrony-supervisor.py from ntp.json each time that changes. Edits here are lost.",
        "pool pool.ntp.org iburst maxsources 4",
        "user root",
        "port 0",
        "cmdport 0",
        f"bindcmdaddress {rig.run}/chronyd.sock",
        f"pidfile {rig.run}/chronyd.pid",
        f"driftfile {rig.run}/drift",
        "makestep 1.0 3",
    ]
    starts = rig.wait_for(lambda: [line for line in rig.log() if line.startswith("start")])
    assert starts[0] == f"start 0 -d -f {rig.run}/chrony.conf"  # in the foreground, logging to the container's output, with this file


@pytest.mark.parametrize("server, line", [
    ("time.example.org", "server time.example.org iburst"),
    ("192.168.1.10", "server 192.168.1.10 iburst"),
    ("2001:db8::1", "server 2001:db8::1 iburst"),
    ("", "pool pool.ntp.org iburst maxsources 4"),
])
def test_the_servers_the_owner_picked_is_the_one_in_the_configuration(rig, server, line):
    rig.set_server(json.dumps({"server": server}))
    rig.start()
    rig.wait_starts(1)
    config = rig.lines()
    assert line in config and len([entry for entry in config if entry.startswith(("server ", "pool "))]) == 1
    for required in ("user root", "port 0", "cmdport 0", "makestep 1.0 3"):
        assert required in config


def test_the_configuration_is_a_client_only_with_no_clock_hardware_and_no_way_in_but_the_unix_socket(rig):
    rig.start()
    rig.wait_starts(1)
    config = "\n".join(rig.lines())
    assert "port 0" in config.splitlines() and "cmdport 0" in config.splitlines()  # no NTP server port, no command port
    for directive in ("allow", "deny", "rtcsync", "rtcfile", "rtcdevice", "hwtimestamp", "refclock", "broadcast", "bindacqaddress", "lock_all", "sched_priority", "bindaddress"):
        assert not any(line.startswith(directive) for line in config.splitlines()), directive
    assert "makestep 1.0 3" in config.splitlines()  # a Pi with no battery clock boots wrong: step it at once, three times at most
    run = str(rig.run)
    assert all(run in line for line in config.splitlines() if line.startswith(("bindcmdaddress", "pidfile", "driftfile")))  # all in the tmpfs


def test_the_unprivileged_privilege_drop_is_turned_off_in_the_configuration(rig):
    """chronyd has only CAP_SYS_TIME: it cannot drop to another user, and a failed attempt would stop it."""
    rig.start()
    rig.wait_starts(1)
    assert "user root" in rig.lines()


def test_render_config_gives_exactly_this_for_a_server():
    text = sup.render_config("time.example.org", Path("/run/chrony"))
    assert text == (
        "# Made by chrony-supervisor.py from ntp.json each time that changes. Edits here are lost.\n"
        "server time.example.org iburst\nuser root\nport 0\ncmdport 0\nbindcmdaddress /run/chrony/chronyd.sock\n"
        "pidfile /run/chrony/chronyd.pid\ndriftfile /run/chrony/drift\nmakestep 1.0 3\n"
    )


# -- never trusting the file ----------------------------------------------------------------------------------------------------------

EVIL = [
    "a b", "a;b", "a\nserver evil.example iburst", "pool.ntp.org\nuser evil", "pool.ntp.org\n", "-x", "--version", "$(id)", "`id`", "a#b", 'a"b', "a'b", "a\\b",
    "bücher.example", "ntp。example", "127.0.0.1", "0.0.0.0", "224.0.0.1", "::1", "ff02::1", "fe80::1%eth0", "a/b", "a:123", "1.2.3", "999.1.1.1",
    "pool..ntp.org", ".pool", "pool.", "x" * 64, "a" * 254, "a\x00b", " pool.ntp.org", "localhost", "a|b", "a&b", "a>b", "a<b", "a{b}", "a*b",
]


@pytest.mark.parametrize("evil", EVIL)
def test_a_value_that_is_not_a_host_or_an_address_never_reaches_the_configuration(rig, evil):
    rig.set_server(json.dumps({"server": evil}))
    rig.start()
    rig.wait_starts(1)
    config = rig.confs()[0]
    assert "pool pool.ntp.org iburst maxsources 4" in config and "evil" not in config and "server " not in config
    why = rig.wait_for(lambda: (rig.status_json() or {}).get("config_error"))
    assert isinstance(why, str) and evil not in why  # it says why, in words, without repeating the value


@pytest.mark.parametrize("content", ["", "not json", "[]", "null", '{"other": 1}', '{"server": 5}', '{"server": null}', '{"server": ["a"]}', "x" * 10000, "\xff\xfe", '{"server": "a b"}'])
def test_a_file_that_is_not_the_setting_is_the_default_and_says_so(rig, content):
    rig.set_server(content)
    rig.start()
    rig.wait_starts(1)
    assert "pool pool.ntp.org iburst maxsources 4" in rig.confs()[0]
    assert rig.wait_for(lambda: (rig.status_json() or {}).get("config_error"))
    assert rig.status_json()["server"] == "pool.ntp.org"


def test_a_file_that_is_not_text_is_the_default_and_says_so(rig):
    (rig.config / "ntp.json").write_bytes(b"\xff\xfe\x00\x01")
    rig.start()
    rig.wait_starts(1)
    assert "pool pool.ntp.org iburst maxsources 4" in rig.confs()[0]
    assert rig.wait_for(lambda: (rig.status_json() or {}).get("config_error")) == "ntp.json is not text"


def test_each_layer_checks_on_its_own_the_file_reader_and_the_renderer(tmp_path):
    path = tmp_path / "ntp.json"
    path.write_text(json.dumps({"server": "a b"}))
    with pytest.raises(sup.ConfigError):
        sup.read_setting(path)  # layer one: what is read from the file
    with pytest.raises(sup.ConfigError):
        sup.render_config("a b\nserver evil", Path("/run/chrony"))  # layer two: what is written, even if something skipped layer one
    for evil in EVIL:
        with pytest.raises(sup.ConfigError):
            sup.render_config(evil, Path("/run/chrony"))


def test_a_missing_file_is_the_default_without_complaint(tmp_path):
    assert sup.read_setting(tmp_path / "nope.json") == ""


def test_with_a_missing_file_there_is_no_error_in_the_status(rig):
    rig.start()
    rig.wait_starts(1)
    assert rig.wait_for(lambda: rig.status_json())["config_error"] is None


def test_the_supervisor_and_the_dashboard_agree_on_every_value():
    """The two copies of the rule must not drift apart: a value one accepts and the other refuses is a bug in one of them."""
    from test_time_setting import BAD, GOOD

    rng = random.Random(7)
    alphabet = "abcXYZ019.-:_ ;#$/%ü\n\"'`\\*"
    fuzz = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))) for _ in range(3000)]
    for value in [*GOOD, *[v for v, _ in BAD], *EVIL, *fuzz, "", "a" * 253, "a" * 254]:
        try:
            api_time.check_server(value)
            theirs = True
        except api_time.TimeServerError:
            theirs = False
        try:
            sup.check_server(value)
            ours = True
        except sup.ConfigError:
            ours = False
        assert ours == theirs, repr(value)


# -- changing the setting ------------------------------------------------------------------------------------------------------------


def test_a_new_server_restarts_chronyd_with_a_new_configuration(rig):
    rig.start()
    rig.wait_starts(1)
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.wait_starts(2)
    rig.wait_for(lambda: "stop 0" in rig.log())  # the old one was told to stop (SIGTERM), not left running beside the new
    assert "pool pool.ntp.org iburst maxsources 4" in rig.confs()[0] and "server time.example.org iburst" in rig.confs()[1]
    status = rig.wait_for(lambda: (rig.status_json() or {}).get("server") == "time.example.org" and rig.status_json())
    assert status["chronyd"] is True and status["config_error"] is None


def test_the_same_server_written_again_does_not_restart_it(rig):
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.start()
    rig.wait_starts(1)
    rig.set_server(json.dumps({"server": "time.example.org"}))
    time.sleep(0.5)
    assert len(rig.confs()) == 1 and not [line for line in rig.log() if line.startswith("stop")]


def test_a_bad_value_written_later_is_ignored_the_running_server_stays_and_the_status_says_why(rig):
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.start()
    rig.wait_starts(1)
    rig.set_server(json.dumps({"server": "a;b\nserver evil.example"}))
    status = rig.wait_for(lambda: (rig.status_json() or {}).get("config_error") and rig.status_json())
    time.sleep(0.3)
    assert len(rig.confs()) == 1 and status["server"] == "time.example.org" and status["chronyd"] is True
    rig.set_server(json.dumps({"server": "other.example.org"}))  # and a good one afterwards is used, and the complaint goes
    rig.wait_starts(2)
    assert "server other.example.org iburst" in rig.confs()[1] and not any("evil" in c for c in rig.confs())
    assert rig.wait_for(lambda: (rig.status_json() or {}).get("server") == "other.example.org" and rig.status_json())["config_error"] is None


def test_going_back_to_empty_goes_back_to_the_default_pool(rig):
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.start()
    rig.wait_starts(1)
    rig.set_server(json.dumps({"server": ""}))
    rig.wait_starts(2)
    assert "pool pool.ntp.org iburst maxsources 4" in rig.confs()[1]


def test_the_file_is_never_written_by_the_container(rig):
    rig.set_server(json.dumps({"server": "time.example.org"}))
    before = (rig.config / "ntp.json").read_bytes()
    rig.start()
    rig.wait_starts(1)
    rig.set_server(json.dumps({"server": "x\ny"}))
    time.sleep(0.4)
    rig.stop()
    assert os.listdir(rig.config) == ["ntp.json"] and (rig.config / "ntp.json").read_text() == json.dumps({"server": "x\ny"})
    assert before == json.dumps({"server": "time.example.org"}).encode()


# -- keeping chronyd running -----------------------------------------------------------------------------------------------------------


def test_chronyd_that_exits_is_started_again_with_the_same_configuration(rig):
    (rig.fake / "die-once").write_text("")
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.start()
    rig.wait_starts(2)
    assert rig.confs()[0] == rig.confs()[1]
    status = rig.wait_for(lambda: (rig.status_json() or {}).get("chronyd") is True and rig.status_json())
    assert status["server"] == "time.example.org"


def test_while_chronyd_is_down_the_status_says_so_at_once(rig):
    (rig.fake / "die-once").write_text("")
    rig.start(MOR_TIME_RESTART_SECONDS="1")
    rig.wait_for(lambda: (rig.status_json() or {}).get("chronyd") is False)  # then it comes back after the wait
    rig.wait_for(lambda: (rig.status_json() or {}).get("chronyd") is True)


def test_a_chronyd_that_cannot_start_ends_the_container_with_a_message(rig):
    process = rig.start(MOR_CHRONYD=str(rig.root / "no-such-chronyd"))
    assert process.wait(10) != 0
    assert "no-such-chronyd" in rig.stop()


def test_stopping_the_container_stops_chronyd_and_says_it_is_down(rig):
    rig.start()
    rig.wait_starts(1)
    rig.wait_for(lambda: (rig.status_json() or {}).get("chronyd") is True)
    rig.process.send_signal(signal.SIGTERM)
    assert rig.process.wait(8) == 0
    assert "stop 0" in rig.log() and rig.status_json()["chronyd"] is False


# -- the status it writes ---------------------------------------------------------------------------------------------------------------


def test_the_status_says_what_chrony_sees(rig):
    (rig.fake / "tracking").write_text(SYNCED)
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.start()
    status = rig.wait_for(lambda: (rig.status_json() or {}).get("synced") and rig.status_json())
    assert status["v"] == 1 and status["chronyd"] is True and status["server"] == "time.example.org"
    assert status["stratum"] == 3 and status["offset_ms"] == 0.012 and status["config_error"] is None
    assert abs(status["at"] - time.time()) < 10
    asked = [line for line in rig.log() if line.startswith("chronyc")]
    assert asked and asked[0] == f"chronyc -h {rig.run}/chronyd.sock -n -c tracking"  # over its Unix socket, no names looked up


def test_not_synchronised_yet_is_said_as_such(rig):
    (rig.fake / "tracking").write_text(UNSYNCED)
    rig.start()
    status = rig.wait_for(lambda: (rig.status_json() or {}).get("synced") is False and rig.status_json())
    assert status["chronyd"] is True and status["stratum"] is None and status["offset_ms"] is None


def test_when_chronyc_cannot_answer_it_says_nothing_about_sync_but_chronyd_is_still_up(rig):
    rig.start()
    status = rig.wait_for(lambda: rig.status_json())
    assert status["chronyd"] is True and status["synced"] is None and status["stratum"] is None


def test_the_status_is_rewritten_every_so_often_so_a_fresh_one_means_a_live_supervisor(rig):
    rig.start()
    first = rig.wait_for(lambda: rig.status_json())["at"]
    later = rig.wait_for(lambda: (rig.status_json() or {}).get("at", 0) > first + 0.3 and rig.status_json())
    assert later["at"] > first


def test_the_status_file_is_readable_by_the_dashboard_and_written_whole_each_time(rig):
    rig.start()
    rig.wait_for(lambda: rig.status_json())
    path = rig.status / "status.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # the API is another user and only reads it
    for _ in range(200):  # while it is being rewritten, a reader never sees a part of it
        assert isinstance(json.loads(path.read_text()), dict)
    time.sleep(0.5)
    assert os.listdir(rig.status) == ["status.json"]  # the temporary file it writes beside it is renamed over it, not left


def test_it_writes_only_its_own_two_places(rig):
    rig.set_server(json.dumps({"server": "time.example.org"}))
    rig.start()
    rig.wait_starts(1)
    rig.wait_for(lambda: rig.status_json())
    rig.stop()
    assert sorted(os.listdir(rig.config)) == ["ntp.json"]
    assert sorted(os.listdir(rig.status)) == ["status.json"]
    assert sorted(os.listdir(rig.run)) == ["chrony.conf"]
    assert sorted(set(os.listdir(rig.root)) - {"time-config", "time-status", "run-chrony", "fake", "bin", "test.log"}) == []


def test_files_are_written_whole_or_not_at_all(tmp_path, monkeypatch):
    target = tmp_path / "status.json"
    sup.write_atomic(target, "old\n")
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("gone")))
    with pytest.raises(OSError):
        sup.write_atomic(target, "new\n")
    assert target.read_text() == "old\n" and sorted(os.listdir(tmp_path)) == ["status.json"]  # the old one, and no temporary file left
    monkeypatch.undo()
    sup.write_atomic(target, "new\n")
    assert target.read_text() == "new\n" and stat.S_IMODE(target.stat().st_mode) == 0o644


# -- parsing chronyc -----------------------------------------------------------------------------------------------------------------


def test_parse_tracking_reads_a_real_line():
    assert sup.parse_tracking(SYNCED) == {"synced": True, "stratum": 3, "offset_ms": 0.012}
    assert sup.parse_tracking(UNSYNCED) == {"synced": False, "stratum": None, "offset_ms": None}


@pytest.mark.parametrize("junk", ["", "506 Cannot talk to daemon", "a,b", "x,y,z,w,v", "A29FC801,x,three,1,2,3,4,5,6,7,8,9,10,Normal", "\x00"])
def test_parse_tracking_gives_nothing_for_what_it_cannot_read(junk):
    assert sup.parse_tracking(junk) == {}


# -- the health check ------------------------------------------------------------------------------------------------------------------


def write_status(folder, **fields):
    data = {"v": 1, "at": time.time(), "chronyd": True}
    data.update(fields)
    (folder / "status.json").write_text(json.dumps(data))


def test_healthy_means_a_fresh_status_with_chronyd_up(tmp_path):
    write_status(tmp_path)
    assert sup.check(tmp_path) == 0
    write_status(tmp_path, synced=False)  # not reaching the server is not unhealthy: it is doing its job
    assert sup.check(tmp_path) == 0


@pytest.mark.parametrize("fields", [{"at": 0}, {"chronyd": False}, {"chronyd": None}, {"at": time.time() + 3600}])
def test_unhealthy_means_old_or_future_dated_or_chronyd_down(tmp_path, fields):
    write_status(tmp_path, **fields)
    assert sup.check(tmp_path) == 1


@pytest.mark.parametrize("content", [None, "", "not json", "[]", '{"chronyd": true}', '{"at": "now", "chronyd": true}'])
def test_unhealthy_without_a_readable_status(tmp_path, content):
    if content is not None:
        (tmp_path / "status.json").write_text(content)
    assert sup.check(tmp_path) == 1


def test_the_health_check_runs_from_the_command_line(rig):
    write_status(rig.status)
    done = subprocess.run([sys.executable, str(SCRIPT), "--check"], env=rig.env(), capture_output=True, text=True, timeout=20)
    assert done.returncode == 0
    (rig.status / "status.json").unlink()
    done = subprocess.run([sys.executable, str(SCRIPT), "--check"], env=rig.env(), capture_output=True, text=True, timeout=20)
    assert done.returncode == 1 and "no status yet" in done.stderr
