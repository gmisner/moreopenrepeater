from api.audio_devices import (
    alsa_card_stable_id,
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
