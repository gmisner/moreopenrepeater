"""The DMR container's supervisor (#221, #229): three programs kept running, the dashboard's request file honored, and a
configuration the container seeds and then leaves alone."""
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "docker" / "dmr-supervisor.py"
SEEDS = ROOT / "packaging" / "dmr"

spec = importlib.util.spec_from_file_location("dmr_supervisor", SUPERVISOR)
dmr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dmr)

# -- the .ini files -------------------------------------------------------------------------------------------------


def test_set_ini_value_changes_one_line_in_one_section_and_nothing_else():
    text = "[A]\nkey = 1 ; one\n[B]\nkey = 2\nother=3\n"
    changed = dmr.set_ini_value(text, "B", "key", "9")
    assert changed == "[A]\nkey = 1 ; one\n[B]\nkey = 9\nother=3\n"  # the same key in [A] is untouched


def test_set_ini_value_keeps_the_spacing_of_the_line_and_its_line_ending():
    assert dmr.set_ini_value("[S]\r\nFileLevel=2\r\n", "S", "FileLevel", "0") == "[S]\r\nFileLevel=0\r\n"
    assert dmr.set_ini_value("[S]\nkey   =   x", "S", "key", "y") == "[S]\nkey   =   y"  # no trailing newline


def test_a_seed_that_no_longer_matches_the_program_is_an_error_not_a_silent_no_op():
    with pytest.raises(ValueError, match="no nope in \\[S\\]"):
        dmr.set_ini_value("[S]\nkey=1\n", "S", "nope", "1")
    with pytest.raises(ValueError):
        dmr.set_ini_value("[T]\nkey=1\n", "S", "key", "1")


@pytest.fixture
def config(tmp_path):
    return tmp_path / "config"


def test_the_first_start_writes_the_projects_ini_files_with_the_containers_addresses(config):
    written = dmr.seed_config(config, SEEDS, core_address="172.30.0.10")
    assert written == ["Analog_Bridge.ini", "MMDVM_Bridge.ini", "DVSwitch.ini"]
    analog = (config / "Analog_Bridge.ini").read_text()
    usrp = analog.split("[USRP]")[1].split("[")[0]
    assert "address = 172.30.0.10" in usrp  # the core, which is not on the loopback here
    assert "emulatorAddress = 127.0.0.1:2470" in analog  # the emulator is in the same container: the loopback is right
    assert "address = 127.0.0.1" in analog.split("[AMBE_AUDIO]")[1].split("[")[0]  # only [USRP] changed
    mmdvm = (config / "MMDVM_Bridge.ini").read_text()
    assert "FileLevel=0" in mmdvm and "FilePath=/tmp" in mmdvm  # no log file on the disk


def test_the_seeded_files_are_readable_by_the_owner_only_because_the_network_password_goes_in_them(config):
    dmr.seed_config(config, SEEDS)
    for name in ("Analog_Bridge.ini", "MMDVM_Bridge.ini", "DVSwitch.ini"):
        assert (config / name).stat().st_mode & 0o777 == 0o600


def test_a_file_that_is_already_there_is_never_touched_so_the_owners_and_dashboards_changes_survive(config):
    config.mkdir()
    mine = "[General]\nCallsign=W1AW\n; my own\n"
    (config / "MMDVM_Bridge.ini").write_text(mine)
    assert dmr.seed_config(config, SEEDS) == ["Analog_Bridge.ini", "DVSwitch.ini"]
    assert (config / "MMDVM_Bridge.ini").read_text() == mine
    assert dmr.seed_config(config, SEEDS) == []  # and a second start writes nothing


def test_the_macro_file_is_linked_from_the_package_once(config, tmp_path):
    macro = tmp_path / "dvsm.macro"
    macro.write_text("macro")
    dmr.seed_config(config, SEEDS, macro=macro)
    assert (config / "dvsm.macro").is_symlink() and (config / "dvsm.macro").read_text() == "macro"
    dmr.seed_config(config, SEEDS, macro=macro)  # again: no error
    dmr.seed_config(tmp_path / "other", SEEDS, macro=tmp_path / "missing")  # no macro in the package: no link, no error
    assert not (tmp_path / "other" / "dvsm.macro").exists()


