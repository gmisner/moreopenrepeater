"""The compose files for hardware the core is only given when asked (#221).

The danger they guard: Docker refuses to start a container whose `devices:` path is missing, so a device that may not be
there must never be in compose.yaml. And what an optional file maps is exactly the node asked for: no privileged mode, no
added capability, not the whole of /dev. Compose merges a list by appending and a mapping by replacing, which `merged`
does here so the result for a given .env can be checked."""
import re
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"


def load(name):
    return yaml.safe_load((DOCKER / name).read_text())


def interpolate(text, env):
    """${NAME:-default} and ${NAME:?message}, as Compose does."""

    def one(match):
        name, op, rest = match.group(1), match.group(2), match.group(3)
        if env.get(name):
            return env[name]
        if op == ":?":
            raise KeyError(f"{name} is required: {rest}")
        return rest

    return re.sub(r"\$\{(\w+)(:-|:\?)([^}]*)\}", one, text)


def merged(env, *names):
    """The core service's devices (a list, appended) and environment (a mapping, later files win) across files."""
    devices, environment = [], {}
    for name in names:
        core = load(name)["services"]["core"]
        devices += [interpolate(entry, env) for entry in core.get("devices", [])]
        environment.update({key: interpolate(str(value), env) for key, value in (core.get("environment") or {}).items() if not key.startswith("<<")})
    return devices, environment


BASE_ENV = {}


def test_the_default_compose_file_maps_only_the_sound_cards_and_the_ptt_interface():
    devices, _ = merged(BASE_ENV, "compose.yaml")
    assert devices == ["/dev/snd:/dev/snd", "/dev/moreopenrepeater-ptt:/dev/moreopenrepeater-ptt"]


def test_the_default_compose_file_has_none_of_the_optional_nodes():
    text = (DOCKER / "compose.yaml").read_text()
    assert not re.search(r"^\s+- [^#\n]*(gpiochip|ttyUSB|ttyACM|/serial/|moreopenrepeater-link)", text, re.M)
    core = load("compose.yaml")["services"]["core"]
    assert not any(re.search(r"gpiochip|tty|serial|link", entry) for entry in core["devices"])


def test_the_default_compose_file_still_turns_the_link_radio_off():
    assert load("compose.yaml")["services"]["core"]["environment"]["MOREOPENREPEATER_LINK_CM108_HIDRAW"] == "off"


def test_the_gpio_file_maps_exactly_the_one_chip_asked_for_and_nothing_else():
    document = load("compose.gpio.yaml")
    assert list(document) == ["services"] and list(document["services"]) == ["core"]
    assert list(document["services"]["core"]) == ["devices"]  # no privileged, cap_add, group_add, environment
    devices, _ = merged({"MOR_GPIOCHIP": "/dev/gpiochip4"}, "compose.gpio.yaml")
    assert devices == ["/dev/gpiochip4:/dev/gpiochip4"]


def test_the_gpio_file_refuses_to_load_without_a_chip_rather_than_guessing_one():
    try:
        merged({}, "compose.gpio.yaml")
    except KeyError as error:
        assert "MOR_GPIOCHIP" in str(error) and "./setup.sh --gpio" in str(error)
    else:
        raise AssertionError("the file loaded without MOR_GPIOCHIP")


def test_the_link_radio_file_maps_exactly_its_interface_and_names_it_in_the_cores_environment():
    document = load("compose.link-radio.yaml")
    assert list(document["services"]) == ["core"] and set(document["services"]["core"]) == {"devices", "environment"}
    devices, environment = merged(BASE_ENV, "compose.yaml", "compose.link-radio.yaml")
    assert devices == ["/dev/snd:/dev/snd", "/dev/moreopenrepeater-ptt:/dev/moreopenrepeater-ptt", "/dev/moreopenrepeater-link:/dev/moreopenrepeater-link"]
    assert environment["MOREOPENREPEATER_LINK_CM108_HIDRAW"] == "/dev/moreopenrepeater-link"  # not "off" any more
    assert environment["MOREOPENREPEATER_CM108_HIDRAW"] == "/dev/moreopenrepeater-ptt"  # the repeater's is unchanged


def test_the_link_name_can_be_overridden_and_follows_into_the_environment():
    devices, environment = merged({"MOR_LINK_HIDRAW": "/dev/hidraw3"}, "compose.yaml", "compose.link-radio.yaml")
    assert devices[-1] == "/dev/hidraw3:/dev/hidraw3" and environment["MOREOPENREPEATER_LINK_CM108_HIDRAW"] == "/dev/hidraw3"


def test_nothing_widens_the_container_beyond_the_device_nodes():
    for name in ("compose.gpio.yaml", "compose.link-radio.yaml"):
        core = load(name)["services"]["core"]
        for forbidden in ("privileged", "cap_add", "device_cgroup_rules", "pid", "ipc", "security_opt", "volumes", "network_mode"):
            assert forbidden not in core, (name, forbidden)
        for entry in core["devices"]:
            assert not entry.startswith(("/dev:", "/dev/:")) and "*" not in entry, (name, entry)
    base = load("compose.yaml")["services"]["core"]
    assert "privileged" not in base and "cap_add" not in base and base["cap_drop"] == ["ALL"]


def test_the_core_user_is_already_in_the_groups_these_nodes_need():
    groups = load("compose.yaml")["services"]["core"]["group_add"]
    assert "audio" in groups and "dialout" in groups and any("MOR_GPIO_GID" in str(group) for group in groups)


def test_the_example_env_has_no_active_hardware_setting_because_setup_copies_it_as_it_is():
    active = [line for line in (DOCKER / ".env.example").read_text().splitlines() if line and not line.startswith("#")]
    assert active == []  # a live COMPOSE_FILE or MOR_GPIOCHIP here would map a device on every machine


def test_the_serial_file_setup_writes_is_not_committed_since_it_is_this_machines():
    assert "/docker/compose.serial.yaml" in (DOCKER.parent / ".gitignore").read_text().splitlines()
    assert not (DOCKER / "compose.serial.yaml").exists()
