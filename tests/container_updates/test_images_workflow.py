"""The workflow that publishes the container images (.github/workflows/images.yml).

It can only be run for real on GitHub, so these check what can go wrong quietly: publishing an image that must not be
public, or one that compose and the Dockerfiles do not agree on."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
COMPOSE = yaml.safe_load((ROOT / "docker/compose.yaml").read_text())
PUBLISHED = WORKFLOW["jobs"]["build"]["strategy"]["matrix"]["image"]

# Not for a public registry until their licenses are checked (issue #367). winlink: upstream LinBPQ has no license file (#344).
NOT_PUBLISHED = {"winlink", "dmr", "allstar", "direwolf"}


def test_images_that_are_not_cleared_for_a_public_registry_are_not_published():
    assert not NOT_PUBLISHED & set(PUBLISHED)
    assert not NOT_PUBLISHED & set(WORKFLOW["jobs"]["publish"]["strategy"]["matrix"]["image"])


def test_build_and_publish_cover_the_same_images():
    assert PUBLISHED == WORKFLOW["jobs"]["publish"]["strategy"]["matrix"]["image"]


def test_every_published_image_has_a_dockerfile_and_a_compose_service_that_names_it():
    for name in PUBLISHED:
        assert (ROOT / "docker" / f"Dockerfile.{name}").is_file(), name
        service = COMPOSE["services"][name]
        assert service["build"]["dockerfile"] == f"docker/Dockerfile.{name}", name
        assert service["image"] == f"${{MOR_REGISTRY:-}}moreopenrepeater-{name}:${{MOR_TAG:-local}}", name


def test_it_builds_both_architectures_on_native_runners():
    arch = WORKFLOW["jobs"]["build"]["strategy"]["matrix"]["arch"]
    assert {a["platform"] for a in arch} == {"linux/amd64", "linux/arm64"}
    assert {a["runner"] for a in arch} == {"ubuntu-24.04", "ubuntu-24.04-arm"}


def test_a_pull_request_never_pushes():
    text = (ROOT / ".github/workflows/images.yml").read_text()
    assert 'push=false' in text  # the plan says so for pull_request
    assert "needs.plan.outputs.push == 'true'" in text  # login, digests and publish all wait for it
    assert WORKFLOW["jobs"]["publish"]["if"] == "needs.plan.outputs.push == 'true'"


def test_only_commits_on_main_whose_tests_passed_are_published():
    plan = next(step for step in WORKFLOW["jobs"]["plan"]["steps"] if step.get("id") == "plan")["run"]
    assert "merge-base --is-ancestor" in plan and "origin/main" in plan
    assert "--workflow tests.yml" in plan and "--status success" in plan
