"""The DVSwitch installer runs as root, so it must not run anything it fetched
over an unauthenticated channel. It trusts a keyring that ships in this repo,
checked against a pinned fingerprint, and apt verifies the packages."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = (ROOT / "scripts" / "dmr-install.sh").read_text()
KEYRING = ROOT / "packaging" / "dmr" / "dvswitch-keyring.gpg"
RESTART_UNIT = (ROOT / "packaging" / "moreopenrepeater-dmr-restart.service").read_text()


def _code_lines() -> list[str]:
    return [line for line in INSTALLER.splitlines() if not line.lstrip().startswith("#")]


def test_installer_fetches_nothing_over_plain_http():
    assert not [line for line in _code_lines() if "http://" in line]


def test_installer_does_not_execute_a_downloaded_script():
    code = "\n".join(_code_lines())
    assert "dvswitch-$repo_script" not in code
    assert not re.search(r"curl[^\n]*-o\s+\"?/tmp/", code)
    assert not re.search(r"chmod \+x[^\n]*/tmp/", code)
    assert "| bash" not in code and "| sh" not in code


def test_installer_uses_the_shipped_keyring_with_a_pinned_fingerprint():
    assert "packaging/dmr/dvswitch-keyring.gpg" in INSTALLER
    assert re.search(r'DVSWITCH_KEY_FPR="[0-9A-F]{40}"', INSTALLER)
    assert "--with-colons" in INSTALLER  # compares the full fingerprint, not a short key id
    assert "DVSWITCH_KEYRING=/usr/share/keyrings/dvswitch-keyring.gpg" in INSTALLER
    assert "deb [signed-by=%s] https://dvswitch.org/DVSwitch_Repository" in INSTALLER


def test_installer_stops_if_the_keyring_does_not_match():
    assert "fail" in INSTALLER.split("DVSWITCH_KEY_FPR=", 1)[1]
    assert "does not match" in INSTALLER


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg is not installed")
def test_shipped_keyring_has_exactly_the_pinned_fingerprint():
    pinned = re.search(r'DVSWITCH_KEY_FPR="([0-9A-F]{40})"', INSTALLER).group(1)
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run(
            ["gpg", "--show-keys", "--with-colons", "--fingerprint", str(KEYRING)],
            capture_output=True, text=True, check=True, env={**os.environ, "GNUPGHOME": home},
        ).stdout
    primary = []
    take_next = False
    for line in out.splitlines():
        if line.startswith("pub:"):
            take_next = True
        elif line.startswith("fpr:") and take_next:
            primary.append(line.split(":")[9])
            take_next = False
    assert primary == [pinned]


def test_restart_helper_unit_gets_a_private_tmp():
    assert re.search(r"^PrivateTmp=yes$", RESTART_UNIT, re.MULTILINE)
