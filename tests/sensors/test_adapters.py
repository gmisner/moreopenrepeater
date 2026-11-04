from pathlib import Path

from sensors.ads1115 import FULL_SCALE_V, LSB_V, _config_word
from sensors.ds18b20 import list_ds18b20_ids, read_ds18b20_celsius
from sensors.speech import speak_reading, speak_value


def test_ds18b20_reads_w1_slave(tmp_path: Path):
    device = tmp_path / "28-00000abcdef"
    device.mkdir()
    (device / "w1_slave").write_text(
        "5e 01 4b 46 7f ff 0c 10 61 : crc=61 YES\n"
        "5e 01 4b 46 7f ff 0c 10 61 t=21875\n"
    )
    assert list_ds18b20_ids(tmp_path) == ["28-00000abcdef"]
    assert read_ds18b20_celsius(w1_root=tmp_path) == 21.875
    assert read_ds18b20_celsius("28-00000abcdef", w1_root=tmp_path) == 21.875


def test_ds18b20_skips_missing_and_bad_crc(tmp_path: Path):
    assert read_ds18b20_celsius(w1_root=tmp_path) is None
    device = tmp_path / "28-bad"
    device.mkdir()
    (device / "w1_slave").write_text("crc=00 NO\nt=0\n")
    assert read_ds18b20_celsius(w1_root=tmp_path) is None


def test_ads1115_config_and_lsb():
    assert _config_word(0) >> 12 & 0b111 == 0b100
    assert _config_word(3) >> 12 & 0b111 == 0b111
    assert abs(LSB_V * 32767 - FULL_SCALE_V) < 0.001
    # No /dev/i2c in CI — graceful skip.
    from sensors.ads1115 import read_ads1115_volts

    assert read_ads1115_volts(0, i2c_path="/dev/i2c-missing-for-tests") is None


def test_spoken_readings():
    assert speak_value(13.2) == "thirteen point two"
    assert speak_value(0.5) == "zero point five"
    assert speak_value(100) == "one hundred"
    assert speak_reading("Battery", 13.2, "V") == "Battery thirteen point two volts"
    assert speak_reading("Cabinet", None, "C") == "Cabinet is not available."
