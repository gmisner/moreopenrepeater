import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.gpio import GpioControl, GpioError, parse_gpio_command
from api.service import RepeaterService
from audio_io.cm108 import CM108Interface
from controller.events import RunAction
from controller.state_machine import RepeaterConfig

PINS = {"1": {"mode": "output", "name": "Fan"}, "2": {"mode": "input", "name": "Door"}, "4": {"mode": "output", "name": ""}}


class FakeHidraw:
    def __init__(self):
        self.writes = []
        self.gpio = 0

    def write_report(self, report):
        self.writes.append((report[2], report[3]))  # (levels, outputs)

    def get_input_report(self, length):
        return bytes([0, self.gpio, 0, 0])


def make(pins=PINS):
    service = RepeaterService(config=RepeaterConfig(gpio_pins=pins))
    device = FakeHidraw()
    return GpioControl(service, CM108Interface(device), poll_seconds=0.01), service, device


def pin(status, number):
    return next(p for p in status["pins"] if p["pin"] == number)


def test_parse_gpio_command():
    assert parse_gpio_command("1 on") == (1, "on", 1.0)
    assert parse_gpio_command(" 8  PULSE 2.5 ") == (8, "pulse", 2.5)
    for bad in ("3 on", "1 blink", "1 on 2", "1 pulse 61", "on"):
        with pytest.raises(ValueError):
            parse_gpio_command(bad)


def test_outputs_start_low_and_unused_pins_go_back_to_inputs():
    async def scenario():
        gpio, service, device = make()
        gpio.start()
        assert device.writes == [(0, 0x01), (0, 0x09)]
        assert pin(gpio.status(), 1) == {"pin": 1, "name": "Fan", "mode": "output", "on": False}
        gpio.set_output(1, True)
        service.update_config(gpio_pins={"4": {"mode": "output", "name": ""}})
        assert device.writes[-1] == (0, 0x08)
        assert pin(gpio.status(), 1)["mode"] is None
        await gpio.stop()

    asyncio.run(scenario())


def test_only_outputs_can_be_switched():
    gpio, *_ = make()
    with pytest.raises(GpioError, match="GPIO2 isn't set up as an output"):
        gpio.set_output(2, True)
    no_interface = GpioControl(RepeaterService(config=RepeaterConfig(gpio_pins=PINS)), None)
    with pytest.raises(GpioError, match="No CM108"):
        no_interface.set_output(1, True)
    assert not no_interface.status()["available"]


def test_macro_commands_say_what_happened():
    async def scenario():
        gpio, _, device = make()
        gpio.start()
        assert gpio.run_command("1 on") == "Fan on."
        assert gpio.run_command("1 toggle") == "Fan off."
        assert gpio.run_command("4 toggle") == "Output 4 on."
        assert gpio.run_command("2 on") == "That output is not set up."
        assert gpio.run_command("1 pulse 0.05") == "Fan pulsed."
        assert gpio.status()["pins"][0]["on"] is True
        await asyncio.sleep(0.1)
        assert gpio.status()["pins"][0]["on"] is False
        assert device.writes[-1] == (0x08, 0x09)  # GPIO4 stayed on
        await gpio.stop()

    asyncio.run(scenario())


def test_switching_a_pulsed_output_cancels_the_pulse():
    async def scenario():
        gpio, *_ = make()
        gpio.start()
        gpio.pulse(1, 0.05)
        gpio.set_output(1, True)
        await asyncio.sleep(0.1)
        assert pin(gpio.status(), 1)["on"] is True
        await gpio.stop()

    asyncio.run(scenario())


def test_inputs_are_polled():
    async def scenario():
        gpio, _, device = make()
        gpio.start()
        assert pin(gpio.status(), 2)["on"] is None
        device.gpio = 0x02
        await asyncio.sleep(0.05)
        assert pin(gpio.status(), 2)["on"] is True
        device.gpio = 0
        await asyncio.sleep(0.05)
        assert pin(gpio.status(), 2)["on"] is False
        await gpio.stop()

    asyncio.run(scenario())


def test_routes_and_the_dtmf_macro(tmp_path):
    gpio, service, device = make({})
    spoken = []
    service.speak = spoken.append
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
        log_path=tmp_path / "test.log",
        gpio=gpio,
    )
    with TestClient(app) as client:
        assert client.put("/api/config", json={"gpio_pins": {"3": {"mode": "output"}}}).status_code == 422
        assert client.put("/api/config", json={"gpio_pins": {"1": {"mode": "relay"}}}).status_code == 422
        config = client.put("/api/config", json={"gpio_pins": {"1": {"mode": "output", "name": "Fan"}}}).json()
        assert config["gpio_pins"] == {"1": {"mode": "output", "name": "Fan"}}
        assert pin(client.put("/api/gpio/1", json={"on": True}).json(), 1)["on"] is True
        assert client.put("/api/gpio/2", json={"on": True}).status_code == 409
        assert client.post("/api/macros", json={"pattern": "*91", "action": "gpio", "command": "3 on"}).status_code == 422
        assert client.post("/api/macros", json={"pattern": "*90", "action": "gpio", "command": "1 off"}).status_code == 200
        service._run_action(RunAction("gpio", "1 off"))
        assert spoken == ["tts:Fan off."]
        assert pin(client.get("/api/gpio").json(), 1)["on"] is False
