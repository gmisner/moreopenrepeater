import asyncio
import errno
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.gpio import GpioControl
from api.service import RepeaterService
from api.tx_fan import TxFan
from audio_io.cm108 import CM108Interface
from controller.state_machine import RepeaterConfig


class FakeHidraw:
    def __init__(self):
        self.writes = []

    def write_report(self, report):
        self.writes.append((report[2], report[3]))  # (levels, outputs)

    def get_input_report(self, length):
        return bytes(4)


class FakeLine:
    def __init__(self, fail_writes=0):
        self.writes = []
        self.closed = False
        self.fail_writes = fail_writes

    def write(self, active):
        if self.fail_writes:
            self.fail_writes -= 1
            raise OSError(errno.EIO, "Input/output error")
        self.writes.append(active)

    def read(self):
        return False

    def close(self):
        self.closed = True


class Pins:
    def __init__(self, error=None, **line_options):
        self.opened = []
        self.lines = []
        self.error = error
        self.line_options = line_options

    def __call__(self, pin, *, output, active_low):
        if self.error is not None:
            raise self.error
        self.opened.append((pin, output, active_low))
        self.lines.append(FakeLine(**self.line_options))
        return self.lines[-1]


def run(scenario):
    asyncio.run(scenario())


def cm108_fan(**overrides):
    now = [0.0]
    config = RepeaterConfig(fan_output="cm108", fan_cm108_pin=1, fan_run_on_minutes=1.0, **overrides)
    service = RepeaterService(config=config)
    device = FakeHidraw()
    cm108 = CM108Interface(device)
    return TxFan(service, cm108, clock=lambda: now[0]), service, cm108, device, now


def test_the_fan_runs_while_transmitting_and_for_the_run_on_after():
    async def scenario():
        fan, service, _, device, now = cm108_fan()
        fan.start()
        assert device.writes == [(0, 0x01)]
        assert fan.status()["on"] is False
        service.ptt_active = True
        fan.tick()
        assert device.writes[-1] == (0x01, 0x01)
        assert fan.status()["reason"] == "transmitting"
        service.ptt_active = False
        now[0] = 30.0
        fan.tick()
        status = fan.status()
        assert status["on"] is True and status["reason"] == "run-on" and status["run_on_left"] == 30.0
        now[0] = 60.5
        fan.tick()
        assert device.writes[-1] == (0, 0x01)
        assert fan.status() == {
            "output": "cm108", "pin": 1, "on": False, "reason": None, "run_on_left": None, "temperature_c": None, "error": None,
        }
        await fan.stop()
        assert device.writes[-1] == (0, 0)

    run(scenario)


def test_an_active_low_fan_pin_is_high_while_off():
    async def scenario():
        fan, service, _, device, _ = cm108_fan(fan_polarity="low")
        fan.start()
        assert device.writes == [(0x01, 0x01)]
        service.ptt_active = True
        fan.tick()
        assert device.writes[-1] == (0, 0x01)
        await fan.stop()

    run(scenario)


def test_the_gpio_card_leaves_the_fans_pin_alone():
    async def scenario():
        fan, service, cm108, _, _ = cm108_fan(gpio_pins={"4": {"mode": "output", "name": "Light"}})
        gpio = GpioControl(service, cm108)
        gpio.start()
        fan.start()
        row = next(p for p in gpio.status()["pins"] if p["pin"] == 1)
        assert row == {"pin": 1, "name": "Transmitter fan", "mode": "fan", "on": False, "until": None}
        service.update_config(gpio_pins={})
        assert cm108.output_level(1) is False
        assert cm108.output_level(4) is None
        service.update_config(gpio_pins={"1": {"mode": "input", "name": "Door"}})
        assert fan.status()["error"] == "GPIO1 is set up on the CM108 GPIO card too."
        assert fan.status()["on"] is None
        assert next(p for p in gpio.status()["pins"] if p["pin"] == 1)["mode"] == "input"
        await fan.stop()
        await gpio.stop()

    run(scenario)


def test_a_fan_on_a_cm108_needs_one():
    async def scenario():
        service = RepeaterService(config=RepeaterConfig(fan_output="cm108"))
        fan = TxFan(service, None)
        fan.start()
        service.ptt_active = True
        fan.tick()
        assert fan.status()["error"] == "No CM108 interface was found when the service started."
        assert fan.status()["on"] is None
        await fan.stop()

    run(scenario)


