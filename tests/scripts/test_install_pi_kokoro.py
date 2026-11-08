"""Regression: updates must not re-download Kokoro when models are present."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = (ROOT / "scripts" / "install-pi.sh").read_text()


def test_installer_skips_kokoro_model_download_when_ready():
    assert "kokoro_models_ready" in INSTALLER
    assert "skipping download" in INSTALLER
    assert 'stat -c%s "$model"' in INSTALLER


def test_installer_reuses_pip_cache_across_venv_swaps():
    assert 'PIP_CACHE_DIR="${PIP_CACHE_DIR:-/var/cache/moreopenrepeater/pip}"' in INSTALLER
    assert "--cache-dir \"$PIP_CACHE_DIR\"" in INSTALLER
    # Fresh venvs still rebuild, but wheels must not force a network pull every update.
    assert "--no-cache-dir -e" not in INSTALLER
