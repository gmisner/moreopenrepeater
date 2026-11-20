"""setup.sh's flags for hardware the core is only given when asked: --gpio, --serial, --link-radio (#221).

The rule they keep: Docker will not start a container whose `devices:` path is missing, so nothing that may be missing is
in compose.yaml, a flag checks the device is there before it writes anything, and a plain `./setup.sh` is what it was.
These run the real script in a copy of the docker folder, with a fake /dev (symlinks to /dev/null, which is a real
character device), a fake rules folder, and `docker`, `sudo`, `udevadm`, `gpiodetect` and `getent` stood in by shims."""
import stat
import pytest

from test_setup import calls_made, run, site, value  # noqa: F401 - the folder copy, the runner and its shims

NULL = "/dev/null"
BY_ID = "usb-FTDI_FT232R_USB_UART_A1B2C3-if00-port0"
BY_ID_2 = "usb-Prolific_PL2303-if00-port0"


@pytest.fixture
def hw(site, tmp_path):  # noqa: F811
    """The site, a fake /dev with one GPIO chip and one serial adapter, and shims that let `sudo cp` really copy."""
    folder, bin_dir, calls = site
    dev = tmp_path / "dev"
    (dev / "serial" / "by-id").mkdir(parents=True)
    (dev / "gpiochip0").symlink_to(NULL)
    (dev / "ttyUSB0").symlink_to(NULL)
    (dev / "ttyUSB1").symlink_to(NULL)
    (dev / "serial" / "by-id" / BY_ID).symlink_to("../../ttyUSB0")
    (dev / "serial" / "by-id" / BY_ID_2).symlink_to("../../ttyUSB1")
    rules = tmp_path / "rules"
    rules.mkdir()

    def shim(name, body):
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    shim("sudo", f'echo "sudo $*" >> {calls}; exec "$@"')  # logs, then does it, into the fake rules folder
    shim("udevadm", f'echo "udevadm $*" >> {calls}; exit 0')
    shim("getent", 'echo "gpio:x:997:pi"')
    shim("gpiodetect", 'echo "gpiochip0 [pinctrl-bcm2835] (54 lines)"')
    return {"site": site, "dev": dev, "rules": rules, "bin": bin_dir, "shim": shim, "folder": folder, "calls": calls}


def go(hw, *args, **env):
    environment = {"MOR_DEV_DIR": str(hw["dev"]), "MOR_UDEV_RULES_DIR": str(hw["rules"]), **env}
    return run(hw["site"], "--callsign", "W1AW", *args, **environment)


def env_file(hw):
    return (hw["folder"] / ".env").read_text()


def settled(hw):
    """The .env after a first plain run (it writes its own settings then), and the record of calls cleared."""
    assert go(hw).returncode == 0
    hw["calls"].write_text("")
    return env_file(hw)


def started(hw):
    return any(line == "docker compose up -d --build" for line in calls_made(hw["site"]))


def compose_files(hw):
    return value(hw["folder"], ".env", "COMPOSE_FILE")


def by_id(hw, name=BY_ID):
    return str(hw["dev"] / "serial" / "by-id" / name)


# -- a plain run is what it was ---------------------------------------------------------------------------------------


def test_a_plain_run_adds_nothing_for_hardware(hw):
    result = go(hw)
    assert result.returncode == 0, result.stderr
    text = env_file(hw)
    for name in ("COMPOSE_FILE", "MOR_GPIOCHIP", "MOR_SERIAL_DEVICES", "MOR_LINK_RADIO_ID", "MOR_GPIO_GID"):
        assert name not in text, name
    assert not (hw["folder"] / "compose.serial.yaml").exists()
    assert started(hw)
    assert not [line for line in calls_made(hw["site"]) if "udevadm" in line]


# -- --gpio -----------------------------------------------------------------------------------------------------------


def test_gpio_gives_the_core_the_header_chip_and_the_gpio_group(hw):
    result = go(hw, "--gpio")
    assert result.returncode == 0, result.stderr
    chip = str(hw["dev"] / "gpiochip0")
    assert value(hw["folder"], ".env", "MOR_GPIOCHIP") == chip
    assert value(hw["folder"], ".env", "MOR_GPIO_GID") == "997"
    assert compose_files(hw) == "compose.yaml:compose.gpio.yaml"
    assert started(hw) and chip in result.stdout