@pytest.mark.parametrize("address", ["not-an-ip", "172.28.0.10; echo", "172.28.0.10\n[X]", "", "172.28.0", "999.1.1.1"])
def test_a_core_address_that_is_not_an_ip_is_refused_before_it_is_written_into_a_config_file(config, address):
    with pytest.raises(ValueError, match="MOR_CORE_ADDRESS"):
        dmr.seed_config(config, SEEDS, core_address=address)
    assert not config.exists() or not list(config.iterdir())


# -- what the dashboard counts as configured ----------------------------------------------------------------------


def test_a_fresh_seed_is_missing_everything_the_dashboard_asks_for(config):
    dmr.seed_config(config, SEEDS)
    assert dmr.missing_settings(config) == ["callsign", "DMR Id", "network address", "network password"]


def set_mmdvm(config, **values):
    path = config / "MMDVM_Bridge.ini"
    text = path.read_text()
    for (section, key), value in {("General", "Callsign"): "W1AW", ("General", "Id"): "1234567", **{
        ("DMR Network", "Address"): "master.example.net", ("DMR Network", "Password"): "secret"}}.items():
        text = dmr.set_ini_value(text, section, key, values.get(key, value))
    path.write_text(text)


def test_settings_that_are_complete_are_complete(config):
    dmr.seed_config(config, SEEDS)
    set_mmdvm(config)
    assert dmr.missing_settings(config) == []


@pytest.mark.parametrize(
    "key, value, lacking",
    [("Callsign", "N0CALL", "callsign"), ("Callsign", "  ", "callsign"), ("Id", "0", "DMR Id"), ("Id", "0000000", "DMR Id"),
     ("Address", "", "network address"), ("Password", " ", "network password")],
)
def test_each_missing_piece_is_named(config, key, value, lacking):
    dmr.seed_config(config, SEEDS)
    set_mmdvm(config, **{key: value})
    assert dmr.missing_settings(config) == [lacking]


def test_a_missing_or_broken_file_is_not_complete_and_does_not_raise(config):
    assert dmr.missing_settings(config) == ["MMDVM_Bridge.ini"]
    config.mkdir()
    (config / "MMDVM_Bridge.ini").write_text("not an ini file at all\n")
    assert dmr.missing_settings(config) == ["MMDVM_Bridge.ini"]


# -- the programs ---------------------------------------------------------------------------------------------------

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def programs(**argv):
    return {name: {"argv": argv.get(name, SLEEP)} for name in ("md380_emu", "analog_bridge", "mmdvm_bridge")}


@pytest.fixture
def sup(tmp_path):
    made = []

    def make(missing=lambda: [], progs=None, **kwargs):
        kwargs.setdefault("restart_delay", 0.05)
        kwargs.setdefault("stop_timeout", 3)
        s = dmr.Supervisor(progs or programs(), control_dir=tmp_path / "control", run_dir=tmp_path / "run", missing=missing,
                           out=io.StringIO(), **kwargs)
        made.append(s)
        return s

    yield make
    for s in made:
        s.stopping = True
        s.stop_all()


def status(s):
    return json.loads(s.status_path.read_text())


def test_it_starts_all_three_and_says_so(sup):
    s = sup()
    s.start_all()
    assert all(s.running().values())
    for name in ("md380_emu", "analog_bridge", "mmdvm_bridge"):
        assert f"started {name}" in s._out.getvalue()


def test_what_a_program_says_goes_to_the_log_with_its_name(sup):
    say = [sys.executable, "-u", "-c", "print('hello from the bridge'); import time; time.sleep(30)"]
    s = sup(progs=programs(analog_bridge=say))
    s.start_all()
    deadline = time.time() + 5
    while "hello from the bridge" not in s._out.getvalue() and time.time() < deadline:
        time.sleep(0.05)
    assert "[analog_bridge] hello from the bridge" in s._out.getvalue()


