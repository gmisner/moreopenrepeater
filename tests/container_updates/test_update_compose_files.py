"""update.sh must use the same compose files as the rest of the install: the ones COMPOSE_FILE in .env lists (setup.sh puts the
overlays there), never its own -f, or a layout you turned on would be dropped the next time it ran."""
import re
from pathlib import Path

DOCKER = Path(__file__).resolve().parents[2] / "docker"


def test_update_sh_uses_the_same_compose_file_list_and_never_dash_f(checkout):
    (checkout.docker / ".env").write_text("COMPOSE_FILE=compose.yaml:compose.gpio.yaml:compose.protect-sd.yaml\n")
    result = checkout.run("update.sh")
    assert result.returncode == 0, result.stderr
    assert checkout.builds() == ["docker compose up -d --build"]  # no -f: Compose reads COMPOSE_FILE from .env itself
    assert checkout.env_value("COMPOSE_FILE") == "compose.yaml:compose.gpio.yaml:compose.protect-sd.yaml"  # and it is left as it was
    code = "\n".join(line for line in (DOCKER / "update.sh").read_text().splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"docker compose[^\n]* -f ", code)


def test_update_sh_makes_the_modems_own_settings_file_from_an_existing_winlink_env(checkout):
    (checkout.docker / "winlink.env").write_text("MOR_WINLINK_CALL=W1AW\nMOR_WINLINK_ADMIN_PASSWORD=a-long-admin-password\nMOR_PACKET_PTT=none\n")
    result = checkout.run("update.sh")
    assert result.returncode == 0, result.stderr
    made = (checkout.docker / "direwolf.env").read_text()
    assert "MOR_WINLINK_CALL=W1AW" in made and "MOR_PACKET_PTT=none" in made and "PASSWORD" not in made
    assert "a-long-admin-password" not in result.stdout + result.stderr
    assert checkout.index("docker compose up -d --build") is not None
