"""The API image learns its commit from build arguments: .env -> compose build args -> Dockerfile ARG -> ENV -> the app.

Each link is checked, and the last one is run: the ENV lines the Dockerfile sets are fed, with sample argument values, to
the same function the API calls at startup. If any link is cut (an ARG not declared, a compose arg dropped, a name that
differs from what the app reads) the page would say "unknown" on a correctly updated install."""
import re
from pathlib import Path

import yaml

from api.updates import container_install_from_env

DOCKER = Path(__file__).resolve().parents[2] / "docker"
DOCKERFILE = (DOCKER / "Dockerfile.api").read_text()
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
ARGS = ["MOR_COMMIT", "MOR_COMMIT_DATE", "MOR_BRANCH", "MOR_REPO"]


def test_compose_hands_each_value_from_env_to_the_api_build():
    build = COMPOSE["services"]["api"]["build"]
    assert build["dockerfile"] == "docker/Dockerfile.api"
    assert build["args"] == {name: "${%s:-}" % name for name in ARGS}  # empty when never recorded, never an error


def test_the_dockerfile_declares_each_argument_and_turns_it_into_an_environment_variable():
    for name in ARGS:
        assert re.search(rf'^ARG {name}=""$', DOCKERFILE, re.M), name
        assert re.search(rf"=\${name}\b", DOCKERFILE), name
    assert "MOREOPENREPEATER_INSTALL=container" in DOCKERFILE


def test_the_arguments_come_after_the_slow_layers_so_a_new_commit_does_not_redo_them():
    first_arg = DOCKERFILE.index("ARG MOR_COMMIT=")
    assert DOCKERFILE.index("pip install") < first_arg
    assert DOCKERFILE.index("COPY src /app/src") < first_arg


def test_what_the_dockerfile_sets_is_what_the_app_reads():
    sample = {
        "MOR_COMMIT": "a" * 40,
        "MOR_COMMIT_DATE": "2026-11-19T08:30:00+00:00",
        "MOR_BRANCH": "beta",
        "MOR_REPO": "gmisner/moreopenrepeater",
    }
    # `ENV A=x \` continued lines, with $ARG replaced by the sample value
    env_block = re.search(r"^ENV (MOREOPENREPEATER_INSTALL=container(?:.*\\\n)+.*)$", DOCKERFILE, re.M).group(1)
    env = {}
    for name, value in re.findall(r"(MOREOPENREPEATER_\w+)=(\S+?)(?:\s*\\)?$", env_block, re.M):
        env[name] = re.sub(r"\$(\w+)", lambda m: sample[m.group(1)], value)
    install = container_install_from_env(env)
    assert install is not None
    assert (install.commit, install.date, install.branch, install.repo) == (
        sample["MOR_COMMIT"], sample["MOR_COMMIT_DATE"], "beta", "gmisner/moreopenrepeater",
    )
    assert install.channel == "beta"


def test_an_image_built_without_recording_is_a_container_with_an_unknown_commit():
    env_block = re.search(r"^ENV (MOREOPENREPEATER_INSTALL=container(?:.*\\\n)+.*)$", DOCKERFILE, re.M).group(1)
    env = {name: re.sub(r"\$\w+", "", value) for name, value in re.findall(r"(MOREOPENREPEATER_\w+)=(\S+?)(?:\s*\\)?$", env_block, re.M)}
    install = container_install_from_env(env)
    assert install is not None and install.version is None


def test_only_the_api_image_gets_the_arguments():
    for name, service in COMPOSE["services"].items():
        build = service.get("build")
        if name != "api" and isinstance(build, dict):
            assert "MOR_COMMIT" not in (build.get("args") or {}), name
