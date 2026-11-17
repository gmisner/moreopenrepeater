"""Keep systemd's startup timeout above either TTS service's ready wait."""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def _ready_timeout(path: Path) -> float:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DEFAULT_READY_TIMEOUT_SECONDS"
            for target in node.targets
        ):
            return float(ast.literal_eval(node.value))
    raise AssertionError(f"DEFAULT_READY_TIMEOUT_SECONDS not found in {path}")


def test_systemd_start_timeout_exceeds_tts_ready_wait():
    tts_wait = max(
        _ready_timeout(ROOT / "src" / "playout" / "piper_service.py"),
        _ready_timeout(ROOT / "src" / "playout" / "kokoro_service.py"),
    )
    unit = (ROOT / "packaging" / "moreopenrepeater.service").read_text()
    match = re.search(r"^TimeoutStartSec=(\d+)([smh]?)\s*$", unit, re.M)
    assert match, "TimeoutStartSec not found in moreopenrepeater.service"
    value, suffix = int(match.group(1)), match.group(2)
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[suffix]
    systemd_timeout = value * multiplier

    assert systemd_timeout > tts_wait, (
        f"TimeoutStartSec={systemd_timeout}s must exceed the TTS ready wait "
        f"of {tts_wait}s"
    )
