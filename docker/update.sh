#!/bin/sh
# Updates the container install: pull the new code, note which commit it is, rebuild and restart.
#
#   cd docker && ./update.sh
#
# The dashboard's Updates page can't do this for you: the dashboard container has no Docker access and no git, on
# purpose. So it checks GitHub and shows this command, and this script does the work on the host.
#
# In order, and it stops at the first thing that is wrong (nothing is rebuilt after a problem):
#   1. refuses if this checkout has changes of its own, or isn't on a branch that follows origin
#   2. git pull --ff-only: only ever moves forward, never merges or rewrites. If origin and this checkout have each
#      moved on, it stops with nothing changed.
#   3. writes the commit, its date, the branch and the GitHub repo into .env (MOR_COMMIT and friends), which is what
#      the API image is built with. This is what the Updates page shows as the running version.
#   4. docker compose up -d --build, adding the profiles (allstar, dmr, winlink) of whichever of those containers you
#      have, so they are rebuilt with the rest. The repeater is off the air while the core restarts.
#
# To follow another channel, switch the checkout's branch first (main is dev, beta, stable) and run this:
#   git checkout stable && ./update.sh
#
#   --record-only   only write step 3's lines into .env (setup.sh does this on a first install). Pulls and builds nothing.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

RECORD_ONLY=0
case "${1:-}" in
  "") ;;
  --record-only) RECORD_ONLY=1 ;;
  -h|--help) sed -n '2,21p' "$HERE/update.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "update: unknown option $1 (see --help)" >&2; exit 2 ;;
esac

say() { printf '%s\n' "$*"; }
die() { printf 'update: %s\n' "$*" >&2; exit 2; }

# set_env NAME VALUE: put NAME=VALUE in .env, replacing any earlier line for NAME. An empty VALUE removes the line, so
# "not known" is simply absent. A line that is already right is left alone, so running this twice changes nothing.
set_env() {
  if [ -z "$2" ]; then
    grep -q "^$1=" .env 2>/dev/null || return 0
  else
    grep -qxF "$1=$2" .env 2>/dev/null && return 0
  fi
  {
    grep -v "^$1=" .env || true
    [ -z "$2" ] || printf '%s=%s\n' "$1" "$2"
  } > .env.new
  cat .env.new > .env  # in place, so .env keeps its permissions
  rm -f .env.new
}

# record_version: what the API image is built with. No git inside the image and no host path in the container: the
# commit travels as a build argument (compose.yaml, Dockerfile.api), and this is where it comes from.
record_version() {
  commit="$(git rev-parse HEAD)"
  date="$(git log -1 --format=%cI HEAD)"
  # Only a branch name that is safe to put in .env and in an image: a detached HEAD or an odd name is "not known".
  branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  printf '%s' "$branch" | grep -Eq '^[A-Za-z0-9._/-]{1,100}$' || branch=""
  # owner/name of the GitHub remote, never the URL itself: a URL can carry a password or token.
  url="$(git config --get remote.origin.url 2>/dev/null || true)"
  url="${url%/}"
  url="${url%.git}"
  repo="$(printf '%s' "$url" | sed -n -E 's#^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$#\2#p')"
  [ -f .env ] || { : > .env; chmod 600 .env; }
  set_env MOR_COMMIT "$commit"
  set_env MOR_COMMIT_DATE "$date"
  set_env MOR_BRANCH "$branch"
  set_env MOR_REPO "$repo"
}

# profiles: the --profile flags for the optional containers that exist here, found by Docker's own labels on this
# folder's containers. A plain `up` leaves profiled services alone, so without this they would keep their old image.
# (If you set COMPOSE_PROFILES yourself, Compose reads that as well.)
profiles() {
  found="$(docker ps -a --filter "label=com.docker.compose.project.working_dir=$HERE" --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null || true)"
  out=""
  for service in $found; do
    case "$service" in
      allstar) profile=allstar ;;
      dmr) profile=dmr ;;
      direwolf|winlink) profile=winlink ;;
      *) continue ;;
    esac
    case " $out " in *" --profile $profile "*) ;; *) out="$out --profile $profile" ;; esac
  done
  printf '%s' "$out"
}

# The whole script is one function that ends in `exit`, so the shell has read all of it before the pull below can
# replace this very file with a newer one.
main() {
  command -v git >/dev/null 2>&1 || die "git is not installed here."
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "this folder is not a git checkout, so there is nothing to pull. Get a fresh copy with git clone, then run setup.sh there."

  if [ "$RECORD_ONLY" = 1 ]; then
    record_version
    return 0
  fi

  command -v docker >/dev/null 2>&1 || die "docker is not installed. See https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 || die "'docker compose' is not available (the Docker Compose plugin is needed)."

  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "this checkout has changes of its own (git status shows them). Commit or stash them, then run this again. Nothing was changed."
  fi
  git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 \
    || die "this checkout is not on a branch that follows origin, so there is nothing to pull. Pick a channel first: git checkout main (dev), beta or stable. Nothing was changed."

  say "Pulling the new code"
  if ! git pull --ff-only; then
    die "couldn't move this checkout forward (git's message is above). Either origin can't be reached, or this checkout has commits that origin doesn't have, so it can't simply fast-forward. Nothing was changed and no container was touched."
  fi

  record_version
  say "  this is $(git rev-parse --short HEAD) on $(git symbolic-ref --quiet --short HEAD)"

  PROFILE="$(profiles)"
  say "Rebuilding and restarting the containers (the repeater is off the air for a minute or two)"
  # shellcheck disable=SC2086
  docker compose $PROFILE up -d --build
  say "Done. The dashboard's Updates page shows the new version once it is back."
}

main
exit
