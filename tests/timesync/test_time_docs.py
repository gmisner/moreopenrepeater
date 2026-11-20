"""The docs say what the time container is, what privilege it needs and how to undo it; the code and the docs name the same things."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = (ROOT / "docs" / "docker.md").read_text()
SECTION = DOCS[DOCS.index("## The time container"):DOCS.index("## Health and restarts")]


def test_the_section_covers_the_check_the_setting_the_container_the_privilege_the_host_and_removal():
    for heading in ("### What the check does", "### The server setting", "### The optional time container", "#### The privilege it needs, in plain words",
                    "#### If the host already syncs its own clock", "#### Removing it"):
        assert heading in SECTION, heading


def test_the_privilege_is_said_plainly_with_the_one_capability_and_the_host_wide_effect():
    assert "`CAP_SYS_TIME`, which lets it set the host's clock" in SECTION
    assert "every other\ncontainer and the host itself see the change" in SECTION
    assert "**only** `SYS_TIME` added back" in SECTION
    assert "has not been\n  run yet, not on a Pi and not on Docker" in SECTION  # and it says it was never run


def test_the_commands_to_turn_the_hosts_sync_off_and_to_remove_it_are_there_and_the_dangerous_one_is_warned_against():
    for command in ("sudo timedatectl set-ntp false", "sudo timedatectl set-ntp true", "sudo systemctl disable --now chrony",
                    "docker compose --profile time rm -sf chrony", "./setup.sh --time", "docker compose --profile time up -d --build chrony"):
        assert command in SECTION, command
    assert "Do not use `docker compose down -v`" in SECTION


def test_both_routes_are_documented_with_who_may_call_them():
    assert "| `GET /api/time` | any signed-in account |" in SECTION and "| `PUT /api/time/ntp` | admin only |" in SECTION


def test_the_events_in_the_log_table_are_the_ones_the_code_logs():
    code = (ROOT / "src" / "api" / "time_sync.py").read_text()
    for event in ("time.clock_off", "time.clock_ok", "time.check_failed", "time.server_changed"):
        assert f"`{event}`" in DOCS and f'"{event}"' in code, event
    assert "`alerts`, `sensors` or `time`" in DOCS


def test_the_numbers_in_the_docs_are_the_numbers_in_the_code():
    from api import time_sync as ts

    assert ts.OFFSET_LIMIT_SECONDS == 2.0 and "off by 2 seconds or more twice in a row" in SECTION and ts.ALERT_CONFIRM_CHECKS == 2
    assert ts.ALERT_REPEAT_SECONDS == 6 * 3600 and "after six hours" in SECTION
    assert ts.CHECK_SECONDS == 600 and "once every ten minutes" in SECTION
    assert ts.FIRST_CHECK_SECONDS == 120 and "two minutes after start" in SECTION
    assert ts.MAX_SERVER_LENGTH == 253 and "at most 253 characters" in SECTION
    assert ts.QUERY_TIMEOUT_SECONDS == 2.0 and "a 2 second wait" in SECTION


def test_nothing_this_change_wrote_has_an_em_dash():
    names = [
        "docker/chrony-supervisor.py", "docker/Dockerfile.chrony", "src/api/time_sync.py", "src/api/models/time_sync.py", "src/api/routes/time_sync.py",
        "docs/docker.md", "docker/setup.sh", "docker/update.sh",
    ]
    for name in names:
        assert "—" not in (ROOT / name).read_text(), name
    compose = (ROOT / "docker" / "compose.yaml").read_text()
    assert "—" not in compose[compose.index("  # The time container, off by default"):]
    assert not re.search("—", SECTION)