def test_nothing_is_started_until_the_settings_are_complete_and_what_is_missing_is_said_once(sup):
    lacking = ["callsign", "network password"]
    s = sup(missing=lambda: lacking)
    s.start_all()
    s.tick()
    s.tick()
    assert not any(s.running().values()) and s.waiting == lacking
    assert s._out.getvalue().count("waiting for the DMR settings: callsign, network password") == 1  # not on every tick


def test_the_programs_start_by_themselves_when_the_settings_become_complete(sup):
    lacking = ["callsign"]
    s = sup(missing=lambda: lacking)
    s.start_all()
    assert not any(s.running().values())
    lacking.clear()  # the dashboard saved them; no restart request needed
    s.tick()
    assert all(s.running().values()) and s.waiting == []
    assert "the DMR settings are complete" in s._out.getvalue()


def test_a_program_that_dies_is_started_again_after_the_delay(sup):
    s = sup(restart_delay=0.2)
    s.start_all()
    old = s._children["mmdvm_bridge"]
    old.kill()
    old.wait()
    s.tick()  # sees it exited
    assert not s.running()["mmdvm_bridge"] and "mmdvm_bridge exited" in s._out.getvalue()
    time.sleep(0.3)
    s.tick()
    assert s.running()["mmdvm_bridge"] and s._children["mmdvm_bridge"] is not old


def test_a_program_that_keeps_dying_waits_longer_each_time_up_to_a_minute(sup):
    s = sup(restart_delay=3)
    s._failures["analog_bridge"] = 1
    assert s._delay("analog_bridge") == 3
    s._failures["analog_bridge"] = 2
    assert s._delay("analog_bridge") == 6
    s._failures["analog_bridge"] = 4
    assert s._delay("analog_bridge") == 24
    s._failures["analog_bridge"] = 30
    assert s._delay("analog_bridge") == dmr.MAX_RESTART_DELAY


def test_a_program_that_ran_for_a_while_before_it_died_starts_the_count_again(sup):
    clock = {"now": 1000.0}
    s = sup(clock=lambda: clock["now"])
    s.start_all()
    clock["now"] += 5
    s._note_exit.__func__  # the method exists
    child = s._children["mmdvm_bridge"]
    child.kill(); child.wait()
    s._note_exit("mmdvm_bridge", -9, clock["now"])
    assert s._failures["mmdvm_bridge"] == 1  # died after 5 s: a failure
    s.start("mmdvm_bridge")
    clock["now"] += dmr.QUICK_EXIT_SECONDS + 1
    child = s._children["mmdvm_bridge"]
    child.kill(); child.wait()
    s._note_exit("mmdvm_bridge", -9, clock["now"])
    assert s._failures["mmdvm_bridge"] == 0  # it had run for more than 30 s


@pytest.mark.parametrize("code", [252, 253, 254])
def test_analog_bridge_is_not_started_again_after_a_configuration_error_as_its_unit_does_not(sup, code):
    exits = [sys.executable, "-c", f"raise SystemExit({code})"]
    s = sup(progs=programs(analog_bridge=exits))
    s.start_all()
    time.sleep(0.5)
    for _ in range(4):
        s.tick()
        time.sleep(0.1)
    assert "analog_bridge" in s._gave_up and not s.running()["analog_bridge"]
    assert s._out.getvalue().count("started analog_bridge") == 1  # never restarted
    assert "configuration error" in s._out.getvalue()


def test_the_other_programs_and_other_exit_codes_are_restarted_as_before(sup):
    exits = [sys.executable, "-c", "raise SystemExit(254)"]
    s = sup(progs=programs(mmdvm_bridge=exits), restart_delay=0.05)  # 254 means "fix the settings" only for Analog_Bridge
    s.start_all()
    time.sleep(0.4)
    for _ in range(3):
        s.tick()
        time.sleep(0.1)
    assert "mmdvm_bridge" not in s._gave_up and s._out.getvalue().count("started mmdvm_bridge") >= 2