def test_gpio_picks_the_chip_gpiodetect_says_is_the_headers(hw):
    (hw["dev"] / "gpiochip4").symlink_to(NULL)
    hw["shim"]("gpiodetect", 'printf "gpiochip0 [gpio-brcmstb] (32 lines)\\ngpiochip4 [pinctrl-rp1] (54 lines)\\n"')
    go(hw, "--gpio")
    assert value(hw["folder"], ".env", "MOR_GPIOCHIP") == str(hw["dev"] / "gpiochip4")


def test_gpio_without_gpiodetect_uses_the_only_chip(hw):
    (hw["bin"] / "gpiodetect").unlink()
    go(hw, "--gpio")
    assert value(hw["folder"], ".env", "MOR_GPIOCHIP") == str(hw["dev"] / "gpiochip0")


def test_gpio_that_cannot_tell_the_chips_apart_refuses_and_changes_nothing(hw):
    (hw["bin"] / "gpiodetect").unlink()
    (hw["dev"] / "gpiochip4").symlink_to(NULL)
    before = settled(hw)
    result = go(hw, "--gpio")
    assert result.returncode == 2 and "can't tell which" in result.stderr and "MOR_GPIOCHIP" in result.stderr
    assert env_file(hw) == before and not started(hw)


def test_gpio_on_a_machine_without_a_chip_refuses(hw):
    (hw["dev"] / "gpiochip0").unlink()
    (hw["bin"] / "gpiodetect").unlink()
    result = go(hw, "--gpio")
    assert result.returncode == 2 and "no" in result.stderr and "gpiochip" in result.stderr
    assert "COMPOSE_FILE" not in env_file(hw) and not started(hw)


def test_gpio_refuses_a_chip_the_owner_named_that_is_not_there(hw):
    (hw["folder"] / ".env").write_text(env_file(hw) + f"MOR_GPIOCHIP={hw['dev']}/gpiochip9\n")
    result = go(hw, "--gpio")
    assert result.returncode == 2 and "does not exist" in result.stderr and "will not start the core" in result.stderr
    assert "COMPOSE_FILE" not in env_file(hw) and not started(hw)


def test_gpio_refuses_something_that_is_not_a_device(hw):
    plain = hw["dev"] / "gpiochip3"
    plain.write_text("")
    (hw["folder"] / ".env").write_text(env_file(hw) + f"MOR_GPIOCHIP={plain}\n")
    result = go(hw, "--gpio")
    assert result.returncode == 2 and "is not a device" in result.stderr
    assert "COMPOSE_FILE" not in env_file(hw) and not started(hw)


def test_gpio_keeps_the_owners_chip_and_group(hw):
    (hw["dev"] / "gpiochip4").symlink_to(NULL)
    (hw["folder"] / ".env").write_text(env_file(hw) + f"MOR_GPIOCHIP={hw['dev']}/gpiochip4\nMOR_GPIO_GID=44\n")
    go(hw, "--gpio")
    assert value(hw["folder"], ".env", "MOR_GPIOCHIP") == f"{hw['dev']}/gpiochip4"
    assert value(hw["folder"], ".env", "MOR_GPIO_GID") == "44"


def test_gpio_without_a_gpio_group_says_so_and_sets_no_number(hw):
    hw["shim"]("getent", "exit 2")
    result = go(hw, "--gpio")
    assert result.returncode == 0 and "no gpio group" in result.stdout and "MOR_GPIO_GID" in result.stdout
    assert value(hw["folder"], ".env", "MOR_GPIO_GID") is None
    assert compose_files(hw) == "compose.yaml:compose.gpio.yaml"


def test_gpio_run_twice_changes_nothing(hw):
    go(hw, "--gpio")
    first = env_file(hw)
    second = go(hw, "--gpio")
    assert second.returncode == 0 and env_file(hw) == first
    assert first.count("COMPOSE_FILE=") == 1 and first.count("MOR_GPIOCHIP=") == 1


def test_gpio_adds_to_a_compose_file_list_the_owner_already_has(hw):
    (hw["folder"] / ".env").write_text(env_file(hw) + "COMPOSE_FILE=compose.yaml:compose.protect-sd.yaml\n")
    go(hw, "--gpio")
    assert compose_files(hw) == "compose.yaml:compose.protect-sd.yaml:compose.gpio.yaml"
    go(hw, "--gpio")
    assert compose_files(hw) == "compose.yaml:compose.protect-sd.yaml:compose.gpio.yaml"  # not twice


