"""The images install exact, hashed Python packages, so a rebuild (./update.sh) cannot move numpy, pydantic or sounddevice by itself.

The base images and downloads were already pinned by digest or commit; the Python packages were ranges (`>=`) with no lockfile. These
check the files are exact pins with hashes, that they still satisfy the ranges the project asks for, and that the Dockerfiles install
them in a way that refuses anything else."""
import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
LOCKS = ["core-requirements.txt", "core-piper-requirements.txt", "logd-requirements.txt", "api-requirements.txt"]
INPUTS = {"core-requirements.txt": "pins/core.in", "core-piper-requirements.txt": "pins/piper.in", "logd-requirements.txt": "pins/logd.in"}


def entries(name):
    """[(package, version, marker or None, [hashes])] for each requirement in the file."""
    found, current = [], None
    for raw in (DOCKER / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash="):
            current[3].append(line.removeprefix("--hash=").removesuffix("\\").strip())
            continue
        text = line.removesuffix("\\").strip()
        assert not text.startswith("-"), f"{name}: an option line other than a hash: {text}"
        requirement, _, marker = text.partition(";")
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([0-9][A-Za-z0-9.+!_-]*)", requirement.strip())
        assert match, f"{name}: not an exact pin: {text}"
        current = [canonicalize_name(match.group(1)), match.group(2), marker.strip() or None, []]
        found.append(current)
    return found


@pytest.mark.parametrize("name", LOCKS)
def test_every_requirement_is_an_exact_pin_with_a_hash_for_each_file(name):
    found = entries(name)
    assert found, name
    for package, version, _, hashes in found:
        assert hashes, f"{name}: {package}=={version} has no hash"
        for digest in hashes:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (name, package, digest)
    names = [package for package, *_ in found]
    assert len(names) == len(set(names)), f"{name}: a package is listed twice"
    requirement_lines = [line for line in (DOCKER / name).read_text().splitlines() if re.match(r"[A-Za-z]", line)]
    assert requirement_lines and not [line for line in requirement_lines if re.search(r"[<>~!]=?|\*| @ |git\+|://", line.split(";")[0])]


def ranges(path, key=None):
    if key:
        return [Requirement(line) for line in tomllib.loads(path.read_text())["project"][key]]
    return [Requirement(line.strip()) for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def pinned(name):
    return {package: Version(version) for package, version, *_ in entries(name)}


def test_the_dashboard_pins_satisfy_the_ranges_in_pyproject_and_no_dependency_is_missing():
    pins = pinned("api-requirements.txt")
    for requirement in ranges(ROOT / "pyproject.toml", "dependencies"):
        package = canonicalize_name(requirement.name)
        assert package in pins, f"{requirement} is in pyproject.toml but not in api-requirements.txt (run docker/pins/update.sh)"
        assert requirement.specifier.contains(pins[package], prereleases=True), f"{requirement} is not met by {pins[package]}"
        for extra in requirement.extras:
            assert extra == "standard"  # uvicorn[standard]: its extras are in the file because they are pinned like the rest
    assert {"uvicorn", "httptools", "uvloop", "watchfiles", "websockets", "python-dotenv", "pyyaml"} <= set(pins)  # what [standard] brings


@pytest.mark.parametrize("name", ["core-requirements.txt", "logd-requirements.txt", "core-piper-requirements.txt"])
def test_the_other_pins_satisfy_the_ranges_they_are_made_from(name):
    pins = pinned(name)
    for requirement in ranges(DOCKER / INPUTS[name]):
        package = canonicalize_name(requirement.name)
        assert package in pins, f"{requirement} is in {INPUTS[name]} but not in {name} (run docker/pins/update.sh)"
        assert requirement.specifier.contains(pins[package], prereleases=True), f"{requirement} is not met by {pins[package]}"


def test_the_cores_ranges_are_the_ones_pyproject_has():
    """pins/core.in says it is in step with pyproject.toml: the same name must not have a looser or different range."""
    project = {canonicalize_name(r.name): r for r in ranges(ROOT / "pyproject.toml", "dependencies")}
    for requirement in ranges(DOCKER / "pins/core.in"):
        package = canonicalize_name(requirement.name)
        if package in project:
            assert str(requirement.specifier) == str(project[package].specifier), package


def test_piper_inside_the_core_agrees_with_the_cores_own_pins():
    core = pinned("core-requirements.txt")
    piper = pinned("core-piper-requirements.txt")
    for package in set(core) & set(piper):
        assert core[package] == piper[package], f"{package} would move from {core[package]} to {piper[package]} when Piper is installed"
    assert "piper-tts" in piper and "numpy" in piper


def test_the_core_and_the_collector_get_nothing_the_web_app_needs():
    for name in ("core-requirements.txt", "logd-requirements.txt"):
        pins = pinned(name)
        assert not {"fastapi", "uvicorn", "mcp", "starlette"} & set(pins), name
    assert not {"numpy", "sounddevice", "httpx"} & set(pinned("logd-requirements.txt"))  # no numpy, audio or web client in the collector
    assert {"numpy", "sounddevice", "httpx", "pydantic"} <= set(pinned("core-requirements.txt"))


def installs(dockerfile):
    """Each `pip install` in the Dockerfile, with its line continuations joined."""
    text = (DOCKER / dockerfile).read_text().replace("\\\n", " ")
    return [line.strip() for line in text.splitlines() if "pip install" in line and not line.lstrip().startswith("#")]


@pytest.mark.parametrize("dockerfile", ["Dockerfile.core", "Dockerfile.logd", "Dockerfile.api"])
def test_each_image_installs_only_hashed_requirements_or_the_project_itself_without_dependencies(dockerfile):
    found = installs(dockerfile)
    assert found
    for line in found:
        hashed = "--require-hashes" in line and "--only-binary=:all:" in line and re.search(r"-r /tmp/[a-z-]+-requirements\.txt", line)
        project = "--no-deps /app" in line
        assert hashed or project, f"{dockerfile}: {line}"
        assert not re.search(r"pip install\s+['\"]?[A-Za-z]", line), f"{dockerfile}: installs a named package: {line}"


def test_each_image_copies_the_file_it_installs():
    for dockerfile, copies in {
        "Dockerfile.core": ["core-requirements.txt", "core-piper-requirements.txt"], "Dockerfile.logd": ["logd-requirements.txt"],
        "Dockerfile.api": ["api-requirements.txt"],
    }.items():
        text = (DOCKER / dockerfile).read_text()
        for name in copies:
            assert f"COPY docker/{name} /tmp/{name}" in text and f"-r /tmp/{name}" in text, (dockerfile, name)
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    for name in LOCKS:
        assert f"!docker/{name}" in ignore, name  # the build context excludes everything else


def test_piper_in_the_core_is_off_when_asked_and_its_pins_are_the_only_install_under_it():
    text = (DOCKER / "Dockerfile.core").read_text()
    assert 'if [ "$WITH_PIPER" = "1" ]' in text
    assert 'piper-tts[http]>=1.2' not in text


def test_the_update_script_names_every_file_and_the_docs_say_how_to_use_it():
    script = (DOCKER / "pins/update.sh").read_text()
    for name in LOCKS:
        assert name in script
    assert "--generate-hashes" in script and "--no-build" in script and "--python-version 3.13" in script
    docs = (ROOT / "docs/docker.md").read_text()
    assert "Updating the pinned Python packages" in docs and "./pins/update.sh" in docs and "--upgrade-package" in docs
    assert "pip --require-hashes" in docs


def test_the_python_version_the_pins_are_for_is_the_images():
    base = re.search(r"^FROM python:(\d+\.\d+)-", (DOCKER / "Dockerfile.core").read_text(), re.M).group(1)
    assert f"--python-version {base}" in (DOCKER / "pins/update.sh").read_text()