# -- the dashboard's requests -------------------------------------------------------------------------------------


def ask(s, text):
    s.control_dir.mkdir(parents=True, exist_ok=True)
    s.request_path.write_text(text)
    return s.handle_request()


def test_restart_stops_and_starts_everything_with_the_current_settings(sup):
    s = sup()
    s.start_all()
    pids = {n: c.pid for n, c in s._children.items()}
    assert ask(s, "restart\n") == "restart"
    assert all(s.running().values()) and {n: c.pid for n, c in s._children.items()} != pids
    assert status(s)["state"] == "succeeded" and status(s)["action"] == "restart" and "finished_at" in status(s)
    assert not s.request_path.exists()  # the helper removes it once read


def test_restart_when_the_settings_are_incomplete_says_what_is_missing(sup):
    s = sup(missing=lambda: ["DMR Id"])
    ask(s, "restart")
    assert status(s)["state"] == "failed" and "DMR Id" in status(s)["message"]
    assert not any(s.running().values())


def test_stop_stops_everything_and_nothing_brings_it_back_until_asked(sup):
    s = sup(restart_delay=0.01)
    s.start_all()
    ask(s, "stop")
    assert not any(s.running().values()) and s.stopped and status(s)["state"] == "succeeded"
    for _ in range(3):
        s.tick()
        time.sleep(0.05)
    assert not any(s.running().values())
    ask(s, "restart")
    assert all(s.running().values()) and not s.stopped


def test_install_says_there_is_nothing_to_install_because_it_is_in_the_image(sup):
    s = sup()
    ask(s, "install")
    assert status(s)["state"] == "failed" and "built into the DMR container" in status(s)["message"]


@pytest.mark.parametrize("junk", ["", "reboot", "make-me-a-sandwich", "restarting", "\x00\x01"])
def test_a_request_that_means_nothing_is_removed_and_does_nothing(sup, junk):
    s = sup()
    s.start_all()
    pids = {n: c.pid for n, c in s._children.items()}
    assert ask(s, junk) is None
    assert not s.request_path.exists() and {n: c.pid for n, c in s._children.items()} == pids  # nothing restarted
    assert not s.status_path.exists()


def test_only_the_first_line_of_the_request_is_read_and_case_is_ignored(sup):
    s = sup()
    assert ask(s, "  STOP  \nrestart\n") == "stop"


def test_a_request_that_is_a_symlink_is_removed_and_not_followed(sup, tmp_path):
    s = sup()
    target = tmp_path / "secret.txt"
    target.write_text("restart")
    s.control_dir.mkdir(parents=True)
    s.request_path.symlink_to(target)
    assert s.handle_request() is None
    assert not s.request_path.is_symlink() and target.read_text() == "restart"  # the target was not read, changed or removed


def test_the_status_matches_what_the_dashboards_reader_expects(sup):
    """src/api/dmr_restart.py reads {state, action, started_at, message, finished_at} from status.json."""
    s = sup()
    ask(s, "stop")
    got = status(s)
    assert set(got) == {"state", "action", "started_at", "message", "finished_at"}
    assert got["started_at"] <= got["finished_at"] and isinstance(got["started_at"], float)


def test_a_status_that_is_still_running_has_no_finished_time(sup):
    s = sup()
    s.write_status("running", "restart", 1.0, "working")
    assert "finished_at" not in status(s)


def test_the_status_file_is_replaced_in_one_step(sup):
    s = sup()
    ask(s, "stop")
    assert not list(s.control_dir.glob("*.new"))


# -- health ---------------------------------------------------------------------------------------------------------


def test_health_says_when_it_is_running_and_which_program_is_not(sup, tmp_path):
    s = sup()
    s.start_all()
    s.write_health()
    assert dmr.check(tmp_path / "run") == (True, "running")
    s._children["md380_emu"].kill()
    s._children["md380_emu"].wait()
    s.write_health()
    assert dmr.check(tmp_path / "run") == (False, "not running: md380_emu")