def test_a_fan_on_a_pi_pin():
    async def scenario():
        pins = Pins()
        service = RepeaterService(config=RepeaterConfig(fan_output="gpio", fan_gpio_pin=26, fan_polarity="low"))
        fan = TxFan(service, None, open_pin=pins)
        fan.start()
        assert pins.opened == [(26, True, True)]
        line = pins.lines[0]
        service.ptt_active = True
        fan.tick()
        assert line.writes == [True]
        service.update_config(fan_output="none")
        assert line.writes == [True, False] and line.closed
        assert fan.status()["on"] is None
        await fan.stop()

    run(scenario)


def test_a_pi_pin_in_use_or_busy_is_reported():
    async def scenario():
        pins = Pins()
        config = RepeaterConfig(fan_output="gpio", fan_gpio_pin=17, audio_enabled=True, ptt_output="gpio", ptt_gpio_pin=17)
        service = RepeaterService(config=config)
        fan = TxFan(service, None, open_pin=pins)
        fan.start()
        assert fan.status()["error"] == "GPIO17 is already the repeater's PTT pin."
        assert pins.opened == []
        service.update_config(fan_gpio_pin=26)
        assert pins.opened == [(26, True, False)] and fan.status()["error"] is None
        service.update_config(ptt_gpio_pin=26)
        assert pins.lines[0].closed
        assert fan.status()["error"] == "GPIO26 is already the repeater's PTT pin."
        await fan.stop()

        service.update_config(fan_gpio_pin=21)
        busy = TxFan(service, None, open_pin=Pins(error=OSError(errno.EBUSY, "Device or resource busy")))
        busy.start()
        assert busy.status()["error"] == "couldn't open GPIO21 for the fan: Device or resource busy"
        await busy.stop()

    run(scenario)


def test_a_failed_switch_is_retried():
    async def scenario():
        pins = Pins(fail_writes=1)
        service = RepeaterService(config=RepeaterConfig(fan_output="gpio"))
        fan = TxFan(service, None, open_pin=pins)
        fan.start()
        service.ptt_active = True
        fan.tick()
        assert fan.status()["error"] == "couldn't switch the fan: Input/output error"
        fan.tick()
        assert pins.lines[0].writes == [True]
        assert fan.status()["error"] is None and fan.status()["on"] is True
        await fan.stop()

    run(scenario)


def test_the_fan_runs_while_the_cpu_is_hot():
    async def scenario():
        now = [0.0]
        temperatures = [69.0, 70.0, 66.0, 64.0]
        pins = Pins()
        service = RepeaterService(config=RepeaterConfig(fan_output="gpio", fan_temp_c=70.0))
        fan = TxFan(service, None, open_pin=pins, temperature=lambda: temperatures.pop(0), clock=lambda: now[0])
        fan.start()
        line = pins.lines[0]
        fan.tick()
        assert line.writes == []
        fan.tick()  # not read again for ten seconds
        assert temperatures == [70.0, 66.0, 64.0]
        for expected in ([True], [True], [True, False]):
            now[0] += 10
            fan.tick()
            assert line.writes == expected
        assert fan.status()["temperature_c"] == 64.0
        await fan.stop()

    run(scenario)


def test_fan_settings_and_status_routes(tmp_path):
    pins = Pins()
    service = RepeaterService()
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
        log_path=tmp_path / "test.log",
        gpio=GpioControl(service, None),
        tx_fan=TxFan(service, None, open_pin=pins),
    )
    with TestClient(app) as client:
        assert client.get("/api/gpio").json()["fan"]["output"] == "none"
        for bad in ({"fan_cm108_pin": 3}, {"fan_temp_c": 30}, {"fan_run_on_minutes": 61}, {"fan_output": "relay"}):
            assert client.put("/api/config", json=bad).status_code == 422
        config = client.put(
            "/api/config", json={"fan_output": "gpio", "fan_gpio_pin": 21, "fan_run_on_minutes": 5, "fan_temp_c": 75}
        ).json()
        assert (config["fan_output"], config["fan_gpio_pin"], config["fan_run_on_minutes"], config["fan_temp_c"]) == ("gpio", 21, 5, 75)
        assert pins.opened == [(21, True, False)]
        assert client.put("/api/config", json={"clear_fan_temp_c": True}).json()["fan_temp_c"] is None
        fan = client.get("/api/gpio").json()["fan"]
        assert fan["output"] == "gpio" and fan["pin"] == 21 and fan["on"] is False and fan["error"] is None
        config = client.put("/api/config", json={"fan_output": "cm108", "fan_cm108_pin": "2"}).json()
        assert config["fan_cm108_pin"] == 2
        assert client.get("/api/gpio").json()["fan"]["error"] == "No CM108 interface was found when the service started."
    assert pins.lines[0].closed
