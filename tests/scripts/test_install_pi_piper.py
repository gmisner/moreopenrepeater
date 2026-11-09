"""Piper is the default neural TTS on Pi installs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = (ROOT / "scripts" / "install-pi.sh").read_text()


def test_installer_prefers_piper_tts_dependency():
    assert 'dependencies=("piper-tts[http]>=1.2")' in INSTALLER
    assert "piper.download_voices" in INSTALLER
    assert "piper_voice_ready" in INSTALLER
    assert "en_US-amy-low" in INSTALLER


def test_installer_keeps_kokoro_only_when_already_installed():
    assert "pip show kokoro-cli" in INSTALLER
    assert 'dependencies+=("kokoro-cli>=0.4")' in INSTALLER
