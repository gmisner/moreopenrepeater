"""Every setting the container files tell the owner to set is one the code reads (#221, #229).

Found when the dashboard became reachable from the LAN: `api.env.example` and `compose.yaml` said to set
MOREOPENREPEATER_ADMIN_USER and _PASSWORD, which nothing reads (the app reads MOREOPENREPEATER_AUTH_USER and
_AUTH_PASSWORD), so someone following them would have believed sign-in was on while the dashboard was open."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
FILES = [DOCKER / "compose.yaml", DOCKER / "compose.host-network.yaml", *sorted(DOCKER.glob("*.env.example")), DOCKER / ".env.example"]
NAME = re.compile(r"MOREOPENREPEATER_[A-Z0-9_]*[A-Z0-9]")
SOURCE = "\n".join(path.read_text() for path in sorted((ROOT / "src").rglob("*.py")))


def names(path: Path) -> set[str]:
    return set(NAME.findall(path.read_text()))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_setting_a_container_file_mentions_is_one_the_code_reads(path):
    unknown = sorted(name for name in names(path) if name not in SOURCE)
    assert not unknown, f"{path.name} mentions settings nothing reads: {unknown}"


def test_the_sign_in_settings_are_the_ones_the_app_reads():
    for name in ("MOREOPENREPEATER_AUTH_USER", "MOREOPENREPEATER_AUTH_PASSWORD"):
        assert name in SOURCE and name in (DOCKER / "api.env.example").read_text()
    assert "MOREOPENREPEATER_ADMIN_USER" not in SOURCE


def test_the_guard_can_fail(tmp_path):
    """The check above is only worth having if a made-up name is caught."""
    made_up = tmp_path / "x.env.example"
    made_up.write_text("# MOREOPENREPEATER_ADMIN_USER=admin\n# MOREOPENREPEATER_AUTH_USER=admin\n")
    assert sorted(name for name in names(made_up) if name not in SOURCE) == ["MOREOPENREPEATER_ADMIN_USER"]
