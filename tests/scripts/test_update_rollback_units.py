"""Keep updater rollback snapshots aligned with the units installed by the Pi installer."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def _units_from_installer() -> set[str]:
    source = (ROOT / "scripts" / "install-pi.sh").read_text()
    block = re.search(r"for unit in (.*?)\n  install -m 644", source, re.S)
    assert block, "could not locate the installer systemd unit list"
    return set(re.findall(r"moreopenrepeater[\w.-]+\.(?:service|path)", block.group(1)))


def _units_from_updater() -> set[str]:
    source = (ROOT / "scripts" / "update.sh").read_text()
    block = re.search(r"for unit in (.*?)\n    files\+=", source, re.S)
    assert block, "could not locate the updater rollback unit list"
    return set(re.findall(r"moreopenrepeater[\w.-]+\.(?:service|path)", block.group(1)))


def test_updater_snapshots_every_unit_installed_by_pi_installer():
    installed = _units_from_installer()
    snapshotted = _units_from_updater()

    assert installed == snapshotted, (
        f"installer-only units: {sorted(installed - snapshotted)}; "
        f"rollback-only units: {sorted(snapshotted - installed)}"
    )