def test_health_needs_a_fresh_heartbeat_from_the_supervisor(sup, tmp_path):
    s = sup()
    s.start_all()
    s.write_health()
    assert dmr.check(tmp_path / "run", clock=lambda: time.time() + 60)[0] is False  # stale: the supervisor itself is stuck
    assert dmr.check(tmp_path / "nothing") == (False, "no heartbeat from the supervisor")


def test_a_bridge_stopped_on_request_or_waiting_for_settings_is_not_a_fault(sup, tmp_path):
    s = sup(missing=lambda: ["callsign"])
    s.start_all()
    s.write_health()
    healthy, message = dmr.check(tmp_path / "run")
    assert healthy and "waiting for the DMR settings: callsign" in message
    s2 = sup()
    ask(s2, "stop")
    s2.write_health()
    assert dmr.check(tmp_path / "run") == (True, "stopped by request")


def test_the_heartbeat_is_written_by_the_tick(sup, tmp_path):
    s = sup()
    s.tick()
    assert (tmp_path / "run" / "health.json").exists()


# -- the whole process ----------------------------------------------------------------------------------------------


def test_sigterm_stops_the_programs_and_exits_cleanly_so_docker_stop_is_quick(tmp_path):
    driver = textwrap.dedent(f"""
        import importlib.util, sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("s", {str(SUPERVISOR)!r}); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
        progs = {{n: {{"argv": sleep}} for n in ("md380_emu", "analog_bridge", "mmdvm_bridge")}}
        sys.exit(m.Supervisor(progs, control_dir=Path({str(tmp_path / 'c')!r}), run_dir=Path({str(tmp_path / 'r')!r})).run())
    """)
    process = subprocess.Popen([sys.executable, "-u", "-c", driver], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    deadline = time.time() + 10
    while len([line for line in lines if "started" in line]) < 3 and time.time() < deadline:
        lines.append(process.stdout.readline())
    pids = [int(line.split("pid ")[1].rstrip(")\n")) for line in lines if "started" in line]
    assert len(pids) == 3
    started = time.time()
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=10) == 0
    assert time.time() - started < 5
    time.sleep(0.2)
    for pid in pids:  # none of the programs outlives it
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_the_default_programs_are_started_as_the_units_do_with_the_owners_ini_files():
    programs = dmr.default_programs(Path("/etc/moreopenrepeater/dmr"))
    assert programs["md380_emu"]["argv"] == ["/opt/md380-emu/qemu-arm-static", "/opt/md380-emu/md380-emu", "-S", "2470"]
    assert programs["analog_bridge"]["argv"] == ["/opt/Analog_Bridge/Analog_Bridge", "/etc/moreopenrepeater/dmr/Analog_Bridge.ini"]
    assert programs["mmdvm_bridge"]["argv"] == ["/opt/MMDVM_Bridge/MMDVM_Bridge", "/etc/moreopenrepeater/dmr/MMDVM_Bridge.ini"]
    assert programs["mmdvm_bridge"]["env"] == {"DVSWITCH": "/etc/moreopenrepeater/dmr/DVSwitch.ini"}
    assert programs["analog_bridge"]["env"]["AnalogBridgeLogDir"].startswith("/tmp")  # a tmpfs: no log files on the disk


def test_the_unit_files_in_the_repo_agree_with_the_programs_started_here():
    """packaging/dmr/*.service.d.conf is how the native install starts them; the container must start the same ones."""
    analog = (ROOT / "packaging" / "dmr" / "analog_bridge.service.d.conf").read_text()
    mmdvm = (ROOT / "packaging" / "dmr" / "mmdvm_bridge.service.d.conf").read_text()
    assert "/opt/Analog_Bridge/Analog_Bridge /etc/moreopenrepeater/dmr/Analog_Bridge.ini" in analog
    assert "/opt/MMDVM_Bridge/MMDVM_Bridge /etc/moreopenrepeater/dmr/MMDVM_Bridge.ini" in mmdvm
    assert "DVSWITCH=/etc/moreopenrepeater/dmr/DVSwitch.ini" in mmdvm


