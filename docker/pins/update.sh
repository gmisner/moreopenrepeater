#!/bin/sh
# Writes the pinned requirement files the images install, from the ranges in pins/*.in and pyproject.toml.
#
#   cd docker && ./pins/update.sh                        re-resolve and keep every version that is already pinned (adds what is new)
#   cd docker && ./pins/update.sh --upgrade              move everything to the newest versions that fit the ranges
#   cd docker && ./pins/update.sh --upgrade-package numpy   move just that one (and what it needs)
#
# Needs uv (https://docs.astral.sh/uv/). It resolves for Python 3.13, the version of the images' base, for every platform at once
# (--universal), and writes every file's hash (--generate-hashes), so the images install with --require-hashes: a rebuild gets
# exactly these files or fails. --no-build refuses a package that has no wheel, because the images have no compiler.
# Nothing is bumped by this script on its own; read the diff, build the images, run the tests, and commit the files together.
#
# The files, and the images that install them:
#   core-requirements.txt        from pins/core.in    the core (Dockerfile.core)
#   core-piper-requirements.txt  from pins/piper.in   Piper inside the core (Dockerfile.core, WITH_PIPER=1)
#   logd-requirements.txt        from pins/logd.in    the log collector (Dockerfile.logd)
#   api-requirements.txt         from ../pyproject.toml's dependencies    the dashboard and API (Dockerfile.api)
set -eu

cd "$(dirname "$0")/.."
command -v uv >/dev/null 2>&1 || { echo "pins: uv is not installed (https://docs.astral.sh/uv/)" >&2; exit 2; }

compile() { # INPUT OUTPUT [more uv arguments]
  input="$1"; output="$2"; shift 2
  uv pip compile "$input" -o "$output" --python-version 3.13 --universal --generate-hashes --no-build \
    --custom-compile-command "./pins/update.sh" "$@"
}

# Each file is resolved with the previous file's pins as constraints where it must agree with them: Piper inside the core must
# not move the core's numpy.
compile pins/core.in core-requirements.txt "$@"
compile pins/logd.in logd-requirements.txt "$@"
compile ../pyproject.toml api-requirements.txt "$@"
compile pins/piper.in core-piper-requirements.txt -c core-requirements.txt "$@"
