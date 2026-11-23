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
#   4. docker compose up -d --build (or, with prebuilt images on, pull the published ones and build only the rest, then up -d),
#      with the compose files COMPOSE_FILE in .env lists (setup.sh puts the overlays you turned
#      on there: --gpio, --serial, --link-radio, --protect-sd, --host-network), adding the profiles (allstar, dmr, winlink,
#      time) of whichever of those containers you have, so they are rebuilt with the rest. The repeater is off the air while
#      the core restarts.
#
# To follow another channel, switch the checkout's branch first (main is dev, beta, stable) and run this:
#   git checkout stable && ./update.sh
#
#   --record-only   only write step 3's lines into .env (setup.sh does this on a first install). Pulls and builds nothing.
#   --registry      use the project's prebuilt images from now on (docs/docker.md, Prebuilt images): writes MOR_REGISTRY and the
#                   tag of this commit to .env. Changes nothing else: run ./update.sh after it to start them.
#   --no-registry   build the images here again (removes MOR_REGISTRY and the tags from .env). Run ./update.sh after it.
#   --rollback      prebuilt images only: go back to the images this install ran before the last update. The checkout does not
#                   move, and the next ./update.sh goes forward again.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Read before anything can replace this folder's files with a pull.
# shellcheck source=docker/images.sh
. "$HERE/images.sh"

RECORD_ONLY=0
MODE=update
case "${1:-}" in
  "") ;;
  --record-only) RECORD_ONLY=1 ;;
  --registry) MODE=registry-on ;;
  --no-registry) MODE=registry-off ;;
  --rollback) MODE=rollback ;;
  -h|--help) sed -n '2,29p' "$HERE/update.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# env_value NAME: what .env says for NAME, or nothing.
env_value() {
  sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1
}

# github_repo: owner/name of the GitHub remote, never the URL itself: a URL can carry a password or token. Nothing for any
# other remote.
github_repo() {
  url="$(git config --get remote.origin.url 2>/dev/null || true)"
  url="${url%/}"
  url="${url%.git}"
  printf '%s' "$url" | sed -n -E 's#^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$#\2#p'
}

# record_version: what the API image is built with. No git inside the image and no host path in the container: the
# commit travels as a build argument (compose.yaml, Dockerfile.api), and this is where it comes from.
record_version() {
  commit="$(git rev-parse HEAD)"
  date="$(git log -1 --format=%cI HEAD)"
  # Only a branch name that is safe to put in .env and in an image: a detached HEAD or an odd name is "not known".
  branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  printf '%s' "$branch" | grep -Eq '^[A-Za-z0-9._/-]{1,100}$' || branch=""
  repo="$(github_repo)"
  [ -f .env ] || { : > .env; chmod 600 .env; }
  set_env MOR_COMMIT "$commit"
  set_env MOR_COMMIT_DATE "$date"
  set_env MOR_BRANCH "$branch"
  set_env MOR_REPO "$repo"
  # Prebuilt images: the one made from this exact commit, so the images always match this checkout's compose files. The tag
  # that was running goes in MOR_PREVIOUS_TAG for --rollback.
  if registry_mode; then
    tag="sha-$(printf '%s' "$commit" | cut -c1-12)"
    old="$(env_value MOR_TAG)"
    if [ "$old" != "$tag" ]; then
      [ -z "$old" ] || set_env MOR_PREVIOUS_TAG "$old"
      set_env MOR_TAG "$tag"
    fi
  fi
}