def test_main_check_flag_and_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(dmr, "RUN_DIR", tmp_path)
    assert dmr.main(["--check"], env={}) == 1  # no heartbeat
    assert dmr.main(["--bogus"], env={}) == 2


def test_an_empty_value_is_filled_without_swallowing_the_line_ending():
    """`Address=` is how the seed leaves the network's address. The line ending must survive, or the next line is glued on."""
    text = "[DMR Network]\nAddress=\nPort=62031\n"
    assert dmr.set_ini_value(text, "DMR Network", "Address", "master.example.net") == "[DMR Network]\nAddress=master.example.net\nPort=62031\n"
    assert dmr.set_ini_value("[S]\r\nkey=\r\nnext=1\r\n", "S", "key", "v") == "[S]\r\nkey=v\r\nnext=1\r\n"


# -- configuring it from the command line -------------------------------------------------------------------------

GOOD = dict(callsign="w1aw", dmr_id="1234567", network="brandmeister", password="pa55word")


def values(config, name, section):
    import configparser
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    text = (config / name).read_text()
    parser.read_string(text[text.index("["):])  # Analog_Bridge.ini opens with an `include` line that is not INI
    return dict(parser[section])


def test_configure_writes_the_same_keys_the_dashboard_writes(config):
    dmr.seed_config(config, SEEDS, core_address="172.28.0.10")
    dmr.configure(config, **GOOD, talkgroup=91, slot=1, color_code=2)
    assert dmr.missing_settings(config) == []
    mmdvm = {s: values(config, "MMDVM_Bridge.ini", s) for s in ("General", "DMR", "DMR Network")}
    assert mmdvm["General"]["callsign"] == "W1AW" and mmdvm["General"]["id"] == "123456701" and mmdvm["General"]["duplex"] == "0"
    assert mmdvm["DMR Network"]["address"] == "3102.repeater.net" and mmdvm["DMR Network"]["port"] == "62031"
    assert mmdvm["DMR Network"]["password"] == "pa55word" and mmdvm["DMR Network"]["slot1"] == "1" and mmdvm["DMR Network"]["slot2"] == "0"
    assert mmdvm["DMR"]["colorcode"] == "2"
    analog = values(config, "Analog_Bridge.ini", "AMBE_AUDIO")
    assert analog["gatewaydmrid"] == "1234567" and analog["repeaterid"] == "123456701" and analog["txtg"] == "91" and analog["txts"] == "1"
    assert values(config, "DVSwitch.ini", "DMR")["slot"] == "1"


def test_configure_leaves_the_containers_own_core_address_alone(config):
    dmr.seed_config(config, SEEDS, core_address="172.28.0.10")
    dmr.configure(config, **GOOD)
    assert "address = 172.28.0.10" in (config / "Analog_Bridge.ini").read_text().split("[USRP]")[1]


def test_configure_changes_only_the_lines_it_means_to(config):
    dmr.seed_config(config, SEEDS)
    before = (config / "MMDVM_Bridge.ini").read_text().splitlines()
    dmr.configure(config, **GOOD)
    after = (config / "MMDVM_Bridge.ini").read_text().splitlines()
    assert len(before) == len(after)
    changed = {line.split("=")[0] for line, other in zip(before, after) if line != other}
    assert changed <= {"Callsign", "Id", "Address", "Password", "Enable", "ColorCode", "Slot1", "Slot2", "Duplex", "Port"}


def test_a_custom_network_needs_its_master(config):
    dmr.seed_config(config, SEEDS)
    with pytest.raises(dmr.ConfigureError, match="custom network"):
        dmr.configure(config, **{**GOOD, "network": "custom"})
    dmr.configure(config, **{**GOOD, "network": "custom"}, host="master.example.net", port=62040)
    assert values(config, "MMDVM_Bridge.ini", "DMR Network")["address"] == "master.example.net"


