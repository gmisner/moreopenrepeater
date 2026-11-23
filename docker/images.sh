# Shared by setup.sh and update.sh (sourced, never run on its own): how the containers are made and started.
#
# By default every image is built on this machine. With prebuilt images on (`./setup.sh --registry`, or `./update.sh --registry`
# on an install that exists), .env has MOR_REGISTRY and MOR_TAG, and the images the project publishes are pulled instead. The
# services it does not publish (docs/docker.md, Prebuilt images) are still built here.

# The services the images workflow publishes (.github/workflows/images.yml). A test keeps this list the same as that one.
PUBLISHED_SERVICES="core api logd piper chrony"

# registry_mode: true when this install pulls prebuilt images.
registry_mode() {
  grep -q '^MOR_REGISTRY=.' .env 2>/dev/null
}

# start_stack PROFILE_FLAGS: make the images and start the containers.
# Returns 3 when the prebuilt images could not be pulled (a commit with no published images, or no network). Nothing was
# started or changed then, so the running containers are as they were.
start_stack() {
  stack_profile="$1"
  if ! registry_mode; then
    # shellcheck disable=SC2086
    docker compose $stack_profile up -d --build
    return
  fi
  # shellcheck disable=SC2086
  services="$(docker compose $stack_profile config --services)" || return 1
  pull=""
  build=""
  for service in $services; do
    case " $PUBLISHED_SERVICES " in
      *" $service "*) pull="$pull $service" ;;
      *) build="$build $service" ;;
    esac
  done
  # shellcheck disable=SC2086
  docker compose $stack_profile pull $pull || return 3
  # shellcheck disable=SC2086
  [ -z "$build" ] || docker compose $stack_profile build $build || return 1
  # No --build: that would build the pulled services here too and use those instead.
  # shellcheck disable=SC2086
  docker compose $stack_profile up -d
}