# profiles: the --profile flags for the optional containers that exist here, found by Docker's own labels on this
# folder's containers. A plain `up` leaves profiled services alone, so without this they would keep their old image.
# (If you set COMPOSE_PROFILES yourself, Compose reads that as well.)
profiles() {
  found="$(docker ps -a --filter "label=com.docker.compose.project.working_dir=$HERE" --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null || true)"
  out=""
  for service in $found; do
    # shellcheck disable=SC2209  # `time` below is a profile's name, not the command
    case "$service" in
      allstar) profile=allstar ;;
      dmr) profile=dmr ;;
      direwolf|winlink) profile=winlink ;;
      chrony) profile=time ;;
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

  case "$MODE" in
    registry-on)
      repo="$(github_repo)"
      [ -n "$repo" ] || die "this checkout's remote is not a GitHub repository, so there is no registry to pull images from. Nothing was changed."
      owner="$(printf '%s' "${repo%%/*}" | tr '[:upper:]' '[:lower:]')"
      [ -f .env ] || { : > .env; chmod 600 .env; }
      set_env MOR_REGISTRY "ghcr.io/$owner/"
      record_version
      say "Prebuilt images are on: ghcr.io/$owner/, tag $(env_value MOR_TAG). Nothing was started or changed yet; run ./update.sh to use them."
      return 0
      ;;
    registry-off)
      set_env MOR_REGISTRY ""
      set_env MOR_TAG ""
      set_env MOR_PREVIOUS_TAG ""
      say "Prebuilt images are off. Nothing was started or changed yet; run ./update.sh to build the images on this machine."
      return 0
      ;;
  esac

  command -v docker >/dev/null 2>&1 || die "docker is not installed. See https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 || die "'docker compose' is not available (the Docker Compose plugin is needed)."

  if [ "$MODE" = rollback ]; then
    registry_mode || die "this install builds its images on this machine, so there are no earlier images to go back to."
    previous="$(env_value MOR_PREVIOUS_TAG)"
    [ -n "$previous" ] || die "no earlier images are recorded: this install has not been updated since it started using prebuilt images."
    current="$(env_value MOR_TAG)"
    set_env MOR_TAG "$previous"
    set_env MOR_PREVIOUS_TAG "$current"
    say "Going back to the images tagged $previous (the checkout stays where it is; the next ./update.sh goes forward again)"
    rc=0
    start_stack "$(profiles)" || rc=$?
    if [ "$rc" = 3 ]; then
      set_env MOR_TAG "$current"
      set_env MOR_PREVIOUS_TAG "$previous"
      die "couldn't pull the images tagged $previous. Nothing was changed and the containers were not touched."
    fi
    [ "$rc" = 0 ] || exit "$rc"
    say "Done. The dashboard's Updates page shows the version once it is back."
    return 0
  fi

  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "this checkout has changes of its own (git status shows them). Commit or stash them, then run this again. Nothing was changed."
  fi
  git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 \
    || die "this checkout is not on a branch that follows origin, so there is nothing to pull. Pick a channel first: git checkout main (dev), beta or stable. Nothing was changed."

  say "Pulling the new code"
  if ! git pull --ff-only; then
    die "couldn't move this checkout forward (git's message is above). Either origin can't be reached, or this checkout has commits that origin doesn't have, so it can't simply fast-forward. Nothing was changed and no container was touched."
  fi

  old_tag="$(env_value MOR_TAG)"
  old_previous="$(env_value MOR_PREVIOUS_TAG)"
  record_version
  say "  this is $(git rev-parse --short HEAD) on $(git symbolic-ref --quiet --short HEAD)"

  # An existing winlink.env keeps working: the packet modem now has its own settings file, made from it once (the modem is not
  # given the gateway's passwords).
  sh ./split-winlink-env.sh

  PROFILE="$(profiles)"
  if registry_mode; then
    say "Pulling the prebuilt images ($(env_value MOR_TAG)) and restarting the containers (the repeater is off the air for a minute or two)"
  else
    say "Rebuilding and restarting the containers (the repeater is off the air for a minute or two)"
  fi
  # No -f here, on purpose: Compose reads COMPOSE_FILE from .env, which lists the overlays setup.sh turned on (compose.gpio.yaml,
  # compose.serial.yaml, compose.link-radio.yaml, compose.protect-sd.yaml, compose.host-network.yaml). A -f would ignore it.
  rc=0
  start_stack "$PROFILE" || rc=$?
  if [ "$rc" = 3 ]; then
    # Nothing was started, so the running containers are the old version, and the tag goes back to match them.
    set_env MOR_TAG "$old_tag"
    set_env MOR_PREVIOUS_TAG "$old_previous"
    die "couldn't pull the prebuilt images for this commit. They may not be published yet (the project publishes the commits it promotes to beta and stable), or this machine can't reach ghcr.io. The code was pulled but no container was touched. To build here instead: ./update.sh --no-registry && ./update.sh"
  fi
  [ "$rc" = 0 ] || exit "$rc"
  say "Done. The dashboard's Updates page shows the new version once it is back."
}

main
exit
