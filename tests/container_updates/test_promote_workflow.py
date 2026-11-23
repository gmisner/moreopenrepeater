"""The promote workflow (.github/workflows/promote.yml) publishes a commit's images before it moves a channel.

It can only be run for real on GitHub. These check the ordering and permissions that are easy to get wrong without anyone
noticing: a channel pointing at a commit with no images, or an images job that quietly cannot push."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROMOTE = yaml.safe_load((ROOT / ".github/workflows/promote.yml").read_text())
IMAGES = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
JOBS = PROMOTE["jobs"]


def scripts(job):
    """All the shell a job runs, as plain text."""
    return "\n".join(step.get("run", "") for step in JOBS[job].get("steps", []))


def test_the_images_are_published_before_the_channel_moves():
    assert JOBS["images"]["needs"] == "check"
    assert set(JOBS["move"]["needs"]) == {"check", "images"}
    # Nothing but the move job may push the branch.
    for name in JOBS:
        pushes = "git push" in scripts(name)
        assert pushes == (name == "move"), name


def test_it_calls_the_images_workflow_with_the_checked_commit_and_the_tags():
    job = JOBS["images"]
    assert job["uses"] == "./.github/workflows/images.yml"
    assert job["with"]["commit"] == "${{ needs.check.outputs.sha }}"
    assert job["with"]["tags"] == "${{ needs.check.outputs.tags }}"
    assert "workflow_call" in IMAGES[True]  # PyYAML reads the key `on` as True


def test_the_images_job_may_push_to_the_registry():
    # A called workflow gets no more than its caller gives it, so without this the pushes fail.
    assert JOBS["images"]["permissions"]["packages"] == "write"


def test_the_channel_tag_and_the_release_tag_go_on_the_images():
    check = scripts("check")
    assert 'echo "tags=$CHANNEL${release:+,$release}"' in check
    # The release tag is worked out once, in the check, and the release is made from the same value.
    assert JOBS["move"]["steps"][1]["env"]["RELEASE"] == "${{ needs.check.outputs.release }}"
    assert "refs/tags/$release" in check


def test_the_check_keeps_the_rules_a_channel_always_had():
    check = scripts("check")
    assert "git merge-base --is-ancestor" in check
    assert "--workflow tests.yml" in check
    # A fast-forward only: a plain push, never forced.
    move = scripts("move")
    assert "--force" not in move and "git push -f" not in move
