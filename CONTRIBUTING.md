# Contributing to moreopenrepeater

Thanks for helping. moreopenrepeater runs real transmitters, so changes are
kept small and well tested. Bug reports from people running it on the air are
just as valuable as code.

## Reporting bugs and asking for features

- **Bugs:** open an issue with the bug report template. Logs from the
  dashboard's Logs page help a lot. Remove passwords, tokens, phone numbers
  and API keys first.
- **Features:** open an issue with the feature request template and describe
  what you want the repeater to do on the air, not just the setting you'd add.
- **Security problems:** don't open an issue. Report them privately as
  described in [SECURITY.md](SECURITY.md).

## Setting up

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

On Debian or Ubuntu, `sudo apt install libportaudio2 espeak-ng` first, so the
audio and text-to-speech tests run instead of skipping. To run the dashboard
locally and try live audio on a Mac, see "Development" in the
[README](README.md#development).

## Making a change

1. **One change per pull request.** A bug fix, a feature or a cleanup, not
   several at once. Small PRs get reviewed and merged faster, and are easy to
   roll back.
2. **Add tests.** Controller and audio logic have unit tests under `tests/`.
   A bug fix should come with a test that fails without it.
3. **Run the checks before pushing.** CI runs the same ones on Python 3.11
   to 3.14:
   ```
   .venv/bin/pytest -q
   ruff check --select F src tests
   shellcheck scripts/install-pi.sh scripts/update.sh   # if you changed them
   ```
4. **Update the docs.** If users would notice the change, update the page in
   `docs/` that covers it, or the README.
5. **Fill in the pull request template.** Say what changed and why, show
   evidence that it works (test output, a log, a screenshot), and say what
   could break.

## Style

- Match the code around your change: its naming, structure and comment
  density.
- Comments explain constraints the code can't show, not what the next line
  does.
- Dashboard text and docs are plain English for radio operators: say what the
  repeater does on the air.

## How changes reach repeaters

Merged pull requests land on `main`. The maintainer promotes `main` to the
`beta` channel, and `beta` to `stable` once it has run on the air for a while.
Each stable promotion publishes a GitHub release. Repeaters update from the
dashboard's Updates page on the channel they follow.

## Code of conduct

Everyone taking part is expected to follow the
[code of conduct](CODE_OF_CONDUCT.md).
