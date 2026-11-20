"""A page that needs hardware the container was not given says what is missing and the command that gives it (#221).

The words are the dashboard's: the GPIO, serial and link radio errors are what the Audio page shows under the card, and
the serial port list is what its drop-downs offer. The core, not the API container, knows which ports it was given."""
import dataclasses
import errno

import pytest

from api import host_hardware
from api.host_hardware import (
    NO_SECOND_INTERFACE,
    SerialPortList,
    header_pin_reason,
    missing_link_interface,
    serial_port_reason,
    serial_ports_from_core,
)
from api.link_radio import setup_problem
from api.monitor_receiver import MonitorReceiverService
from api.recordings import RecordingStore
from audio_io.receiver import MonitorReceiver
from api.service.core_handle import CoreUnavailable
from api.tx_fan import TxFan
from controller.state_machine import RepeaterConfig
from api.service import RepeaterService

from test_link_radio import CONFIG as LINK_CONFIG, Rig
from test_live_audio import FakeHeader, make_live
from test_monitor_receiver import RATE, FakeInputStream, ImmediateLoop
from test_serial_ptt import SERIAL_REPEATER, FakeSerial, USB0, make as make_serial
from test_split_app import CoreThread, core, sock_dir, split_client, wait_for  # noqa: F401 - fixtures


@pytest.fixture
def container(monkeypatch):
    monkeypatch.setattr(host_hardware, "in_container", lambda env=None, exists=None: True)
    monkeypatch.setattr(host_hardware, "_gpio_chips", lambda: [])


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(host_hardware, "in_container", lambda env=None, exists=None: False)
    monkeypatch.setattr(host_hardware, "_gpio_chips", lambda: [])


NO_CHIP = FileNotFoundError(errno.ENOENT, "No such file or directory")
DENIED = PermissionError(errno.EACCES, "Permission denied")


# -- the wording ------------------------------------------------------------------------------------------------------


def test_a_missing_gpio_chip_in_the_container_names_the_command(container):
    reason = header_pin_reason(NO_CHIP)
    assert "not given to the core container" in reason and "./setup.sh --gpio" in reason and "docker folder" in reason


def test_a_gpio_chip_the_core_may_not_open_names_the_group_number_and_the_command(container, monkeypatch):
    monkeypatch.setattr(host_hardware, "_gpio_chips", lambda: ["/dev/gpiochip0"])
    reason = header_pin_reason(DENIED)
    assert "gpio group number" in reason and "./setup.sh --gpio" in reason


def test_other_gpio_failures_keep_the_systems_words_even_in_a_container(container, monkeypatch):
    busy = OSError(errno.EBUSY, "Device or resource busy")
    assert header_pin_reason(busy) == "Device or resource busy"
    monkeypatch.setattr(host_hardware, "_gpio_chips", lambda: ["/dev/gpiochip0"])
    assert header_pin_reason(NO_CHIP) == "No such file or directory"  # a chip is there: not the setup that is missing


def test_the_native_install_keeps_the_systems_words(native):
    assert header_pin_reason(NO_CHIP) == "No such file or directory"
    assert serial_port_reason(NO_CHIP) == "No such file or directory"


def test_a_missing_serial_port_in_the_container_names_the_command(container):
    reason = serial_port_reason(NO_CHIP)
    assert "not given to the core container" in reason
    assert "./setup.sh --serial /dev/serial/by-id/NAME" in reason and "ls /dev/serial/by-id" in reason


def test_a_serial_port_that_is_denied_keeps_the_systems_words(container):
    assert serial_port_reason(DENIED) == "Permission denied"


def test_the_link_radio_with_no_interface_given_names_the_command(container):
    message = missing_link_interface({"MOREOPENREPEATER_LINK_CM108_HIDRAW": "off"})
    assert "not given to the core container" in message
    assert "./setup.sh --link-radio SERIAL" in message and "./setup.sh --list-interfaces" in message


def test_the_link_radio_message_is_the_old_one_where_off_is_the_owners_choice(native):
    assert missing_link_interface({"MOREOPENREPEATER_LINK_CM108_HIDRAW": "off"}) == NO_SECOND_INTERFACE
    assert missing_link_interface({}) == NO_SECOND_INTERFACE


def test_a_link_interface_that_was_given_but_cannot_be_opened_says_so(container):
    message = missing_link_interface({"MOREOPENREPEATER_LINK_CM108_HIDRAW": "/dev/moreopenrepeater-link"})
    assert "/dev/moreopenrepeater-link" in message and "could not be opened" in message and "--link-radio" not in message


def test_in_container_is_told_by_the_install_setting_or_the_docker_marker():
    assert host_hardware.in_container({"MOREOPENREPEATER_INSTALL": "container"}, exists=lambda path: False)
    assert host_hardware.in_container({}, exists=lambda path: path == "/.dockerenv")
    assert host_hardware.in_container({}, exists=lambda path: path == "/run/.containerenv")
    assert not host_hardware.in_container({}, exists=lambda path: False)


# -- what the pages show ----------------------------------------------------------------------------------------------


class MissingChip:
    def __init__(self, error=NO_CHIP):
        self.error = error

    def __call__(self, *args, **kwargs):
        raise self.error


def test_the_audio_page_says_the_header_is_missing_for_ptt_on_a_pin(container):
    header = FakeHeader()
    header.open = MissingChip()
    live, _service, _clock, engines = make_live(RepeaterConfig(audio_enabled=True, ptt_output="gpio", ptt_gpio_pin=17), header=header)
    assert engines == []
    error = live.status()["error"]
    assert error.startswith("couldn't open GPIO17 for PTT: ") and "./setup.sh --gpio" in error


