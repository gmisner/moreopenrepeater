"""The loopback script's test sequence, run straight through the processor.

Checks the script's expectations hold with no devices involved, so a
failure on real hardware points at the audio path, not the test itself.
"""
import importlib.util
from pathlib import Path

from audio_io.processor import AudioProcessor, ProcessorSettings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "loopback_test.py"


def load_script():
    spec = importlib.util.spec_from_file_location("loopback_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sequence_produces_expected_events():
    script = load_script()
    processor = AudioProcessor(ProcessorSettings(script.RATE, cos_source="vox", vox_threshold_db=-35.0, vox_hold=0.4))
    signal = script.test_signal(script.RATE)
    events = []
    for start in range(0, len(signal) - script.BLOCK + 1, script.BLOCK):
        events += processor.process(signal[start : start + script.BLOCK]).events

    for label, ok in script.evaluate(events):
        assert ok, label