def test_a_compose_path_separator_the_owner_set_is_used(hw):
    (hw["folder"] / ".env").write_text(env_file(hw) + "COMPOSE_PATH_SEPARATOR=;\nCOMPOSE_FILE=compose.yaml;compose.protect-sd.yaml\n")
    go(hw, "--gpio")
    assert compose_files(hw) == "compose.yaml;compose.protect-sd.yaml;compose.gpio.yaml"


def test_no_start_writes_the_settings_and_starts_nothing(hw):
    result = go(hw, "--gpio", "--no-start")
    assert result.returncode == 0 and compose_files(hw) == "compose.yaml:compose.gpio.yaml" and not started(hw)


# -- --serial ---------------------------------------------------------------------------------------------------------


def test_serial_gives_the_core_that_adapter_and_nothing_else(hw):
    result = go(hw, "--serial", by_id(hw))
    assert result.returncode == 0, result.stderr
    assert value(hw["folder"], ".env", "MOR_SERIAL_DEVICES") == by_id(hw)
    assert compose_files(hw) == "compose.yaml:compose.serial.yaml"
    written = (hw["folder"] / "compose.serial.yaml").read_text()
    assert f"      - {by_id(hw)}:{by_id(hw)}\n" in written
    assert written.count("      - ") == 1 and "privileged" not in written and "cap_add" not in written
    assert started(hw)


def test_serial_can_be_repeated_and_each_adapter_is_mapped(hw):
    go(hw, "--serial", by_id(hw), "--serial", by_id(hw, BY_ID_2))
    assert value(hw["folder"], ".env", "MOR_SERIAL_DEVICES") == f"{by_id(hw)} {by_id(hw, BY_ID_2)}"
    written = (hw["folder"] / "compose.serial.yaml").read_text()
    assert f"      - {by_id(hw)}:{by_id(hw)}\n" in written and f"      - {by_id(hw, BY_ID_2)}:{by_id(hw, BY_ID_2)}\n" in written


def test_a_later_serial_adds_to_the_list_and_keeps_the_first(hw):
    go(hw, "--serial", by_id(hw))
    go(hw, "--serial", by_id(hw, BY_ID_2))
    assert value(hw["folder"], ".env", "MOR_SERIAL_DEVICES") == f"{by_id(hw)} {by_id(hw, BY_ID_2)}"
    assert (hw["folder"] / "compose.serial.yaml").read_text().count("      - ") == 2


def test_serial_run_twice_changes_nothing(hw):
    go(hw, "--serial", by_id(hw))
    env_first, file_first = env_file(hw), (hw["folder"] / "compose.serial.yaml").read_text()
    result = go(hw, "--serial", by_id(hw))
    assert result.returncode == 0 and env_file(hw) == env_first
    assert (hw["folder"] / "compose.serial.yaml").read_text() == file_first
    assert env_first.count("MOR_SERIAL_DEVICES=") == 1 and env_first.count("compose.serial.yaml") == 1


def test_a_missing_serial_device_is_refused_before_anything_is_written(hw):
    before = settled(hw)
    result = go(hw, "--serial", str(hw["dev"] / "serial" / "by-id" / "usb-not-plugged-in"))
    assert result.returncode == 2 and "does not exist" in result.stderr and "ls " in result.stderr and "by-id" in result.stderr
    assert env_file(hw) == before and not (hw["folder"] / "compose.serial.yaml").exists() and not started(hw)


def test_something_that_is_not_a_device_is_refused(hw):
    plain = hw["dev"] / "ttyUSB5"
    plain.write_text("")
    result = go(hw, "--serial", str(plain))
    assert result.returncode == 2 and "is not a serial device" in result.stderr
    assert "MOR_SERIAL_DEVICES" not in env_file(hw) and not started(hw)


def test_a_name_the_dashboard_would_not_offer_is_refused(hw):
    (hw["dev"] / "ttyS0").symlink_to(NULL)
    for name in (str(hw["dev"] / "ttyS0"), "/etc/passwd", "relative/ttyUSB0"):
        result = go(hw, "--serial", name)
        assert result.returncode == 2 and "not a name the dashboard offers" in result.stderr, name
    assert "MOR_SERIAL_DEVICES" not in env_file(hw)


