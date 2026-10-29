"""Regression for scripts/asl-harden.sh (install-pi.sh --allstar)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARDEN = ROOT / "scripts" / "asl-harden.sh"

MODULES = """\
[modules]
autoload = no

load = chan_simpleusb.so              ; SimpleUSB Radio Interface Channel Driver
load => chan_usbradio.so               ; USB Console Channel Driver
noload = chan_usrp.so                   ; USRP Channel Module
require = res_usbradio.so

[global]
"""


def _run(asterisk_dir: Path, fresh: int) -> None:
    subprocess.run(["bash", str(HARDEN), str(asterisk_dir), str(fresh)], check=True)


@pytest.mark.skipif(sys.platform == "darwin", reason="asl-harden.sh uses GNU sed -i (Pi / CI)")
def test_fresh_install_hides_secrets_and_unloads_usb_radio(tmp_path: Path) -> None:
    (tmp_path / "manager.conf").write_text("[general]\nsecret = ami-secret\n")
    (tmp_path / "pjsip.conf").write_text("[trunk]\npassword = sip-secret\n")
    (tmp_path / "modules.conf").write_text(MODULES)
    for name in ("manager.conf", "pjsip.conf", "modules.conf"):
        (tmp_path / name).chmod(0o644)

    _run(tmp_path, fresh=1)

    for name in ("manager.conf", "pjsip.conf"):
        mode = stat.S_IMODE((tmp_path / name).stat().st_mode)
        assert mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH) == 0, f"{name} still world-accessible: {oct(mode)}"

    modules = (tmp_path / "modules.conf").read_text()
    assert "noload = chan_simpleusb.so" in modules
    assert "noload => chan_usbradio.so" in modules
    assert "noload = chan_usrp.so" in modules  # untouched


@pytest.mark.skipif(sys.platform == "darwin", reason="asl-harden.sh uses GNU sed -i (Pi / CI)")
def test_existing_install_keeps_loaded_usb_drivers(tmp_path: Path) -> None:
    (tmp_path / "manager.conf").write_text("[general]\nsecret = ami-secret\n")
    (tmp_path / "manager.conf").chmod(0o644)
    (tmp_path / "modules.conf").write_text(MODULES)

    _run(tmp_path, fresh=0)

    mode = stat.S_IMODE((tmp_path / "manager.conf").stat().st_mode)
    assert mode & stat.S_IROTH == 0
    assert (tmp_path / "modules.conf").read_text() == MODULES


def test_harden_script_is_what_the_installer_calls() -> None:
    installer = (ROOT / "scripts" / "install-pi.sh").read_text()
    assert 'bash "$INSTALL_DIR/scripts/asl-harden.sh" "$ASTERISK_DIR" "$fresh"' in installer
    assert HARDEN.is_file() and os.access(HARDEN, os.X_OK)