@pytest.mark.parametrize(
    "change",
    [{"callsign": "W1AW; rm"}, {"callsign": "W1"}, {"callsign": "W1AW\n[Evil]"}, {"dmr_id": "12345"}, {"dmr_id": "12345678"}, {"dmr_id": "abcdefg"},
     {"network": "evil"}, {"password": ""}, {"password": "line\nbreak"}, {"password": "x" * 200}, {"password": "tab\there"},
     {"port": 0}, {"port": 70000}, {"port": "abc"}, {"slot": 3}, {"color_code": 16}, {"talkgroup": -1}, {"ssid": "1"}, {"ssid": "ab"}],
)
def test_an_invalid_value_is_refused_before_anything_is_written(config, change):
    dmr.seed_config(config, SEEDS)
    before = {n: (config / n).read_text() for n in ("Analog_Bridge.ini", "MMDVM_Bridge.ini", "DVSwitch.ini")}
    with pytest.raises(dmr.ConfigureError):
        dmr.configure(config, **{**GOOD, **change})
    assert {n: (config / n).read_text() for n in before} == before  # not half written


@pytest.mark.parametrize("host", ["bad host", "host;x", "host\n[X]", "a" * 300, "-bad", "bad-"])
def test_a_host_name_that_could_break_the_file_is_refused(config, host):
    dmr.seed_config(config, SEEDS)
    with pytest.raises(dmr.ConfigureError):
        dmr.configure(config, **{**GOOD, "network": "custom"}, host=host)


def test_configure_needs_the_files_the_container_writes_on_its_first_start(tmp_path):
    with pytest.raises(dmr.ConfigureError, match="start the container once"):
        dmr.configure(tmp_path / "nothing", **GOOD)


def test_configure_keeps_the_files_private(config):
    dmr.seed_config(config, SEEDS)
    dmr.configure(config, **GOOD)
    for name in ("Analog_Bridge.ini", "MMDVM_Bridge.ini", "DVSwitch.ini"):
        assert (config / name).stat().st_mode & 0o777 == 0o600
    assert not list(config.glob("*.new"))


def test_the_command_takes_the_password_from_the_environment_never_an_argument(config, tmp_path, monkeypatch, capsys):
    dmr.seed_config(config, SEEDS)
    monkeypatch.setattr(dmr, "CONFIG_DIR", config)
    monkeypatch.setattr(dmr, "CONTROL_DIR", tmp_path / "control")
    (tmp_path / "control").mkdir()
    args = ["--configure", "--callsign", "W1AW", "--dmr-id", "1234567", "--network", "tgif"]
    assert dmr.main(args, env={}) == 2  # no password
    assert "DMR_PASSWORD" in capsys.readouterr().err
    assert dmr.main(args, env={"DMR_PASSWORD": "from-the-environment"}) == 0
    out = capsys.readouterr()
    assert "from-the-environment" not in out.out + out.err  # never echoed
    assert (tmp_path / "control" / "request").read_text() == "restart\n"  # and the running bridge is asked to restart
    assert values(config, "MMDVM_Bridge.ini", "DMR Network")["password"] == "from-the-environment"
    assert dmr.missing_settings(config) == []


def test_the_command_refuses_a_password_argument(capsys):
    with pytest.raises(SystemExit):
        dmr.main(["--configure", "--callsign", "W1AW", "--dmr-id", "1234567", "--network", "tgif", "--password", "x"], env={})


def test_the_command_reports_an_invalid_value_and_changes_nothing(config, tmp_path, monkeypatch, capsys):
    dmr.seed_config(config, SEEDS)
    monkeypatch.setattr(dmr, "CONFIG_DIR", config)
    monkeypatch.setattr(dmr, "CONTROL_DIR", tmp_path / "control")
    assert dmr.main(["--configure", "--callsign", "W1AW", "--dmr-id", "12", "--network", "tgif"], env={"DMR_PASSWORD": "x"}) == 2
    assert "7 digits" in capsys.readouterr().err
    assert dmr.missing_settings(config)  # still unconfigured
