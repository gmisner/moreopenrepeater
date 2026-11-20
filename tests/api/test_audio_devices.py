import pytest

from api.audio_devices import (
    AudioDeviceList,
    alsa_card_stable_id,
    audio_devices_from_core,
    device_stem,
    list_audio_devices,
    remember_devices,
    resolve_audio_device,
    set_cache_path,
)


def test_device_stem_strips_hw_address():
    assert device_stem("USB Audio Device: - (hw:3,0)") == "USB Audio Device: -"
    assert device_stem("Built-in") == "Built-in"


def test_alsa_card_stable_id_prefers_usb_serial(tmp_path):
    root = tmp_path / "root"
    usb = root / "sys/devices/platform/usb/1-1.2"
    usb.mkdir(parents=True)
    (usb / "idVendor").write_text("0d8c\n")
    (usb / "idProduct").write_text("013c\n")
    (usb / "serial").write_text("DIGI123\n")
    sound = root / "sys/class/sound/card3"
    sound.mkdir(parents=True)
    (sound / "device").symlink_to(usb)

    assert alsa_card_stable_id(3, sys_root=root) == "usb-0d8c:013c-DIGI123"


def test_resolve_uses_stable_id_when_hw_number_moves(tmp_path):
    set_cache_path(tmp_path / "ids.json")
    remember_devices(
        [{"name": "USB Audio Device: - (hw:3,0)", "stable_id": "usb-0d8c:013c-1-1.2"}]
    )
    devices = [
        {
            "name": "USB Audio Device: - (hw:4,0)",
            "inputs": 1,
            "outputs": 1,
            "default_samplerate": 48000,
            "card": 4,
            "stable_id": "usb-0d8c:013c-1-1.2",
        }
    ]
    assert resolve_audio_device("USB Audio Device: - (hw:3,0)", devices) == "USB Audio Device: - (hw:4,0)"


def test_resolve_unique_stem_without_cache():
    set_cache_path(None)
    devices = [
        {
            "name": "Digirig Link: - (hw:5,0)",
            "inputs": 1,
            "outputs": 1,
            "default_samplerate": 48000,
            "card": 5,
            "stable_id": "usb-a",
        }
    ]
    assert resolve_audio_device("Digirig Link: - (hw:2,0)", devices) == "Digirig Link: - (hw:5,0)"


def test_list_audio_devices_adds_card_and_stable_id():
    raw = [
        {
            "name": "USB Audio Device: - (hw:2,0)",
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        }
    ]
    devices = list_audio_devices(query=lambda: raw, card_id=lambda card: f"id-{card}")
    assert devices == [
        {
            "name": "USB Audio Device: - (hw:2,0)",
            "inputs": 1,
            "outputs": 2,
            "default_samplerate": 48000.0,
            "card": 2,
            "stable_id": "id-2",
        }
    ]


AIOC = {"name": "All-In-One-Cable: USB Audio (hw:2,0)", "inputs": 1, "outputs": 2, "default_samplerate": 48000.0, "card": 2, "stable_id": "usb-1209:7388-1-1.2"}


def test_the_core_lists_its_cards_once_and_keeps_the_answer_for_a_while():
    calls = []
    clock = {"now": 0.0}
    cards = AudioDeviceList(lambda: calls.append(1) or [AIOC], cache_seconds=30.0, clock=lambda: clock["now"])
    assert cards.status() == {"devices": [AIOC], "error": None}
    clock["now"] = 29.0
    cards.status()
    assert len(calls) == 1  # the API asks every couple of seconds; PortAudio is not asked each time
    clock["now"] = 31.0
    cards.status()
    assert len(calls) == 2


def test_a_card_list_that_fails_is_reported_not_raised_and_tried_again_later():
    clock = {"now": 0.0}
    state = {"fail": True}

    def listing():
        if state["fail"]:
            raise OSError("PortAudio is not initialized")
        return [AIOC]

    cards = AudioDeviceList(listing, cache_seconds=30.0, clock=lambda: clock["now"])
    assert cards.status() == {"devices": [], "error": "PortAudio is not initialized"}
    state["fail"] = False
    clock["now"] = 31.0
    assert cards.status() == {"devices": [AIOC], "error": None}


class FakeCore:
    def __init__(self, status=None, away=False):
        self.status, self.away = status, away

    def hardware_status(self, name):
        assert name == "audio_device_list"
        if self.away:
            raise RuntimeError("the core is not being heard from, so its hardware state is not known")
        return self.status


def test_the_api_shows_what_the_core_reports():
    assert audio_devices_from_core(FakeCore({"devices": [AIOC], "error": None}))() == [AIOC]


def test_the_api_raises_instead_of_showing_an_empty_list_when_the_core_cannot_say():
    with pytest.raises(RuntimeError, match="PortAudio exploded"):
        audio_devices_from_core(FakeCore({"devices": [], "error": "PortAudio exploded"}))()
    with pytest.raises(RuntimeError, match="not being heard from"):
        audio_devices_from_core(FakeCore(away=True))()
