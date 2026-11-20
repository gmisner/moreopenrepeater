"""setup.sh notes which commit a first install is, so the Updates page can say what is running.

Same as docker/update.sh does on every update (test_update_script.py): the API image has no git, so the commit goes
into .env and compose builds it into the image. setup.sh only records; it never pulls."""
import shutil


def prepare(checkout):
    (checkout.root / "ptt").write_text("")
    (checkout.docker / ".env").write_text(f"MOR_PTT_HIDRAW={checkout.root / 'ptt'}\nMOR_DATA_DIR={checkout.root / 'data'}\n")


def test_a_first_install_records_the_commit_before_it_builds(checkout):
    prepare(checkout)
    result = checkout.run("setup.sh", "--callsign", "W1AW")
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_COMMIT") == checkout.head()
    assert checkout.env_value("MOR_BRANCH") == "main"
    assert checkout.env_value("MOR_REPO") == "gmisner/moreopenrepeater"
    (build,) = checkout.builds()
    assert build == "docker compose up -d --build"
    seen = [line for line in checkout.log() if line.startswith("env-at-build:")]
    assert len(seen) == 1 and f"MOR_COMMIT={checkout.head()} " in seen[0]


def test_it_records_the_commit_even_when_it_starts_nothing(checkout):
    prepare(checkout)
    assert checkout.run("setup.sh", "--callsign", "W1AW", "--no-start").returncode == 0
    assert checkout.env_value("MOR_COMMIT") == checkout.head()
    assert checkout.builds() == []


def test_setup_never_pulls_or_switches_anything(checkout):
    prepare(checkout)
    checkout.push_from_elsewhere()
    before = checkout.head()
    checkout.run("setup.sh", "--callsign", "W1AW")
    assert checkout.head() == before
    assert checkout.index("git pull") is None and checkout.index("git fetch") is None and checkout.index("git checkout") is None


def test_running_it_again_keeps_one_current_set_of_lines(checkout):
    prepare(checkout)
    checkout.run("setup.sh", "--callsign", "W1AW")
    first = (checkout.docker / ".env").read_text()
    again = checkout.run("setup.sh", "--callsign", "W1AW")
    assert again.returncode == 0
    assert (checkout.docker / ".env").read_text() == first
    assert first.count("MOR_COMMIT=") == 1


def test_a_copy_that_is_not_a_git_checkout_installs_anyway_and_says_the_version_is_not_noted(checkout):
    shutil.rmtree(checkout.work / ".git")
    prepare(checkout)
    result = checkout.run("setup.sh", "--callsign", "W1AW")
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_COMMIT") is None
    assert "couldn't note which version this is" in result.stdout
    assert len(checkout.builds()) == 1  # the install went ahead; the page will say unknown