def test_the_audio_page_says_the_serial_port_is_missing(container):
    serial = FakeSerial()
    serial.open = MissingChip()
    live, _link, _service, _serial, engines = make_serial(SERIAL_REPEATER, serial)
    assert engines == []
    error = live.status()["error"]
    assert error.startswith(f"couldn't open {USB0} (") and "./setup.sh --serial /dev/serial/by-id/NAME" in error


def test_the_link_radio_card_says_its_interface_is_missing(container, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LINK_CM108_HIDRAW", "off")
    config = dataclasses.replace(LINK_CONFIG, link_radio_ptt="cm108")
    rig = Rig(config)  # no second CM108 was found
    assert rig.link.status()["error"] == missing_link_interface({"MOREOPENREPEATER_LINK_CM108_HIDRAW": "off"})
    assert "./setup.sh --link-radio" in rig.link.status()["error"]


def test_the_link_radio_setup_problem_is_still_the_old_words_on_a_native_install(native, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LINK_CM108_HIDRAW", "off")
    assert setup_problem(dataclasses.replace(LINK_CONFIG, link_radio_ptt="cm108"), None) == NO_SECOND_INTERFACE


def test_the_link_radios_serial_failure_names_the_command(container):
    serial = FakeSerial()
    serial.open = MissingChip()
    config = dataclasses.replace(
        LINK_CONFIG, link_radio_ptt="serial", link_radio_ptt_serial_device="/dev/ttyUSB1", link_radio_ptt_serial_line="rts"
    )
    _live, link, _service, _serial, _engines = make_serial(config, serial)
    error = link.status()["error"]
    assert error.startswith("couldn't open /dev/ttyUSB1 (RTS) for the linked radio's PTT: ")
    assert "./setup.sh --serial /dev/serial/by-id/NAME" in error


def test_the_link_radios_header_pin_failure_names_the_command(container):
    header = FakeHeader()
    header.open = MissingChip()
    config = dataclasses.replace(LINK_CONFIG, link_radio_ptt="gpio", link_radio_ptt_gpio_pin=20)
    rig = Rig(config, header=header)
    error = rig.link.status()["error"]
    assert error.startswith("couldn't open GPIO20 for the linked radio's PTT: ") and "./setup.sh --gpio" in error


def test_the_fan_on_a_header_pin_says_the_chip_is_missing(container):
    import asyncio

    async def scenario():
        fan = TxFan(RepeaterService(config=RepeaterConfig(fan_output="gpio", fan_gpio_pin=21)), None, open_pin=MissingChip())
        fan.start()
        error = fan.status()["error"]
        await fan.stop()
        return error

    error = asyncio.run(scenario())
    assert error.startswith("couldn't open GPIO21 for the fan: ") and "./setup.sh --gpio" in error


def test_the_monitor_receivers_squelch_pin_says_the_chip_is_missing(container, tmp_path):
    config = RepeaterConfig(monitor_enabled=True, monitor_input_device="USB Audio 2", monitor_squelch="gpio", monitor_gpio_pin=22)
    monitor = MonitorReceiverService(
        RepeaterService(config=config), RATE, RecordingStore(tmp_path / "monitor-recordings", sample_rate=RATE),
        receiver_factory=lambda *a, **kw: MonitorReceiver(*a, stream_factory=FakeInputStream, **kw),
        open_pin=MissingChip(),
    )
    monitor.attach(ImmediateLoop())
    error = monitor.status()["error"]
    assert error.startswith("couldn't open GPIO22 for the monitor's squelch: ") and "./setup.sh --gpio" in error


# -- the serial ports the dashboard offers ----------------------------------------------------------------------------


def test_the_core_reports_the_ports_it_was_given():
    ports = [{"path": "/dev/serial/by-id/usb-FTDI-if00-port0", "target": "/dev/serial/by-id/usb-FTDI-if00-port0"}]
    assert SerialPortList(lambda: ports).status() == {"ports": ports, "error": None}


def test_a_listing_that_fails_does_not_take_the_core_down():
    def broken():
        raise OSError("no /dev")

    assert SerialPortList(broken).status() == {"ports": [], "error": "no /dev"}


def test_the_api_lists_what_the_core_reports_and_not_its_own_devices():
    class Remote:
        def hardware_status(self, name):
            assert name == "serial_port_list"
            return {"ports": [{"path": "/dev/ttyUSB0", "target": "/dev/ttyUSB0"}], "error": None}

    assert serial_ports_from_core(Remote())() == [{"path": "/dev/ttyUSB0", "target": "/dev/ttyUSB0"}]


def test_a_core_that_is_away_is_unavailable_not_an_empty_list():
    class Away:
        def hardware_status(self, name):
            raise CoreUnavailable("the core is not being heard from")

    with pytest.raises(CoreUnavailable):
        serial_ports_from_core(Away())()


def test_a_core_that_could_not_list_its_ports_says_why():
    class Broken:
        def hardware_status(self, name):
            return {"ports": [], "error": "no /dev"}

    with pytest.raises(RuntimeError, match="no /dev"):
        serial_ports_from_core(Broken())()


def test_the_serial_port_list_the_dashboard_gets_is_the_cores_in_the_split_install(core, sock_dir):  # noqa: F811
    ports = [{"path": "/dev/serial/by-id/usb-FTDI-if00-port0", "target": "/dev/ttyUSB0"}]
    core.runtime.serial_port_list = SerialPortList(lambda: ports)
    with CoreThread(core, sock_dir / "core.sock", allow_simulate=False, log_hub=core.log_hub, activity_store=core.activity_store):
        client, remote = split_client(sock_dir / "core.sock")
        with client:
            wait_for(lambda: remote.connected and remote._config is not None and remote._macros is not None)
            wait_for(lambda: client.get("/api/serial/ports").status_code == 200)
            assert client.get("/api/serial/ports").json() == ports