def test_a_name_with_characters_that_would_break_an_env_file_is_refused(hw):
    result = go(hw, "--serial", f"{hw['dev']}/ttyUSB0; rm -rf x")
    assert result.returncode == 2 and "don't survive an .env file" in result.stderr
    assert "MOR_SERIAL_DEVICES" not in env_file(hw)


def test_a_ttyusb_number_is_allowed_with_a_warning_and_the_stable_name(hw):
    result = go(hw, "--serial", str(hw["dev"] / "ttyUSB0"))
    assert result.returncode == 0
    assert "numbered in the order USB devices were found" in result.stdout
    assert f"--serial {by_id(hw)}" in result.stdout  # the name that stays with this adapter
    assert by_id(hw, BY_ID_2) not in result.stdout.replace(by_id(hw), "")  # and not the other adapter's
    assert value(hw["folder"], ".env", "MOR_SERIAL_DEVICES") == str(hw["dev"] / "ttyUSB0")


def test_serial_without_a_device_is_a_usage_error(hw):
    result = run(hw["site"], "--callsign", "W1AW", "--serial", MOR_DEV_DIR=str(hw["dev"]))
    assert result.returncode == 2 and "--serial needs the device" in result.stderr


# -- --link-radio -----------------------------------------------------------------------------------------------------


def plug_link_interface(hw):
    (hw["dev"] / "moreopenrepeater-link").symlink_to(NULL)


def test_link_radio_installs_the_rule_for_that_interface_and_gives_the_core_its_name(hw):
    plug_link_interface(hw)
    result = go(hw, "--link-radio", "D4E5F6")
    assert result.returncode == 0, result.stderr
    rule = (hw["rules"] / "60-moreopenrepeater-link.rules").read_text()
    assert 'ATTRS{serial}=="D4E5F6"' in rule and 'SYMLINK+="moreopenrepeater-link"' in rule
    assert 'ENV{MOREOPENREPEATER_LINK}="1"' in rule and "moreopenrepeater-ptt" not in rule.replace("61-moreopenrepeater-ptt", "")
    ptt_rule = (hw["rules"] / "61-moreopenrepeater-ptt.rules").read_text()
    assert ptt_rule == (hw["folder"] / "udev" / "61-moreopenrepeater-ptt.rules").read_text()
    made = calls_made(hw["site"])
    assert "udevadm control --reload-rules" in " ".join(made) and "udevadm trigger --subsystem-match=hidraw --action=add" in " ".join(made)
    assert value(hw["folder"], ".env", "MOR_LINK_RADIO_ID") == "D4E5F6"
    assert compose_files(hw) == "compose.yaml:compose.link-radio.yaml"
    assert started(hw)


def test_the_link_rule_sorts_before_the_ptt_rule_so_the_ptt_rule_can_skip_it(hw):
    plug_link_interface(hw)
    go(hw, "--link-radio", "D4E5F6")
    names = sorted(path.name for path in hw["rules"].iterdir())
    assert names == ["60-moreopenrepeater-link.rules", "61-moreopenrepeater-ptt.rules"]


def test_link_radio_by_usb_port_for_an_interface_with_no_serial_number(hw):
    plug_link_interface(hw)
    go(hw, "--link-radio", "port:1-1.4")
    rule = (hw["rules"] / "60-moreopenrepeater-link.rules").read_text()
    assert 'KERNELS=="1-1.4"' in rule and "ATTRS{serial}" not in rule
    assert value(hw["folder"], ".env", "MOR_LINK_RADIO_ID") == "port:1-1.4"


def test_link_radio_run_twice_installs_once_and_changes_nothing(hw):
    plug_link_interface(hw)
    go(hw, "--link-radio", "D4E5F6")
    env_first = env_file(hw)
    sudo_before = [line for line in calls_made(hw["site"]) if line.startswith(("sudo", "udevadm"))]
    result = go(hw, "--link-radio", "D4E5F6")
    assert result.returncode == 0 and "already installed" in result.stdout
    assert env_file(hw) == env_first
    assert [line for line in calls_made(hw["site"]) if line.startswith(("sudo", "udevadm"))] == sudo_before  # nothing more with sudo
    assert env_first.count("compose.link-radio.yaml") == 1 and env_first.count("MOR_LINK_RADIO_ID=") == 1


def test_link_radio_keeps_the_interface_the_owner_chose(hw):
    plug_link_interface(hw)
    (hw["folder"] / ".env").write_text(env_file(hw) + "MOR_LINK_RADIO_ID=AAAA\n")
    result = go(hw, "--link-radio", "BBBB")
    assert "already sets MOR_LINK_RADIO_ID=AAAA" in result.stdout
    assert value(hw["folder"], ".env", "MOR_LINK_RADIO_ID") == "AAAA"
    assert 'ATTRS{serial}=="AAAA"' in (hw["rules"] / "60-moreopenrepeater-link.rules").read_text()


def test_link_radio_whose_interface_is_not_plugged_in_is_refused_and_not_enabled(hw):
    result = go(hw, "--link-radio", "D4E5F6")  # the name does not exist after the rule is installed
    assert result.returncode == 2 and "moreopenrepeater-link" in result.stderr and "does not exist" in result.stderr
    assert "./setup.sh --list-interfaces" in result.stderr
    assert "compose.link-radio.yaml" not in env_file(hw) and not started(hw)


def test_link_radio_with_something_that_is_not_a_serial_number_is_refused(hw):
    plug_link_interface(hw)
    before = settled(hw)
    for bad in ('AB"CD', "x y", "port:abc"):
        result = go(hw, "--link-radio", bad)
        assert result.returncode == 2, bad
    assert env_file(hw) == before and not list(hw["rules"].iterdir()) and not started(hw)


def test_link_radio_without_an_id_is_a_usage_error(hw):
    result = run(hw["site"], "--callsign", "W1AW", "--link-radio", MOR_DEV_DIR=str(hw["dev"]))
    assert result.returncode == 2 and "--link-radio needs" in result.stderr and "--list-interfaces" in result.stderr


def test_list_interfaces_prints_and_changes_nothing(hw):
    before = settled(hw)
    result = run(hw["site"], "--list-interfaces", MOR_SYS_ROOT=str(hw["folder"].parent / "no-sys"))
    assert result.returncode == 0 and "No CM108 or AIOC interface" in result.stdout
    assert env_file(hw) == before and not calls_made(hw["site"])  # not even docker


# -- all three, and what happens when a device goes away ----------------------------------------------------------------


def test_all_three_together_enable_each_file_once_in_order(hw):
    plug_link_interface(hw)
    go(hw, "--gpio", "--serial", by_id(hw), "--link-radio", "D4E5F6")
    assert compose_files(hw) == "compose.yaml:compose.gpio.yaml:compose.serial.yaml:compose.link-radio.yaml"


def test_a_later_plain_run_does_not_start_when_the_gpio_chip_is_gone(hw):
    go(hw, "--gpio")
    (hw["dev"] / "gpiochip0").unlink()
    (hw["site"][2]).write_text("")
    result = go(hw)
    assert result.returncode == 0 and "the GPIO chip" in result.stdout and "does not exist" in result.stdout
    assert "./setup.sh --gpio" in result.stdout and not started(hw)


def test_a_later_plain_run_does_not_start_when_a_serial_adapter_is_gone(hw):
    go(hw, "--serial", by_id(hw), "--serial", by_id(hw, BY_ID_2))
    (hw["dev"] / "serial" / "by-id" / BY_ID_2).unlink()
    hw["site"][2].write_text("")
    result = go(hw)
    assert result.returncode == 0 and f"the serial port {by_id(hw, BY_ID_2)} does not exist" in result.stdout
    assert by_id(hw) not in result.stdout.split("does not exist")[0].split("NOTE")[-1]  # only the missing one is named
    assert not started(hw)


def test_a_later_plain_run_does_not_start_when_the_link_interface_is_gone(hw):
    plug_link_interface(hw)
    go(hw, "--link-radio", "D4E5F6")
    (hw["dev"] / "moreopenrepeater-link").unlink()
    hw["site"][2].write_text("")
    result = go(hw)
    assert "the link radio's interface" in result.stdout and "does not exist" in result.stdout and not started(hw)


def test_a_later_plain_run_starts_when_everything_mapped_is_there(hw):
    plug_link_interface(hw)
    go(hw, "--gpio", "--serial", by_id(hw), "--link-radio", "D4E5F6")
    hw["site"][2].write_text("")
    result = go(hw)
    assert result.returncode == 0 and started(hw) and "does not exist" not in result.stdout


def test_the_help_lists_the_new_flags(hw):
    result = run(hw["site"], "--help")
    for flag in ("--gpio", "--serial DEVICE", "--link-radio ID", "--list-interfaces"):
        assert flag in result.stdout, flag
