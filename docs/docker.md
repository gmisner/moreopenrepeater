# Running the core in Docker (experimental)

Stage 3 of the modular migration ([#229](https://github.com/gmisner/moreopenrepeater/issues/229)).
This runs the **core** (see [core-only.md](core-only.md)) and the **dashboard/API** as two
containers, the way [ADR 0005](adr/0005-container-module-runtime.md) plans for every install.
It is off by default, `install-pi.sh` does not use it, and the normal service is unchanged.

- `core` owns the radio, the sound card and PTT, and keeps repeating with the API stopped.
- `api` holds no device. It follows the core over the control socket (a named volume both mount),
  so it can be stopped, upgraded or crashed without the repeater noticing. When the core is down
  the dashboard says `core_unavailable` and refuses writes with a 503; it never shows a state for a
  core it cannot hear.

What works in the dashboard when the API runs apart from the core: status (live), configuration,
macros, announcements, nets, link activity, the activity history (receptions, airtime, IDs), the
backup export and restore, accounts, alerts, logs, and the pages that are not the radio. **The pages about the core's hardware show what the core
reports** (audio engine, GPIO and fan, link radio and remote base, monitor receiver, autopatch, the
AllStar node, DMR), read over the socket every couple of seconds. With the core away they say so
(a 503 `core.unavailable`) instead of showing an old state, and the status panels are left out.
**Acting on them mostly works**: switching a GPIO output, re-tuning the remote base, and placing and
ending an autopatch call are commands the core takes over the socket and checks itself (an output must
be set up as one, the transmitter must be on, the number must be allowed), and its refusal comes back
in its own words (a 409). **The SIP trunk is the API's own**: it is set up over Asterisk's AMI, a
network call, so it works from the API container. Give the API the same `MOREOPENREPEATER_AMI_*`
settings the core has (see `docker/api.env.example`); without them the page says it is not
configured. **AllStar linking works**: the API runs the link service (the AMI connection to app_rpt), which tells the core who is linked and keying and carries a macro's link commands back (see `core-only.md`). **The AllStar node and DMR setup pages work too** once the node's and the bridge's containers are running: the API edits their
files in volumes it shares with them, and tells the core where they are (see the AllStar and DMR containers below). The rest does not yet:
any audio tool that needs the sound card itself still answers 503 `core.hardware`; listening and the record and score tool work (see Listening below). **The sound card list is the core's.** The API image has no audio libraries, and a card that a running stream already holds cannot be
looked at again by a second process (a one-off check inside the core's container finds fewer cards than the core
does), so the Audio page's picker shows the list the core made when it started, read over the socket like the other hardware
parts. With the core away the picker says so (503) instead of showing an empty list. A card plugged in later shows once the core restarts. **The levels are the core's too.** The Audio page's sliders and the board presets run `amixer`, which the API image does not have and
could not use (it holds no sound card). The API asks the core to run it (`mixer_run` over the socket) and shows what `amixer`
printed. The core accepts only the two forms the dashboard uses, reading a card's controls and setting one control, so the
command cannot be made to run anything else. Saved levels are still applied by the core when its audio starts. The weather
macro (a caller dialling for the weather) is worded by the API, which does the NWS polling, and said by
the core; with the API away the caller hears that weather is not available.

Files:

- `docker/Dockerfile.api`: the same base image, the project's own dependency list (from `pyproject.toml`, pinned in
  `docker/api-requirements.txt`), the app and the built web assets. No audio libraries.
- `docker/Dockerfile.core`: `python:3.13-slim-trixie` (pinned by digest, so one
  line builds for amd64 and arm64), the core's four Python packages (pinned, with their dependencies),
  `espeak-ng` and `libportaudio2` (Piper is `docker/Dockerfile.piper`, its own container).
- `docker/Dockerfile.chrony` and `docker/chrony-supervisor.py`: the optional time container (see **The time container**).
- `docker/compose.yaml`: the services, their devices, volumes and the private network.
- `docker/setup.sh`: the install (see **Install**).
- `docker/update.sh`: the update (see **Updating**).
- `docker/core-requirements.txt`: what the core imports. No FastAPI, uvicorn or mcp. Like `logd-requirements.txt`,
  `api-requirements.txt` and `core-piper-requirements.txt`, it is exact versions with hashes (see **Updating the pinned
  Python packages**).

## Install

**Already running the native install on this machine?** Do not start with the commands below: they make a new, empty
install and would leave your saved configuration, accounts and history behind. Use
[Moving from the native install](#moving-from-the-native-install) (`docker/migrate-from-native.sh`, issue
[#233](https://github.com/gmisner/moreopenrepeater/issues/233)). It starts with a dry run that changes nothing. The
rest of this section is for a machine with no native install.

```
sudo systemctl disable --now moreopenrepeater   # only if the native install is on this machine
cd docker
./setup.sh --callsign W1AW                      # add --allstar and --dmr to run those too, --time for the clock (see The time container),
                                                # --udev to name the PTT interface,
                                                # --import-allstar /etc/asterisk to bring an existing ASL3 node over (docs/allstar.md)
```

`setup.sh` does what is missing and nothing else, so it is safe to run again (it never changes a value you have set):
it writes `.env`, `api.env` and `core.env` from the `.example` files (readable by you only), seeds the saved
configuration (the core will not run without a callsign; everything else is set in the dashboard), **turns sign-in on
with an admin password it generates and shows once**, publishes the dashboard on every network interface, starts the
containers and waits for them to be healthy. Then the dashboard answers on all three of:

- `http://localhost:8000` on the machine itself,
- `http://<the machine's LAN address>:8000` from the rest of your network, and
- `http://<its Tailscale address>:8000` over Tailscale (the address `tailscale ip -4` shows). It is already carried over
  WireGuard; for HTTPS on the tailnet, `tailscale serve --bg 8000` on the host adds that.

**Why sign-in comes first.** Without an account the dashboard is open and can key the transmitter, so it must not
answer on the network until sign-in is on. The script turns sign-in on (`MOREOPENREPEATER_AUTH_USER` and
`MOREOPENREPEATER_AUTH_PASSWORD` in `api.env`; the app refuses to start with only one) and only then sets
`MOR_API_HOST=0.0.0.0`. A login you already set is kept, an address you already chose in `.env` is kept, and
`--localhost` keeps the dashboard on the loopback for a reverse proxy or `tailscale serve` on the same machine. With no
script, `docker compose up` publishes the dashboard on the loopback only (see **The private network**), which is the safe
default. Everything behind the dashboard stays private either way: the core, the AllStar node's management port, the radio
audio and the log collector are only on the services network.

**Time zone.** The containers run on UTC unless they are told a zone, and a repeater on UTC says the wrong hour in its
announcements and runs scheduled things (announcements, nets, link and weather schedules) at the wrong time of day. The
script writes the machine's own zone to `.env` as `MOR_TZ` (from `timedatectl`, else `/etc/timezone`, else the
`/etc/localtime` link; nothing is written if it can't tell, and a `MOR_TZ` you set is kept), and compose passes it to the
core, the dashboard and the AllStar node as `TZ`. The dashboard's **Date and time** page sets the station's zone for the
core and the dashboard from then on, whatever the host says, and shows the time in that zone as you pick it. An empty
choice there means the `MOR_TZ` default. The AllStar node keeps the `MOR_TZ` default (its own clock is not reached by the
dashboard). Times on other dashboard pages are shown in your browser's own zone. The images carry their own zone data
(the Debian base image's `tzdata`), so no zone needs installing on the host.

By hand, without the script:

```
cd docker
cp .env.example .env        # then edit: PTT device, data folder (gpio, serial and a link radio: see GPIO, serial ports and a link radio)
docker compose up -d --build
docker compose exec core python -m core.healthcheck
```

The core records the activity history (receptions, airtime, IDs, kerchunks) in `activity.db` in
the data folder, because it is the one that sees every transmission, and keeps recording with the
API stopped. If you copy an installed Pi's data, copy `activity.db` too and the history carries over.
The dashboard reads it from the core. Two things follow: a backup made while the API runs apart from
the core holds an empty history file (the history stays in the core's data folder), and restoring a
backup does not put history back.

`MOR_DATA_DIR` must hold a `state.json` (the saved configuration). The core
refuses to start without one and exits with status 2, so Compose will not
restart-loop it. To start from an installed Pi's configuration, use the migration script (below), which does this and
more. By hand, copy it and give it to uid 999:

```
sudo mkdir -p /srv/mor-data && sudo cp /opt/moreopenrepeater/data/state.json /srv/mor-data/
sudo chown -R 999:999 /srv/mor-data
echo MOR_DATA_DIR=/srv/mor-data >> .env
```

## Moving from the native install

For an owner who already runs the native install (`scripts/install-pi.sh`, the `moreopenrepeater` service, code in
`/opt/moreopenrepeater`, settings in `/etc/moreopenrepeater/env`) and wants the container install on the same machine.
Issue [#233](https://github.com/gmisner/moreopenrepeater/issues/233). Both installs keep the same data folder format, so the
data comes over as it is. `docker/migrate-from-native.sh` does the moving, and it is careful about it:

- It is a **dry run unless you pass `--apply`**. The dry run says what it found and what it would do, and changes nothing
  (it does ask for your sudo password if it has to read files that belong to the `moreopenrepeater` user).
- The native data is **only ever read**. Nothing is moved or deleted, so going back is just stopping the containers and
  starting the native service.
- It stops the native service itself, only with `--apply`, and prints the command that undoes that.

```
cd docker
./migrate-from-native.sh            # 1. dry run: read what it found, check the plan, nothing changes
./migrate-from-native.sh --apply    # 2. do it (the repeater goes off the air while the containers build)
```

Run it as yourself, not with `sudo`: it uses sudo where it must (reading the native files, stopping the service, writing
files the container owns) and tells you so. It needs Docker and the Compose plugin installed already, and python3 (which the
native install has).

**What it refuses, changing nothing:** Docker is missing or can't be asked, the container stack in this folder is already
running, the native data folder has no `state.json` with a callsign, a native process is running outside systemd, or the
target data folder (`MOR_DATA_DIR` in `docker/.env`, else `docker/data`, or `--target DIR`) is not empty. With
`--into-existing` a target that already holds files is allowed, and the script never overwrites a file in it unless you
also pass `--force` (the old file is then kept as `NAME.before-migrate`).

**What carries over, and what does not:**

| | Carried | How |
| --- | --- | --- |
| Saved configuration, macros, announcements | yes | `state.json` |
| Dashboard accounts, API tokens, agent access | yes | `users.json`, `tokens.json`, `agent-access.json` (mode 0600) |
| Audio clips | yes | `audio/` with its `index.json` |
| Activity history, audit log, APRS and sensor history | yes | `activity.db`, `audit.db`, `aprs.db`, `sensors.db`, copied with SQLite's backup API (a consistent copy, the `-wal` file folded in), then integrity checked |
| Hub registry and the other small settings files | yes | `hub-sites.json` (0600), `alerts.json`, `nets.json`, `control-codes.json`, `stream.json`, `log-shipping.json`, `mailbox/` |
| Saved transmissions | only with `--recordings` | `recordings/`, `monitor-recordings/` (they add up) |
| Dashboard login (user and password) | yes | `api.env`, file to file, never printed. `setup.sh` keeps a login that is already there, so your admin login keeps working |
| Dashboard port, loopback-only host | yes | `.env` (`MOR_API_PORT`, `MOR_API_HOST=127.0.0.1`) |
| Stall seconds, Home Assistant token, transcription key | yes | `core.env` and `api.env` |
| AMI settings pointing at a remote Asterisk | yes | both `api.env` and `core.env` |
| AMI settings pointing at this machine's Asterisk | no | in the container install the node is its own container with its own login |
| Piper and Kokoro voices, speech models | no | the container install has its own Piper container |
| The native install's backups, update schedule, process marker | no | native-only; the backups stay where they are as your safety copy |
| The PTT udev rule | not by this script | it passes `--udev` to `setup.sh` when the rule is not installed |
| AllStar node on the host | **no** | printed as exact next commands (below) |
| DMR (DVSwitch) on the host | **no, there is no importer** | by hand (below) |
| SD card protection (journal and log in memory) | no | the container counterpart is `compose.protect-sd.yaml` (see Sparing the SD card) |
| Tailscale | stays on the host | the dashboard reads its socket read-only |

Env keys the script does not recognize are **listed, not guessed**. Values are never printed, not even in the dry run: it says
`(secret)`. Files in the data folder that it does not know are listed as left behind, with the reason.

**What `--apply` does, in order:** the checks above; reading everything and working out the whole plan; stopping the native
service (`sudo systemctl disable --now moreopenrepeater`, and `moreopenrepeater-core` if that is the one in use); copying the
data (as uid 999; symbolic links in the native data folder are skipped, not followed; a failed copy removes what it wrote);
writing `api.env`, `core.env` and `.env`, keeping any value already set; then running `./setup.sh`. `setup.sh` writes the rest,
leaves the copied `state.json` alone (it only seeds one that is missing), keeps the login that was copied (or, if the native
install had none, turns sign-in on with a generated password it prints once), and starts the containers. The flags it gets are
only the ones that are safe to run for you: `--udev` when the container's PTT name rule is missing, `--gpio` and `--serial DEVICE`
when the saved configuration uses them and the device exists. With a link radio in the configuration it passes `--no-start` and
tells you to run `./setup.sh --link-radio ID` (which starts the containers), since the interface needs its own name first.
`--no-setup` stops after the copy and prints the `setup.sh` command instead.

**AllStar on the host.** The script does not touch it. The container install runs its own node, and two nodes with one number
and the same IAX port must not run together. To bring the node over, stop the host's (`sudo systemctl disable --now asterisk`),
run `./setup.sh --import-allstar /etc/asterisk`, and click **Import node** on the dashboard's AllStarLink page (see
[allstar.md](allstar.md), Importing a node from an existing ASL3 install). Until then the repeater runs with no AllStar link.

**DMR on the host (DVSwitch).** There is no importer. The script says it found one and does not read its settings. To move
it: look up the callsign, DMR Id, network and password in `/etc/moreopenrepeater/dmr/*.ini`, stop the host's bridge
(`sudo systemctl disable --now analog_bridge mmdvm_bridge`; undo with `enable --now`), run `./setup.sh --dmr`, and enter them again
on the dashboard's DMR page.

**Going back.** Because the native data was only read, this is the whole rollback:

```
cd docker && docker compose down            # no -v: that would delete the containers' volumes
                                            # (name --profile allstar, dmr, winlink and time as well if you started them)
sudo systemctl enable --now moreopenrepeater   # moreopenrepeater-core if that was the one you ran
```

The native version ignores `state.json` fields it does not know (it drops them when it loads, `src/api/service/snapshot.py`), and it
reads its own folder, not the container's, so the container's newer fields cannot hurt it. What you change in the container's
dashboard stays in the container's data folder and does not appear in the native install. To carry a change back, use the
dashboard's backup export in the container install and restore it in the native one (the backup format works across both).
Keep the native install until the container one has run for a while.

**What the script does not and can not check.** It never keys a transmitter or hears the radio. After `--apply`, on the air,
yourself: the dashboard answers and shows the right callsign; `ls -l /dev/moreopenrepeater-ptt` exists; keying from a radio keys,
repeats and drops, with the courtesy tone and hang time you expect; the identification comes on time and sounds right; the audio
levels match what you had; and the time-out timer, GPIO or serial lines, a link radio, AllStar and DMR if you use them. The tests
for the script (`tests/container_migrate/`) run it against a fake native install with Docker, systemd and sudo stood in by shims.
They prove what it does with files and commands. They do not prove anything about a Pi, real Docker, hardware or the air.

## Updating

```
cd docker
./update.sh
```

`update.sh` pulls the new code, notes which commit it is, then rebuilds and restarts the containers (the repeater is off
the air for a minute or two while the core restarts). It stops with a plain message, and changes nothing, if

- the checkout has changes of its own (commit or stash them first),
- the checkout is not on a branch that follows `origin` (a detached checkout), or
- `origin` can't be reached, or has moved one way while this checkout moved another, so it can't simply fast-forward.
  It only ever runs `git pull --ff-only`: it never merges or rewrites anything.

It also rebuilds the optional containers you have (AllStar, DMR, Winlink, the time container), which a plain `docker compose up` would leave
on their old image.

With [prebuilt images](#prebuilt-images) on, it pulls the images instead of building them (same stop-before-touching-anything rule if they
are not there), and `./update.sh --rollback` goes back to the previous ones.

**Channels.** The container install follows the branch its checkout is on: `main` is the dev channel, `beta` is beta,
`stable` is stable (the same three as the native install, see `docs/raspberry-pi.md`). To change channel, switch the
branch and update: `git checkout stable && ./update.sh`.

**Updating the pinned Python packages.** The core, the log collector and the dashboard images install exact versions of
every Python package, with a hash for each file, from `docker/core-requirements.txt`, `core-piper-requirements.txt` (Piper
inside the core), `logd-requirements.txt` and `api-requirements.txt`. The images install them with `pip --require-hashes`,
so a rebuild gets exactly those files or fails: `./update.sh` cannot pull a new major of numpy, pydantic or sounddevice
into the core by itself. (The base image is pinned by digest and the other downloads by commit or checksum in the same way.)
The files are written, not edited, from the ranges in `docker/pins/*.in` and from `pyproject.toml`, by a script that needs
[uv](https://docs.astral.sh/uv/):

```
cd docker
./pins/update.sh                           # same versions, adds anything new
./pins/update.sh --upgrade-package numpy   # move one package (and what it needs)
./pins/update.sh --upgrade                 # move everything that the ranges allow
```

Read the diff, build the images (`docker compose build`), run the tests, and commit the changed files together. The hashes
cover every file PyPI has for a version, including the Linux amd64 and arm64 wheels the Pi and a PC use; a package with no
wheel for them is refused, because the images have no compiler. Change a range (in `pyproject.toml` for the dashboard, in
`docker/pins/*.in` for the others) and run the script when a new dependency is needed. `python -m pip install -e .` for
development is not pinned.

**The Updates page.** The dashboard can show what is running and whether GitHub has newer commits on the branch you
follow, but it cannot update anything: it is a separate container with no access to Docker or the host, on purpose, and
there is no git in its image. So the Updates page in a container install has no Update button. It shows the running
commit, any newer commits, and the commands above to copy.

**Where the running version comes from.** The API image is built with the commit as a build argument. `update.sh`
and `setup.sh` write `MOR_COMMIT`, `MOR_COMMIT_DATE`, `MOR_BRANCH` and `MOR_REPO` (the owner and name of the GitHub
remote, never its URL) into `docker/.env`, and `compose.yaml` passes them to `Dockerfile.api`, which sets them as
environment variables for the app. Nothing is mounted from the host. The recorded commit is the checkout's `HEAD` at the
time the script ran, so if you update with plain `git pull` and `docker compose up -d --build`, the page keeps showing
the older commit until you run `./update.sh` once. An image built with nothing recorded shows the version as unknown
and says to run `./update.sh`.

## Prebuilt images

Building the images on the repeater's own computer is slow on a Pi, so the project can publish them ready-made
([#367](https://github.com/gmisner/moreopenrepeater/issues/367)). The **images** workflow
(`.github/workflows/images.yml`) builds each one for amd64 and arm64 on GitHub's servers and puts it in GitHub's container
registry as `ghcr.io/gmisner/moreopenrepeater-<service>`.

- Each commit's images are tagged `sha-` and the first 12 characters of the commit. A moving tag (`beta`, `stable`) or a release tag is added to
  those same images, never to a rebuild, so what reaches `stable` is what ran on `beta`.
- **Promoting publishes them.** The **promote** workflow (`.github/workflows/promote.yml`) publishes the commit's images before it
  moves `beta` or `stable`, so a channel never points at a commit with no images. Moving to `beta` builds them and tags them
  `beta`. Moving to `stable` finds them already built and adds `stable` and the release tag (`v2026.11.23`). If the images fail,
  the channel has not moved: run it again.
- A pull request builds the images and publishes nothing, so a broken Dockerfile shows up before a change is promoted.
- Only commits on `main` whose tests passed can be published.
- **Published:** `core`, `api`, `logd`, `piper` and `chrony`. **Not published:** `winlink` (upstream LinBPQ has no license file,
  [#344](https://github.com/gmisner/moreopenrepeater/issues/344)), and `dmr`, `allstar` and `direwolf` until their packages'
  licenses have been checked. Those keep building on the machine that runs them.

### Using them

On a new install: `./setup.sh --registry` (with your other flags). On an install that exists: `./update.sh --registry`, then
`./update.sh`. This needs a git clone of the project from GitHub, because the compose files and scripts still come from it.

- It writes `MOR_REGISTRY` (`ghcr.io/<owner>/`) and `MOR_TAG` to `.env`. The tag is `sha-` and the first 12 characters of the
  checkout's commit, so the images always match the compose files next to them. `MOR_PREVIOUS_TAG` is the tag the last update
  replaced.
- `./update.sh` then pulls the code as before, pulls the images for that commit, builds only the containers that are not
  published (AllStar, DMR, Winlink, Dire Wolf), and starts everything. It never runs `up --build`, which would build the
  published ones here as well.
- **A commit with no images:** the project publishes the commits it promotes to beta and stable, not every commit on `main`. If
  the images for your commit are not there (or this machine cannot reach `ghcr.io`), the update stops before it touches a
  container, puts the tag back, and says so. The code has been pulled. Wait for the next promotion, or build here
  with `./update.sh --no-registry && ./update.sh`.
- **Going back:** `./update.sh --rollback` restarts the containers on the images they ran before the last update. The checkout
  stays where it is, so the next `./update.sh` goes forward again, and running `--rollback` twice swaps back. It is for a release
  that starts badly. If the compose files changed in the same release, a rollback may not suit the older images.
- **The channel:** the dashboard's Updates page shows the commit baked into the image and the channel from `.env`
  (the branch the checkout follows). One image goes from beta to stable, so the channel is not part of it.
- `./update.sh --no-registry` goes back to building here.

## What the container is allowed to do

- Runs as uid 999 (the uid of `install-pi.sh`'s `moreopenrepeater` user on a Pi), with a
  read-only root, no capabilities and `no-new-privileges`. Docker's default seccomp
  filter is off (see Measured on a Pi).
- Gets `/dev/snd` and one `hidraw` node, by the name `/dev/moreopenrepeater-ptt` (see **A stable name for
  the PTT device**; `MOR_PTT_HIDRAW` overrides it with a `/dev/hidrawN` path).
  The group numbers for `audio` and `dialout` are the same on Debian and Pi OS;
  the `gpio` group number differs, so it is `MOR_GPIO_GID`. The Pi's GPIO chip, serial adapters and a link radio's
  second interface are only given when you ask (see **GPIO, serial ports and a link radio**).
- Writes only to `/data` and the control socket volume.
- **Reads the host's kernel log, read-only.** The core is given `/dev/kmsg` (`MOR_KMSG_DEVICE` in `.env`, which `setup.sh` sets to
  `/dev/kmsg` when the host has it and you can read it). That is what lets the support bundle show a USB drop, which only the kernel
  log records (`disabled by hub (EMI?)`). What this widens: the core, and anything that gets code running inside it, can read
  everything the host's kernel has logged since boot, which is mostly hardware and driver messages: device names and serial
  numbers, network interface names and MAC addresses, and mounts. It cannot write to it, and it gets
  nothing else of the host. The device is mapped read-only (`:r`), and the core runs as uid 999 with no capabilities, so it
  could not read the log if the host had `kernel.dmesg_restrict=1`; it then says so in the bundle. The core reads it only
  when the dashboard asks for a support bundle, never on a timer, and does not keep it. To turn it off, set
  `MOR_KMSG_DEVICE=/dev/null` in `.env` and run `docker compose up -d` (the core is recreated); the core sees the null
  device and reports "not enabled". An install that was set up before this was added gets it by running `./setup.sh` again,
  or by adding `MOR_KMSG_DEVICE=/dev/kmsg` to `.env` yourself.
- The API also sees the host's Tailscale socket folder, read-only (see **Tailscale**). It holds no Tailscale command and
  cannot change Tailscale.
- The optional time container is the one exception to "no capabilities": it is given `CAP_SYS_TIME` (and nothing else), which lets it set
  the host's clock (see **The time container**, The privilege it needs). It is off unless you start it.
- Lives on the private `services` network, not the host's (see **The private network**). Nothing is published
  from the core. The old host-network layout is `compose.host-network.yaml`.

## Tailscale

The Remote access page shows whether the host runs Tailscale: its state, its name on the tailnet and whether Tailscale
Serve is on. The API image has no `tailscale` command and must not get one, so the page reads the same two facts over
tailscaled's local socket instead (`status` and `serve-config` on the LocalAPI, GET only). Nothing on that page can change
Tailscale in a container install: install, join, leave and the Serve switch are done on the host (`sudo tailscale up`,
`tailscale serve --bg 8000`). The native install, where the CLI exists, is unchanged and still uses it.

How the socket gets to the API:

- `compose.yaml` mounts `${MOR_TAILSCALE_DIR}` at `/var/run/tailscale`, read-only, in the `api` service only. It mounts the
  folder rather than the socket file because tailscaled makes a new socket when it restarts, and a file mount would keep
  pointing at the old one.
- `MOR_TAILSCALE_DIR` defaults to `docker/empty/`, an empty folder in this repo. So `docker compose up` works on a machine
  with no Tailscale, and Docker never creates a root-owned `/var/run/tailscale` on the host (it does that for a bind source
  that does not exist).
- `setup.sh` writes `MOR_TAILSCALE_DIR=/var/run/tailscale` into `.env` when `/var/run/tailscale/tailscaled.sock` exists, and
  leaves any value you already set alone. By hand, put that line in `.env` and run `docker compose up -d`. If you install
  Tailscale after setup, do the same.
- The API looks for `/var/run/tailscale/tailscaled.sock` (`MOREOPENREPEATER_TAILSCALE_SOCKET`).

If the page can't read Tailscale it says which of these it is, not "not installed":

| The page says | It means | Fix |
| --- | --- | --- |
| there is no Tailscale socket | Tailscale isn't running on the host, or the container was started without the folder | start Tailscale, or set `MOR_TAILSCALE_DIR` and `docker compose up -d` |
| the socket is there but this dashboard isn't allowed to open it | permission denied | see below |
| the socket is there but nothing is answering | tailscaled is stopped, and left its socket file behind | start Tailscale on the host |
| Tailscale didn't answer within a few seconds | tailscaled is stuck or very busy | check `tailscale status` on the host |

**Permissions.** The container runs as uid 999, which has no special place on the host. Tailscale's own defaults are enough:
on Linux, tailscaled makes the socket readable and writable by everyone (`srw-rw-rw-`) in `/var/run/tailscale` (mode 0755),
and it decides what a client may do from who is on the other end: anyone who can open the socket may read, and only root or
the configured operator may change anything. Check yours with `ls -ld /var/run/tailscale; ls -l /var/run/tailscale/tailscaled.sock`.
If the socket or its folder is tighter than that (someone ran tailscaled with another `--socket` mode, or hardened the
folder), the page says permission denied. Nothing here loosens it for you: either put it back to Tailscale's default, or accept
that the page can't see Tailscale. Two cautions: the `:ro` on the mount protects the folder's files, not the socket
(a client that can connect can send any request), so what keeps the dashboard from changing Tailscale is that uid 999 is
neither root nor the operator, and the dashboard only sends GET requests. Don't make uid 999's host user the operator
(`tailscale set --operator=`) unless you want that.

## Logs

Three places, from the quickest to the most durable:

- **`docker compose logs -f`** (from `docker/`) follows the core and the API together, time-ordered
  by Docker. Both write one structured event per line, as JSON, to standard output
  (`MOREOPENREPEATER_LOG_FORMAT=json`, set in `compose.yaml`), and the `local` log driver rotates them
  (10 MB, 3 files each). `docker compose logs -f core` for the radio alone. This works with everything
  else down. To read them as lines:
  `docker compose logs --no-log-prefix core | jq -r '[.time, .module, .event, .message] | @tsv'`.
  A line looks like `{"schema_version":1,"time":"...","boot_id":"...","seq":41,"severity":20,
  "event":"legacy.audio","module":"core","module_version":"0.1.0","message":"audio engine started ...",
  "context":{"logger":"moreopenrepeater.audio"}}`. Code that has not been given a proper event name yet
  logs as `legacy.<area>` with its text as the message (bearer tokens, `user:pass@host` and `password=x` or `token: x` style pairs masked, cut to 200
  characters, an exception recorded by its type only). Code that has something to count says so with
  `observability.log_event(logger, Severity.WARNING, "audio.glitch", count=1, state="idle")`, which has a
  stable dotted name and a context of facts that is redacted by the contract's rules (credentials,
  PINs, message and DTMF content, and exact coordinates never reach a log). `boot_id` changes on every
  restart and `seq` counts within one, so a restart can be told from lost events, and a record that
  leaves two ways (stdout and the collector) is one event with one number.
- **The dashboard's Logs page** (and its live stream, the support bundle and remote shipping) shows
  the core's lines with the API's. The API reads the core's recent log over the control socket
  (`GET /logs`, a page at a time from a cursor) about once a second and publishes the lines into its
  own log hub, with the time the core logged them. The core keeps its last 2000 lines in memory, so
  lines logged while the API was away are read when it is back, as far as that reaches; after the core
  restarts, its new log is read from the start. The core never waits on this.
- **The `logd` collector** keeps events searchable in a bounded SQLite file (with full-text search):
  14 days of routine events, 30 days of warnings and above, and never more than 64 MB. It is its own
  container (`docker compose up -d logd`) with no network at all, a read-only root, and no
  capabilities. Core and API send to it by setting `MOREOPENREPEATER_LOGD_SOCKET`
  (`/run/logd/events.sock`, a Unix datagram socket on the shared `logd-run` volume; `compose.yaml` sets it).
  That path is built never to hold up the radio: the code that logs only takes a small snapshot and puts
  it on a bounded queue (1000 events), and a separate thread builds and sends each event without waiting.
  Nothing depends on `logd` and it can be stopped, restarted or removed while the radio keeps running;
  `tests/logd/test_compose.py` fails if anyone wires it in as a dependency. A collector that is absent,
  slow or full means the event is dropped, never that anything waits. Every drop is counted, and the next
  time the collector is reachable it is told how many it missed (a `logging.dropped` event), so a gap in
  the record is reported, not hidden. The collector also notices gaps itself (a sequence number that
  skips, a process that restarted, a full queue or a failed write, events pruned for space) and keeps
  them in a separate table. To search it from a terminal:

  ```
  docker compose exec logd python -m logd.query --min-severity warning --since 2026-11-18T00:00:00Z
  docker compose exec logd python -m logd.query -q "audio glitch" --module core --limit 20
  docker compose exec logd python -m logd.query --event "audio.*" --json
  docker compose exec logd python -m logd.query --gaps    # what is known to be missing
  docker compose exec logd python -m logd.query --stats   # what it holds and what it dropped
  ```

  Other code reads the same answers over `/run/logd/query.sock` (HTTP/1.1 over a Unix socket, GET only:
  `/health`, `/stats`, `/events`, `/gaps`). Settings go in `docker/logd.env` (copy `logd.env.example`):
  `MOREOPENREPEATER_LOGD_MAX_MB`, `..._ROUTINE_DAYS`, `..._CRITICAL_DAYS`. When the size limit is reached
  the oldest routine events go first, then the oldest of the rest, and the pruning is recorded as a gap.
  The file is compacted and its write-ahead log folded in at most once an hour (and on the first prune after a
  start), not on every prune: once routine events begin to expire after about 14 days, a prune removes something
  nearly every minute, and rewriting the file that often would only wear an SD card. Freed pages are reused by
  new events and the size limit counts used pages, so the cap holds in between; while the file is over the limit
  each prune still gives freed pages back at once.
  A damaged database is set aside as `events.db.damaged-<time>` and a new one started.
  The dashboard reads this history: **Logs, then the History tab** searches it (words, level, which part, a
  time span, paged back from the newest) and shows a notice when the collector knows events are missing.
  The API finds it through `MOREOPENREPEATER_LOGD_QUERY_SOCKET` (set in `compose.yaml`); without it, or with
  logd stopped, the tab says the history is not available and the live log and log file keep working. The
  tab loads when opened and when you press Search, never on a timer. The support bundle gets
  `event-history.json`: the last 500 warnings and errors, the collector's counts, and its known gaps, or the
  reason there is none. The API serves `GET /api/log-history/status`, `/events` (same filters as `logd.query`)
  and `/gaps` to anyone signed in, as it does the live log.
- **Sparing the SD card (`--protect-sd`).** The history is a SQLite file, which means writes. On a Pi where
  the card's life matters, start the stack with the memory override and the history lives in RAM instead:

  ```
  ./setup.sh --protect-sd         # and ./setup.sh --no-protect-sd to put the history back on the card
  ```

  This adds `compose.protect-sd.yaml` to `COMPOSE_FILE` in `.env` (see **Compose files and the `-f` option**), so a later
  `./update.sh` keeps it. `/var/lib/logd` becomes a 64 MB tmpfs (the quota drops to 48 MB to fit), so the history starts afresh at
  every reboot. Errors are the exception: every event at error severity or worse is also appended to
  `critical-events.jsonl` on a small volume (`logd-critical`; the file is renamed to `.1` at 1 MB, so 2 MB is
  the most it ever holds), and the collector puts those back into an empty history when it starts. So after
  a power cut the History tab still shows what failed, and says that warnings and routine events from before
  are gone. The collector counts a failed write to that file and never lets it hold up logging. Docker's own
  container logs still go to the card, at most 30 MB per container (`max-size` in `compose.yaml`). This is
  the container counterpart of `install-pi.sh --protect-sd` for a native install, which keeps the journal and
  the controller's log in memory. It does not make the support bundle or the live log persist anything either.
- **Files in the data folder:** `moreopenrepeater.log` (the core) and `moreopenrepeater-api.log` (the
  API), each rotated at 2 MB with three backups. Separate files on purpose: two writers must not share
  one.

### Events the core names

Most of the code still logs as `legacy.<area>`. These are named, with facts you can search on, and each
keeps its readable text line in the log file exactly as before:

| Event | Severity | Facts |
| --- | --- | --- |
| `controller.state_changed` | info | `from_state`, `to_state` |
| `ptt.commanded` | info | `active`, `state`: the controller asked for the transmitter |
| `ptt.applied` | info | `active`: the audio thread wrote that to the hardware |
| `ptt.failed` | error | `action` (`key` or `release`); `error_code` `ptt.hardware` |
| `ptt.transmit_capped` | critical | `keyed_s`, `limit_s`; `error_code` `ptt.cap` |
| `ptt.release_unconfirmed` | critical | `error_code` `ptt.hardware` |
| `ptt.released_after_failures` | critical | `blocks`; `error_code` `audio.process`. The audio processor failed 5 blocks in a row while the transmitter was keyed, so the engine unkeyed it itself. It keys again if the controller still wants to transmit and processing recovers |
| `audio.glitch` | info | `counter`, `count`, `state`, `transmitting` |
| `audio.engine_started`, `audio.engine_stopped`, `audio.stream_died` | info, info, error | rates for the first; `reason` for the last: `inactive` (PortAudio said the stream stopped) or `no_audio` (it still looked active but delivered nothing for 1.5 s, which is what a USB drop usually looks like) |
| `receiver.opened` | info | `duration_s`, `open_dbfs`, `peak_dbfs`, `ctcss_hz`, `after_tx_s` (never DTMF digits) |
| `dtmf.digit_received` | info | none: that a digit arrived, never which one (it could be part of a PIN or a code) |
| `command.applied` | info | `command`: the name of a `core.command` the API sent, never its parameters |
| `command.refused` | warning | `command`; `error_code` says why (`command.simulation_disabled`, `command.unknown_clip`, `command.queue_full`, ...) |
| `command.failed` | error | `command`, `exception` (the type, never its text); `error_code` `command.apply_failed` |
| `config.changed` | info | `settings`: the names of what was changed, never the values |
| `config.refused` | warning | `error_code` says why (`config.unsafe`, `config.unknown_setting`, `config.wrong_type`, `config.conflict`) |
| `config.failed` | error | `exception`; `error_code` `config.apply_failed` |
| `speech.accepted` | info | `topic`: the core took a `speak.request` |
| `watchdog.stalled` | error | `part` (`controller`, `audio engine`, `link radio audio engine`, `sound card`), `stall_s`; `error_code` `watchdog.stalled`. The core exits right after |
| `weather.scheduled_due` | info | none: the routine weather announcement came due |
| `weather.alert_announced` | info | `alert` (the kind, such as Flood Watch), `alert_id` |
| `weather.announce_failed` | error | `kind` (`scheduled` or `alert`), `exception`; `error_code` `weather.announce`. Never the text of the alert |
| `weather.check_failed`, `weather.zone_failed` | warning | `exception` (and `zone`); `error_code` `weather.fetch` |
| `aprs.beacon_sent`, `aprs.beacon_failed` | info, error | `server`, `port` (and `exception` when it failed) |
| `link.synced` | info | `nodes`, `keyed`: the core's picture of the links was set from the link service (it connected to app_rpt, lost it, or the core restarted). Never node names |
| `link.command_dropped` | warning | a macro's link command had no link service to go to; `error_code` `link.no_service` (core) or `link.not_connected` (no connection to app_rpt) |
| `link.connected`, `link.connection_lost`, `link.drop_failed` | info, error, warning | `host`, `port`, `node`; `retry_s` and `exception` when it was lost. Never the AMI password |
| `autopatch.dial_requested` | info | none: a DTMF command asked for a call, not which number |
| `time.clock_off`, `time.clock_ok` | warning, info | `offset_ms` (and `limit_ms` for the first): the Pi's clock against the NTP server went past 2 seconds, or came back. Never the server's name |
| `time.check_failed` | warning | `reason`: `resolve`, `timeout`, `network`, `kiss`, `unsynchronized` or `bad_reply`. Logged when it changes, not on every check |
| `time.server_changed` | info | `default`: whether it went back to the default. Never the server's name |
| `<area>.loop_failed` | error | a background loop hit something unexpected and carried on. `area` is `weather`, `link`, `mailbox`, `transcripts`, `net`, `updates`, `alerts`, `sensors` or `time`; `exception` is the type; `error_code` is `<area>.unexpected` |
| `autopatch.call_started`, `.call_connected`, `.call_ended` | info | `direction` (`outgoing`/`incoming`); `via` (`DTMF` or `dashboard`) when started; `result` and `talk_s` when ended |
| `autopatch.call_refused` | info | `direction`, `via` or `reason`; the words say why (not allowed, busy, off, wrong access code) |
| `autopatch.call_failed` | error | `direction`, `exception`; `error_code` `autopatch.call` |
| `autopatch.originate_refused`, `.hangup_failed`, `.dtmf_failed`, `.lookup_failed` | error / warning | `error_code` `autopatch.asterisk` |
| `autopatch.render_failed`, `.listening`, `.listen_failed`, `.connection_ended`, `.unknown_call` | | what they say; `call_id` for the last |
| `usrp.configured` | info | `service`, `enabled`: the core pointed a service's audio at it, or stopped it |
| `winlink.status_changed` | info, warning | `enabled`, `failing`: the Winlink gateway's status changed (warning when it is on and something is failing) |
| `winlink.session_ended` | info | `duration_s`, `messages_in`, `messages_out`, `bytes_in`, `bytes_out`, `outcome` (`completed`, `ended_early` or `unknown`): a Winlink session ended. No callsign and no message text; logged when the Winlink page next reads the status (docs/winlink.md) |
| `cm108.reopened`, `cm108.gone` | info, warning | `device`: the CM108 or AIOC interface came back after a USB drop, or dropped and has not come back; the second logs once, not once per write, with `error_code` `cm108.gone` |
| `cm108.open_failed` | error | `purpose` (`ptt` or `linked_radio`), `device`, `exception`; `error_code` `cm108.open` |
| `cm108.unkey_failed` | error | `exception`; `error_code` `cm108.unkey`. Startup could not unkey the interface, so the radio may be keyed |
| `audio.open_failed` | error | `target` (`live`, `link_radio` or `monitor`), `exception`, and `recovering` for `live`; `error_code` `audio.open`. The sound card or its pins could not be opened |
| `audio.restarting` | info | `target` (`live` or `link_radio`): the audio is being reopened after the stream died |
| `audio.restart_deferred` | warning | `target` (`link_radio`): the stream died again inside the cooldown, so it waits |
| `audio.tap_failed` | error | `failures`; `error_code` `audio.tap`. The audio observer (the level meters and monitor) raised. The block was processed and keyed as normal; this logs the first failure and then one in 500 |
| `audio.block_failed` | error | `target` (`engine` or `monitor`), `exception`; `error_code` `audio.process`. One block of audio failed to process and the worker carried on |
| `cos.read_failed` | error | `target` (`engine` or `monitor`), `exception`; `error_code` `cos.hardware` |
| `station_id.replayed` | info | none: audio came back and the station ID that was cut off goes out again |
| `station_id.cw_fallback`, `station_id.empty` | warning | none; `error_code` `station_id.no_voice` (no voice clip or speech, so CW) or `station_id.empty` (no callsign set) |
| `usrp.unkey_missing` | warning | `service` (`allstar` or `dmr`); `error_code` `usrp.no_unkey`. The far end keyed up and never unkeyed, so the core unkeyed it |
| `gpio.output_changed`, `gpio.input_changed` | info | `gpio` (the number), `on`. The name you gave the pin is in the text line, not in the event |
| `gpio.setup_failed`, `gpio.read_failed` | error | `gpio` (first only), `exception`; `error_code` `gpio.hardware`. The read failure is logged once, not once per poll |
| `controller.lockout_started`, `controller.lockout_cleared` | warning, info | `timeouts`, `window_s`, `clear_after_s` for the first; `error_code` `controller.lockout`. The stuck-carrier lockout |
| `watchdog.notify_failed` | warning | `exception`; `error_code` `watchdog.notify`. systemd could not be told the service is alive |
| `auth.login_failed`, `auth.login_succeeded` | warning, info | `client` (the address) for the first; `error_code` `auth.bad_credentials`. Never the password, and never the name that was typed either (people paste their password into that box); the audit trail records the actor as `anonymous` for a failed sign-in |
| `codes.locked_out` | warning | `failures`, `lockout_min`; `error_code` `codes.locked_out`. Never a code or a secret. Logged by the API and by the core |
| `mailbox.locked` | warning | `box`, `failures`; `error_code` `mailbox.locked`. Never a PIN |
| `state.unreadable` | error | `file` (the name only), `exception`; `error_code` `state.unreadable`. A saved-state file could not be read, so it was moved aside and defaults were used |
| `backup.saved`, `backup.failed`, `backup.restored` | info, error, info | `include_recordings` and `keep` for the first; `exception` and `error_code` `backup.failed` for the second; `recordings_added` for the third |
| `tts.service_ready`, `tts.service_failed` | info, error | `engine` (`piper` or `kokoro`); `exception` and `error_code` `tts.service` for the second. The first is the Piper container answering, which can be after audio is already live (the core does not wait for it) |
| `tts.fallback_used` | warning | `engine`, `fallback` (`espeak-ng`, `cli`, ...), `exception`; `error_code` `tts.fallback`. Never the text that was being spoken |
| `tts.no_engine` | warning | none; `error_code` `tts.no_engine`. Voice IDs fall back to CW |
| `core.connection_changed` | info | `connected`: the API reached the core, or lost it. Logged when it changes, not on each retry |
| `core.socket_failed` | error | `exception`; `error_code` `core.socket`. The control socket could not start, so the core runs without it |
| `core.shutdown_step_failed` | error | `target` (`ptt_live`, `ptt_link_radio`, `gpio`, `tx_fan`, `allstar`, `dmr`, `autopatch`, `live_audio`, `monitor_receiver` or `link_radio`), `reason` (`timed_out` after 2 s, or `failed`); `error_code` `core.shutdown`. One step of shutdown went wrong. PTT is released first, so none of these leaves the transmitter keyed, and the steps after it still ran |
| `aprs.receive_failed` | warning | `server`, `port`, `exception`; `error_code` `aprs.receive`. Receiving the APRS-IS feed for the map; beacons are `aprs.beacon_sent` and `aprs.beacon_failed` |
| `alert.raised`, `alert.send_failed` | warning, error | `topic` (what it is about, such as `lockout`) and `level` (the alert's own severity) for the first; `channel`, `topic`, `exception` and `error_code` `alert.send` for the second. Never the alert's words as a fact |
| `stream.stopped` | warning | `host`, `retry_s`; `error_code` `stream.stopped`. Never the stream password |
| `link.node_requested` | info | `action` (`connect`, `disconnect` or `disconnect_all`), `node`, and `mode` (`monitor` or `transceive`) for a connect |
| `link.schedule_applied`, `link.schedule_failed` | info, warning | `action` (`link` or `unlink`), `node`; `exception` and `error_code` `link.schedule` for the second |

The autopatch events never carry a phone number (called or calling), an access code, a DTMF digit sent into
a call, a channel name (it contains the number) or speech text. The dashboard's audit trail records who
called whom; the log does not.

A fact is something you could count or filter on: a number, a state, a name from a fixed list, a device node. It is never a
secret, a PIN, a DTMF digit, the words of a message or announcement, or anything a person typed in. That is why the
sign-in events carry the address but not the password, and the GPIO events carry the pin number but not the name you gave it. The unit test
`tests/observability/test_event_table.py` fails if a name the code logs is missing from the table above, if the
table lists a name nothing logs, or if a fact would be masked by the redaction filter (a fact called `pin`, `key`,
`session` or `text` comes out as `[redacted]`; a hardware `gpio_pin`, an `audio_device` or an `error_message` does not).
The text log file, the live log stream and the console pass each line through the same masking for `secret-word=value` pairs.

What is still `legacy.<area>` is mostly routine information (a service starting or stopping, a setting being
applied, a simulated input), lines whose only useful detail is text someone typed (net and macro names, DTMF macro
patterns, announcement clips that hold spoken text), the macro and programming service, the monitor and fan
start-up problems, the AllStar and DMR setup lines, and the latency and kernel diagnostics. They still reach logd
with their text and the correlation id of the cycle they happened in.

**One cycle, one id.** When the repeater leaves idle, the core starts an activity cycle with a fresh
`correlation_id`, and every event any of its threads logs until the repeater is idle again carries it,
including plain `legacy.*` lines, and for 2 seconds after (the transmitter is unkeyed just after the
controller says idle, and that belongs to the cycle it ends; a new cycle cuts the 2 seconds short). So one transmission reads as a story: `docker compose exec logd python -m
logd.query --correlation <id>` shows the state changes, the PTT commanded by the controller, the PTT
actually applied by the audio thread (and how long after), any glitches, and the receiver opening. A `ptt.commanded`
with no `ptt.applied` after it, or a `ptt.failed`, is the controller and the hardware disagreeing. In the History tab, **Whole
activity** on any event that has an id shows that whole story in order, with time since the first event
(**Back to results** returns to the search); `GET /api/log-history/events?correlation_id=` is the same thing for scripts. That the
audio thread wrote the PTT line is not proof the transmitter keyed: an independent observation of the
radio is still not built.

## Control socket

The socket is `/run/moreopenrepeater/core.sock` in the `core-run` volume, mode
`0660` in a `0750` directory owned by uid 999. A later API container mounts the
same volume. To look at it from the host:

```
docker compose exec core python -m core.healthcheck
docker compose exec core python - <<'PY'
import socket;s=socket.socket(socket.AF_UNIX);s.connect("/run/moreopenrepeater/core.sock")
s.sendall(b"GET /status HTTP/1.1\r\nHost: core\r\nConnection: close\r\n\r\n");print(s.recv(65536).decode())
PY
```

## Listening and recording

The dashboard's live listening (the Listen page, the public page, the monitor receiver, the
stream) works with the API apart from the core. The core serves its audio on the control socket
as `GET /audio?device=live|monitor&source=rx|tx`, and the API opens one such stream for as long as
anyone is listening to that source, and none when nobody is. Every frame is a 4-byte big-endian
length and that many bytes: the first is a JSON header with the sample rate, the rest are 16-bit
little-endian mono PCM of about 100 ms each. A listener that falls behind loses audio and one that
stops reading is dropped, so listening never slows the radio. There can be at most four listeners
at a time (`MAX_AUDIO_STREAMS`). The "listeners" count the Audio page shows for the engine counts
these streams, not browsers. The Audio page's record and score tool records from the same stream
(the receiver's audio as it comes in, before the repeater's delay), so it works too.

Leaving a mailbox message goes the other way round. The mailbox (the API's) asks the core, with the
`arm_capture` command, to record the next transmission instead of repeating it, which only the core
can do. When the transmission ends the core writes the audio to a file in `captures/` in the data
folder and names it in a `capture` event. The API reads it, deletes it, and the mailbox saves it.
The API only opens files in a `captures` folder that are named `capture-` and sixteen hex digits. If
no API is there to take a recording the core deletes it, and the core clears any it finds left from
before a restart. A caller is told straight away if the core is not running live audio.

## A stable name for the PTT device

`/dev/hidrawN` is numbered in the order USB devices are found, so it changes when the sound card drops off the
bus and comes back (a loose cable, a keyboard re-enumerating, EMI from the transmitter) and sometimes at a
reboot. Docker will not start a container whose `devices:` path is missing, so a core that is told
`/dev/hidraw1` cannot start once the interface is `hidraw0`, and a core that stopped itself because its audio
stalled (see Health and restarts) could not come back. So `compose.yaml` asks for `/dev/moreopenrepeater-ptt`,
a symlink a udev rule keeps pointing at whichever `hidrawN` the AIOC (`1209:7388`) or a CM108/CM119 (`0d8c`)
is on. Docker follows the symlink each time the core starts. Install the rule once, on the host:

```
sudo cp docker/udev/61-moreopenrepeater-ptt.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw --action=add
ls -l /dev/moreopenrepeater-ptt        # points at a hidraw node
```

Without the rule the core does not start, and `docker compose up` says the device does not exist. If you
had `MOR_PTT_HIDRAW=/dev/hidraw0` in `.env`, it still works (and does not follow a renumbering): remove it
once the rule is in. Only one device can carry the name; with two interfaces on the host, narrow the rule
with `ATTRS{serial}`: for a link radio's second interface, `./setup.sh --link-radio` does that for you (see below), and
`./setup.sh --packet-radio` does it for the Winlink packet radio's CM108 or AIOC. Any second CM108 or AIOC on the machine needs
one of the two, because this rule names every one it is not told to leave out, and after a reboot the wrong one could win.
The core is given the one path as `MOREOPENREPEATER_CM108_HIDRAW`, since the default `auto` reads the host's hidraw
numbers, which are not the container's, and no link radio interface is given to it unless you ask
(`MOREOPENREPEATER_LINK_CM108_HIDRAW: "off"`).

## GPIO, serial ports and a link radio

The core is only given the sound cards and the one PTT interface by default. The rest of what the native install
can use is opt-in, because Docker will not start a container whose `devices:` path is missing and a repeater has to
come back after a power cut on a machine that may not have the hardware at all. Each of these is a flag of
`setup.sh`, run in the `docker` folder. Each one checks the device is really there, records it in `.env`, adds a small
compose file to `COMPOSE_FILE` (Compose reads that from `.env`; anything you already listed there stays), and
restarts the core. Run again with the same flag and nothing changes; a value you set in `.env` is kept. If the
device is not there, the script says so and starts nothing.

| You want | Run | What the core is given |
| --- | --- | --- |
| PTT or COS on a Pi header pin, the monitor receiver's squelch pin, a fan on a header pin | `./setup.sh --gpio` | the one header chip, for example `/dev/gpiochip0` (`compose.gpio.yaml`) |
| PTT or COS on a USB serial adapter's RTS, DTR, CTS, DSR or DCD | `./setup.sh --serial /dev/serial/by-id/NAME` | that adapter (`compose.serial.yaml`) |
| A link radio with its own AIOC or CM108 | `./setup.sh --link-radio SERIAL` | that interface, as `/dev/moreopenrepeater-link` (`compose.link-radio.yaml`) |

Nothing else is widened: no privileged mode, no added capability, not the whole of `/dev`.

The **GPIO** card on the Audio page (the spare outputs and inputs) is the CM108's own pins, so it works with the PTT
interface alone and needs none of this. Only header pins do.

**GPIO.** `--gpio` finds the header's chip the way the core does (the one labeled `pinctrl-...`, which `gpiodetect`
prints; on a machine with a single chip, that one), sets `MOR_GPIOCHIP`, and sets `MOR_GPIO_GID` from
`getent group gpio` unless you already set it. On a Pi 5 or a machine with several chips and no `gpiodetect`
(`sudo apt install gpiod`), it asks you to set `MOR_GPIOCHIP` yourself. The pins are 3.3 V only.

**Serial ports.** Give a `/dev/serial/by-id/` name (`ls /dev/serial/by-id` lists them): it stays with the adapter, where
`/dev/ttyUSB0` and `/dev/ttyUSB1` swap when you plug them in another order. A `ttyUSBn` or `ttyACMn` works, with a
warning, and the script prints the by-id name for it when there is one. Repeat the flag for a second adapter. The list
is `MOR_SERIAL_DEVICES` in `.env`; `compose.serial.yaml` is written from it (a compose list cannot come from one
variable), so edit the variable, not the file. The core's user is already in the `dialout` group. The Audio page then
offers the adapters in its serial port lists. The core lists them, since the dashboard container is given no devices.

**A link radio.** The second interface needs a stable name of its own, and two interfaces cannot both carry
`/dev/moreopenrepeater-ptt`. Plug both in, then:

```
./setup.sh --list-interfaces
  /dev/hidraw0  AIOC  serial A1B2C3  on USB port 1-1.2  ->  --link-radio A1B2C3
  /dev/hidraw2  AIOC  serial D4E5F6  on USB port 1-1.4  ->  --link-radio D4E5F6
./setup.sh --link-radio D4E5F6          # the one on the link radio
```

Any other CM108 or AIOC interface on the machine (the Winlink packet radio's, see below) needs the same treatment, even if its
PTT is never used: the repeater's rule names every interface it is not told to leave out.

If it is not clear which is which, unplug the link radio's interface, run `--list-interfaces`, plug it back in and run it
again: the one that came back is it. An interface with no serial number, or two with the same one (common on cheap
CM108 dongles), is named by its USB port instead, `--link-radio port:1-1.4`; it must then stay in that socket. The script
writes `/etc/udev/rules.d/60-moreopenrepeater-link.rules` for that one interface (with sudo), and installs the current
`61-moreopenrepeater-ptt.rules`, which leaves the marked interface out of the repeater's name. The repeater's interface
stays `/dev/moreopenrepeater-ptt`. In the core, `MOREOPENREPEATER_LINK_CM108_HIDRAW` becomes the link name instead of
`off`. The link radio's sound card is chosen on the Audio page as before, since the core already sees every card.

**A packet radio's interface (Winlink).** If the Winlink packet radio uses a CM108 or AIOC (`docs/winlink.md`), its interface
gets a name of its own the same way, and the repeater's rule leaves it out:

```
./setup.sh --packet-radio D4E5F6        # the ID --list-interfaces shows for it (or port:1-1.4)
```

It writes `/etc/udev/rules.d/60-moreopenrepeater-packet.rules`, makes `/dev/moreopenrepeater-packet`, and installs the current
`61-moreopenrepeater-ptt.rules`. Without this, the packet radio's interface can take `/dev/moreopenrepeater-ptt` after a reboot
or a replug (two interfaces cannot both have it, and which wins is not something you control), and the core would key the packet
radio instead of the repeater. This is for **any** second CM108 or AIOC, whether the packet radio's PTT uses it or it is only the
sound card. Nothing is mapped into a container by this flag: to key the packet radio with the interface, set
`MOR_PACKET_PTT_HIDRAW=/dev/moreopenrepeater-packet` in `.env`. Installs without a packet radio are unchanged. The repeater's own
interface stays `/dev/moreopenrepeater-ptt`.

**If a device goes away.** The core will not start while a mapped device is missing (Docker refuses), and setup.sh
says so and starts nothing. Plug it back in, or take it out: remove its compose file from `COMPOSE_FILE` in `.env`
(or its entry from `MOR_SERIAL_DEVICES`) and run `./setup.sh` again. Before that, a page that needs the hardware says
what is missing and the command to run, in place of the system's "No such file or directory".

**Not tried on hardware.** The flags, the compose files and the udev rules are tested against stand-ins (a fake
`/dev`, a fake `/sys`, a shimmed `docker`). They have not been run with a second AIOC, a serial adapter or GPIO wiring.

### Compose files and the `-f` option

The extra compose files (`compose.gpio.yaml`, `compose.serial.yaml`, `compose.link-radio.yaml`, and the two layouts
`compose.protect-sd.yaml` and `compose.host-network.yaml`) are all turned on the same way: `setup.sh` lists them in
`COMPOSE_FILE` in `.env`, which Compose reads on every run, `./update.sh` included. So a flag stays on until you turn it off:

| You want | Run |
| --- | --- |
| the log history in memory, off the SD card | `./setup.sh --protect-sd` (off: `--no-protect-sd`) |
| the old layout, the core and the dashboard on the host's network | `./setup.sh --host-network` (off: `--no-host-network`) |

Running the same flag twice changes nothing, and turning one off leaves the others in `COMPOSE_FILE` (the line goes when
only `compose.yaml` is left). Each restarts the containers it changes.

Do not start Compose with your own `-f` options, for example `docker compose -f compose.yaml -f compose.protect-sd.yaml up -d`.
`-f` makes Compose ignore `COMPOSE_FILE`, so that command would recreate the core **without** the GPIO chip, serial adapters or
link radio's interface that `setup.sh` added there, and `./update.sh` (which does not use `-f`) would drop what you did by hand
the next time it ran. If you did start it that way once, run `./setup.sh --protect-sd` (or `--host-network`) to put it where
`update.sh` finds it.

A file of your own works the same way. Compose reads `compose.override.yaml` on its own only while `COMPOSE_FILE` is **not** set;
once `setup.sh` has set it (any of the flags above), an override file is skipped. `setup.sh` lists an existing
`compose.override.yaml` for you when it first creates `COMPOSE_FILE`; otherwise give your file a name of its own and add it to
`COMPOSE_FILE` in `.env`, after the others (`COMPOSE_FILE=compose.yaml:compose.gpio.yaml:compose.mine.yaml`).

## The AllStar container

The AllStarLink node (Asterisk with app_rpt, from AllStarLink's own Debian 13 packages) runs in its own container,
`allstar`, and **EchoLink comes with it**: `chan_echolink` is a module of the same Asterisk process, configured in
`echolink.conf`, not a separate service. It is off by default, so an existing install is untouched:

```
cd docker
cp allstar.env.example allstar.env        # set MOR_AMI_SECRET, for example: openssl rand -hex 16
docker compose --profile allstar up -d --build allstar
docker compose ps allstar                 # healthy within a minute
docker compose exec allstar asterisk -rx "core show version"
```

**It needs nothing from the host.** No host Asterisk and no host `/etc/asterisk` (the configuration is the
`allstar-config` volume, and what Asterisk keeps is `allstar-data`), no devices, no kernel modules, no host
network. AllStarLink's own Docker setup needs DAHDI built for the host's kernel and OSS sound modules; this one
does not, because Asterisk's timing is the kernel's `timerfd` (checked on a real container: `timing test` gets its
50 ticks a second) and the repeater's audio reaches the node from the core over USRP, so there is no sound card.
The image is built from a pinned base, a hash-checked repository package and a pinned node version
(`ASL_VERSION` in `Dockerfile.allstar`), so an upgrade is a deliberate change.

**What is reachable.** Only IAX2 (4569/udp, how AllStarLink nodes find each other; forward it on your router as you
would for any node) is published, on `MOR_ALLSTAR_IAX_PORT`. If a host Asterisk is still running it already has that
port: stop it, or publish the container's on another port. The management interface (AMI, 5038) is **never
published**: it answers only the private `services` network (`MOR_SERVICES_SUBNET`, default `172.28.0.0/24`), with
a secret you choose. The package ships a published default secret that is only safe on a host's loopback, so the
container **refuses to start** without your own (at least 16 characters), or with that one. The API's link service
uses the same secret (`MOREOPENREPEATER_AMI_SECRET` in `api.env`), with `allstar` as the host once the API is on that
network.

**What the entrypoint does.** It writes two files at every start and touches nothing else in `/etc/asterisk`
(`rpt.conf`, `modules.conf`, `echolink.conf`, `extensions.conf` and `iax.conf` are yours, and the dashboard's AllStar
setup edits them in the volume). `manager.conf` carries the secret, the bind address and the network rule.
`logger.conf` sends everything to the console, so `docker logs allstar` has it and nothing is written to the disk,
and leaves out the `dtmf` level the package's default logs to a file (the log is no place for what someone keyed). It
also switches off `chan_simpleusb` and `chan_usbradio` if `modules.conf` loads them: with no sound card they search for
one over and over, which was about 3,000 log lines in 30 seconds on a test container, and on a Pi that is wear on the
SD card. The container is read-only, runs as user 999 with every capability dropped, and uses about 46 MB and 0.5%
of a CPU when idle.

**Setting the node up from the dashboard.** The AllStar page works in this install. The API container mounts the same
`allstar-config` volume (as `/etc/asterisk`, with `MOREOPENREPEATER_ASTERISK_CONFIG_DIR` set in `compose.yaml`) and edits
the node's files as it always has: it turns the node's radio over to the controller (`rxchannel = USRP/<the core>:34001:<node port>`),
loads `chan_usrp`, sets the callsign and the AllStarLink portal's node number and password, and asks Asterisk to restart over
AMI (so `MOREOPENREPEATER_AMI_*` must be set in `api.env`, which `./setup.sh --allstar` does). Then it tells the core where the
node is, with a `usrp_configure` command, and the core points its AllStar audio at the node and keeps that in its own state
file (`usrp-endpoints.json`, a host and a port), so after a restart of the core, with the API away, the audio is still there. If the
core is away when you save, the API tells it when it connects. Both containers run as user 999, so one owner has the files. Until
the node's container has started the folder is empty and the page says so (and works on its own once it has). The old way still
works: `MOREOPENREPEATER_USRP_NODE` in `core.env` is a node you set up by hand, which the page then leaves alone (and the core
refuses to be pointed elsewhere). Don't set both.

**Importing an existing node.** `./setup.sh --import-allstar /etc/asterisk` points the AllStar page at an ASL3 node already on
this machine and offers to bring its number, callsign, registration, startup macro and allowed links over, with a preview first
(docs/allstar.md, Importing a node). The folder is mounted read-only into the API container only; without it an empty
`docker/empty` folder is mounted.

**Not done yet.** Nets stay in the API. Asterisk's own log
lines go to Docker's log (they are not structured events yet). Measured on a Pi 3B+, in a separate compose project: from
the AllStar page, choosing node 1999 rewrote `rpt.conf` and `modules.conf` in the shared volume, restarted Asterisk over AMI
(still healthy), loaded `chan_usrp`, and the core's audio was running to `allstar:32001` a few seconds later; with the API
stopped and the core restarted, the audio came back by itself.

## The private network

The core and the API are on the private `services` network (default `172.28.0.0/24`, `MOR_SERVICES_SUBNET`) with the
AllStar node, not on the host's network. They used the host's network only so a host Asterisk could reach the core on the
loopback, and a service that relies on the host's loopback relies on the host. Now:

- **Nothing is published from the core.** The dashboard is published on the host's **loopback**
  (`127.0.0.1:8000`, `MOR_API_HOST` and `MOR_API_PORT` in `.env`), as before: with no accounts set up it is open and can key
  the transmitter, so it is not offered to the network until you choose to (`MOR_API_HOST=0.0.0.0`, after creating an
  account). A Tailscale Serve or a reverse proxy on the host still points at `127.0.0.1:8000`. Inside its container the
  API listens on `0.0.0.0:8000`; what the host can reach is only the published port.
- **Fixed addresses, not names**, for the core (`MOR_CORE_IP`, `172.28.0.10`) and the node (`MOR_ALLSTAR_IP`, `172.28.0.20`),
  because Asterisk's USRP channel and its AudioSocket dialplan take an IP and the core only accepts radio audio from the
  node's address (anything that arrives on that port is transmitted). Keep them inside `MOR_SERVICES_SUBNET`. The core
  listens for the node's audio on `0.0.0.0:34001` and for autopatch calls on `0.0.0.0:9092`, which are reachable only from
  the services network and published nowhere. Anything else on that network could connect to the AudioSocket port, which
  has no login, so the network holds only containers you run.
- **The node is found by name for management**: `MOREOPENREPEATER_AMI_HOST=allstar` in `core.env` and `api.env`, with the
  secret from `allstar.env` (`./setup.sh --allstar` writes them). The dashboard's AllStar page turns the radio's audio to and
  from AllStar on. (`MOREOPENREPEATER_USRP_NODE=172.28.0.20:32001` in `core.env` does it by hand instead, and the page then
  leaves it alone.) A bench with no node is unchanged.
- **The way back:** `./setup.sh --host-network` puts the core and the API back
  on the host's network with the loopback addresses (`--no-host-network` returns to the private network), for a site that still
  has an Asterisk or a DMR bridge on the host. It lists `compose.host-network.yaml` in `COMPOSE_FILE` (see **Compose files and the
  `-f` option**), so `./update.sh` keeps it. The DMR bridge's audio port (`MOREOPENREPEATER_DMR_USRP_LISTEN`) is on the
  loopback there too: on the host's network the default of every address would have put that UDP port on the LAN.
  A host DMR bridge's audio path (a USRP pair on the host's loopback) is not reachable from the private network: use the
  override if you still run DMR on the host.
- **Measured on a Pi 3B+** (a separate compose project with a copy of the data, while the live stack was stopped): all
  three services healthy in 32 seconds with the dashboard on the host's loopback and refused on the LAN address; the
  API's link service logged in to a real app_rpt at `allstar:5038` and the core took its picture; a USRP voice packet from
  the node's address (`172.28.0.20`) keyed the repeater and the hardware PTT was applied and released, while the
  identical packet from the API container (`172.28.0.2`) was ignored, as the source-address rule intends; the core
  listens on `0.0.0.0:34001` and `0.0.0.0:9092` inside its own network and is published nowhere; autopatch's AudioSocket port
  answered from the node's container; and after a restart the core found the PTT device again through its stable name and
  the link service resent its picture. Bridge networking adds a NAT hop to 50 USRP packets a second and the
  dashboard, which did not show up in the audio engine's health. **The AIOC dropped off USB right after each keying**
  (the hub's "EMI?" line, then the watchdog's restart), on this layout and on the host layout alike: it is the USB
  problem described under **Health and restarts**, not the network.

## The DMR container

The DMR bridge (DVSwitch's `Analog_Bridge` and `MMDVM_Bridge`, and `md380-emu`, the software AMBE codec they use when there
is no USB vocoder) runs in its own container, `dmr`, off by default like the AllStar node:

```
docker compose --profile dmr up -d --build dmr        # or ./setup.sh --dmr, which does this with the rest
docker compose exec dmr python3 /usr/local/bin/dmr-supervisor.py --check    # "waiting for the DMR settings..." until configured
```

Then open the dashboard's DMR page, enter the callsign, DMR Id, network and password, and save: the DMR audio turns on with it.

**It needs nothing from the host**: no systemd, no host config, no devices, no kernel modules, no sound card, and it publishes
nothing (it reaches the DMR network, BrandMeister, TGIF or a HomeBrew master, by outbound connection). DVSwitch publishes its
packages for Debian 12; they install and run on Debian 13 (arm64 and amd64). The image pins the repository's keyring
by hash, each package by version, and never runs DVSwitch's own install script. `md380-emu` is a 32-bit ARM program that
ships its own `qemu-arm-static`. On a Raspberry Pi 3B+ the three programs together use about 14 MB and 1.4% of a CPU when idle, and
about 27% of one core at its peak while voice goes through the codec (five seconds of USRP audio became a DMR
transmission of 85 frames, with the system load unchanged and `throttled=0x0`). The container gets a quarter of the
default CPU share so the audio engine's processes come first.

**A small supervisor stands in for systemd and the root helper.** It starts the three programs, starts one again three seconds
after it exits (as the units did, with a longer wait for one that keeps dying, up to a minute), and does not restart
`Analog_Bridge` after a configuration error (exit 252 to 254), as its unit does not. It honors the request file and the status
file the dashboard already uses (`MOREOPENREPEATER_DMR_RESTART_REQUEST`, and the status at `MOREOPENREPEATER_DMR_STATUS`): one
word, `restart` or `stop`, in the control volume, answered in `status.json`. (`install` answers that DVSwitch is in the image.)
What the three programs print goes to `docker logs dmr`, named, and nothing is written to the disk.

**Until the settings are complete it starts nothing.** The bridge needs a callsign, a DMR Id, the network's address and the
network password; without them the programs exit at once, and a restart loop every few seconds is log noise and wasted CPU. So
the container reports healthy-but-waiting, says what is missing, and starts the programs by itself when the settings are
complete. Enter them (the password in the environment, never on the command line):

```
docker compose exec -e DMR_PASSWORD='your-network-password' dmr \
  python3 /usr/local/bin/dmr-supervisor.py --configure --callsign W1AW --dmr-id 1234567 --network brandmeister
```

(That is the way without the dashboard. `--network` is `brandmeister`, `tgif` or `custom` with `--host`; `--talkgroup`, `--slot`,
`--port` and `--color-code` are optional. It does not turn the core's DMR audio on: set `MOREOPENREPEATER_DMR_USRP_PEER=172.28.0.30:34012`
in `core.env` for that.) It writes the same keys the dashboard's DMR page writes, refuses anything that could break the `.ini` files, and asks
the running bridge to restart. The files are in the `dmr-config` volume and are written once, the first time, with the core's
address and no log file; after that they are yours and are never touched.

**Setting it up from the dashboard.** The DMR page works in this install. The API mounts the bridge's `dmr-config` and
`dmr-control` volumes (`MOREOPENREPEATER_DMR_DIR`, `_DMR_RESTART_REQUEST` and `_DMR_STATUS` in `compose.yaml`), writes the same
`.ini` keys as ever, and asks the supervisor to restart by writing the request file. It never writes a first set of files: the
bridge does that, with the core's address in them (a file seeded with a loopback address would send the audio nowhere), and
the page says "start it with `docker compose --profile dmr up -d dmr`" until it has. Saving also tells the core where the bridge
is (the API's `MOREOPENREPEATER_DMR_USRP_PEER`, `172.28.0.30:34012` by default), which turns the DMR audio on; turning DMR off turns it
off. The page's three program lights come from the supervisor, which writes `programs.json` in the control volume whenever they change.
The core keeps where the bridge is, as it does for the node, so it survives a restart. Measured on a Pi 3B+: after saving from the page
the bridge restarted with the new settings, kept the core's address in `[USRP]`, and the core's DMR audio was running to the bridge.
The DMR network's traffic is a separate thing from the radio: with no master reachable nothing is transmitted.

## The time container

A Pi has no battery clock. One that boots with no network can be hours or days off, and the repeater leans on the clock: the
station ID goes out by it, so do the spoken time, scheduled announcements and nets, and every time stamp in the logs and the
activity history. So there are two parts, and the second is optional:

1. **The check (always on).** The dashboard compares the Pi's clock with an NTP server and says so when it is off. It changes nothing.
2. **The time container (off by default).** chrony, in its own container, sets the clock from the server you picked.

### What the check does

The API sends one SNTP packet (RFC 4330: a single UDP datagram to port 123, a 2 second wait) to the server you picked, or to
`pool.ntp.org` if you have not, and works out how far the Pi's clock is from it and how long the round trip took. It only believes
a reply that comes from the address it asked, that answers the question it sent (the reply has to echo back the timestamp the
request carried, which is random in its low bits), that comes from a server saying its own clock is set, and whose timestamps
are possible. A kiss-o'-death reply (the server telling a client to go away) is shown as that, never as a time. So a made-up
reply cannot make a wrong clock look right.

It never blocks anything. It runs in a worker thread, one check at a time, with an overall deadline (a name lookup that hangs is
abandoned), and the answer is reused for about a minute, so a page that polls cannot hammer the server. The log gets one named
event when the picture changes (`time.clock_off`, `time.clock_ok`, `time.check_failed`) with numbers only, never the server's name.
It also runs gently in the background, once every ten minutes, and raises an alert (ntfy, Telegram, email or webhook, whichever
you have set up) when the clock is **off by 2 seconds or more twice in a row**. The alert says in plain words what a wrong clock
breaks. It says so again only after six hours if it is still wrong, and sends an all-clear once the clock is within a second.
A server that cannot be reached says nothing about the clock, so it never raises or clears the alert. The first background check is
two minutes after start, so a container that is about to fix the clock gets the chance first.

### The server setting

In the dashboard this is the **Time server** card on the **Date and time** page: an admin picks the server there, everyone else sees
it read-only, and the card shows the clock check and whether the time container is running. The card reads `GET /api/time` about
every 45 seconds while the tab is open and visible.

The server is a host name or an IP address, kept in a small file of its own, `ntp.json`, in the `time-config` volume: not in the
repeater's configuration. Empty means `pool.ntp.org`. It is validated very strictly, because the time container writes it into
chrony's configuration: letters, digits, dots and hyphens in each part of a name (a part never starts or ends with a hyphen), or an
IP address. No spaces, quotes, `#`, `;`, `/` or `%`, nothing that is not plain ASCII (give an international name in its `xn--` form),
at most 253 characters, and never a leading dash (it would be read as an option). An address that cannot be a time server (this
machine, the unspecified address, multicast or reserved) is refused too. The file is written to a temporary file, flushed and
renamed over the old one, so a power cut leaves the old setting or the new one, never half of one, and it is readable by everyone
(it holds nothing secret) because the time container is another user.

| Route | Who | What |
| --- | --- | --- |
| `GET /api/time` | any signed-in account | The server being checked, whether it answered, `offset_ms` (the server's time minus the Pi's clock, so positive means the Pi is behind), `round_trip_ms`, `stratum`, a plain `state` (`ok`, `off` when it is off by `limit_ms`, 2000 by default, or more, `unreachable`, `not_checked`), why there was no reading (`error` and `error_text`) and `container`: whether the time container is running and what it sees |
| `PUT /api/time/ntp` | admin only | Body `{"server": "time.example.org"}`; `""` goes back to the default. Saves it, drops the cached check and checks against the new server before answering. A value that fails the rules above is a 400 with a sentence and nothing changes. The audit log records that it was changed, not to what |

### The optional time container

```
./setup.sh --time                                  # in the docker folder, with the rest of the install
docker compose --profile time up -d --build chrony   # or by hand, on an install that is already running
```

It is a Debian image with Debian's own `chrony` package (the base is pinned by digest like the other images). A small supervisor
(`docker/chrony-supervisor.py`, like the DMR container's) is its entrypoint. It reads `ntp.json` at start and again whenever that file
changes, **checks the value again** (it does not trust the file, whoever wrote it, because the value goes into a configuration file),
renders `chrony.conf`, and restarts chronyd. A value that fails the check is not used: it keeps what it had (the default at start)
and says why in its status. chronyd is a client only: `port 0` and `cmdport 0` mean it listens on no network port, and nothing is
published. `makestep 1.0 3` lets it step the clock in its first three updates after every start, so a Pi that boots with a wrong
clock is corrected at once instead of being nudged for hours. It reads no hardware clock. Its configuration, socket and drift file
are in a tmpfs, so it writes nothing to the SD card.

**Each side writes only its own file.** The dashboard writes the setting into the `time-config` volume, which the time container
mounts read-only. The time container writes `status.json` into the `time-status` volume (every 30 seconds: when, whether chronyd is up,
and what `chronyc tracking` says), which the API mounts read-only. The dashboard calls the container running when that file is fresh
and says chronyd is up. A server it cannot reach is not "not running": the container is doing its job, and the page shows what it sees.

#### The privilege it needs, in plain words

**This container is given `CAP_SYS_TIME`, which lets it set the host's clock.** That is a real privilege, and it is why the container
is off unless you ask for it. The clock belongs to the kernel, not to the container: when this container sets the time, every other
container and the host itself see the change. A bug in chrony, or in the supervisor, or a wrong NTP server, could therefore move the
host's clock. Everything else about the container is as small as it could be made:

- `cap_drop: [ALL]`, then **only** `SYS_TIME` added back. No other capability.
- It runs as root inside the container, with that one capability. That sounds worse than it is: root with one capability can set the
  clock and nothing else. It cannot change file owners, read other users' files, signal other users' processes, bind a low port,
  open raw sockets or load anything. The other way would need more capabilities, not fewer. chrony's own way to run without root
  (`-U`) is documented as assuming the process already has the capabilities, and a container gives an added capability to root, not to
  another user, so chronyd would start and then be unable to set the clock. Its usual privilege drop (`-u`) needs `CAP_SETUID` and
  `CAP_SETGID`, and a supervisor needs `CAP_KILL` to restart it afterwards. The configuration says `user root` so that chronyd does
  not try to drop. (This was worked out from chrony's documentation and source and Docker's seccomp profile. The container has not been
  run yet, not on a Pi and not on Docker, so the first run is the real test.)
- A read-only root filesystem, `no-new-privileges`, no device, no published port, no host network, no host folder mounted, and Docker's
  default seccomp filter stays on (it allows `settimeofday` and `clock_settime` only when `CAP_SYS_TIME` is present).
- No network service: `port 0` and `cmdport 0`. Its one control path is a Unix socket inside its own tmpfs.

#### If the host already syncs its own clock

Two programs setting one clock fight each other: each steps or slews what the other just fixed. If the host runs its own time sync
(systemd-timesyncd, which Raspberry Pi OS has on by default, or chrony or ntpd), pick one. `./setup.sh --time` looks, names what it
finds, and **changes nothing**; it starts the container anyway because you asked for it. To let the container do the job, turn the
host's off:

```
sudo timedatectl set-ntp false            # systemd-timesyncd. Undo with: sudo timedatectl set-ntp true
sudo systemctl disable --now chrony       # or chronyd, ntp, ntpd (and enable --now to undo)
```

Or the other way round: leave the host's sync on and do not run the container. The check and its alert still work without it.

#### Removing it

```
docker compose --profile time rm -sf chrony     # stops and removes just this container
docker volume rm moreopenrepeater_time-status   # optional: its status file. Keep time-config: it is the setting.
```

Do not use `docker compose down -v` on the project to do this: it removes every volume, including the ones that hold the repeater's
settings. The clock stays where chrony left it. Without the container the dashboard still checks the clock and still alerts; its
server setting is kept for when you start the container again. `./update.sh` rebuilds the container with the rest if it exists, and
leaves it alone if it does not.

## Health and restarts

`python -m core.healthcheck` asks the core's own `/health` and exits 1 when it
reports `failed` (a stalled controller tick, say). Docker marks the container
`unhealthy`, but **Docker does not restart an unhealthy container by itself**.
(The check has to stay light: it once imported the core's own modules, which took
over 5 s of CPU on a Pi, timed out and competed with the audio. A test guards that.)
The systemd install does, through its watchdog (`WatchdogSec`). The core
container does the same job itself: `MOREOPENREPEATER_EXIT_ON_STALL=1` (set in
`compose.yaml`) runs the watchdog's check every 2 seconds, and when the controller or an audio engine
has made no progress for 10 seconds it takes the transmitter off the air, records a `watchdog.stalled`
event (error, `part` says which), flushes the log and exits with status 75, and `restart:
unless-stopped` starts it again. That is what happened on the Pi on 2026-11-19: the AIOC dropped off USB for
two seconds, the audio engine stalled without any error, and the core sat unhealthy for 18 minutes with its
station ID going nowhere. **How long is a stall.** Ten seconds by default. `MOREOPENREPEATER_STALL_SECONDS` (in `core.env`, 2 to
300) sets it, and the watchdog and the core's `/health` both read it, so they always agree. It is also how
long a transmitter can stay keyed after the sound card vanishes under a running core: when the USB link dies
mid-transmission the audio thread is stuck in the driver and cannot apply the unkey, so only the watchdog
notices, at this limit (the restart then unkeys it, and the core logs a critical `ptt.release_unconfirmed`
meanwhile). A shorter limit shortens that window and restarts sooner; a Pi 3B+ that is briefly starved of CPU
(swapping, a long garbage collection) could be restarted for nothing, so go below about 5 only if you have
watched it hold. The check runs every 2 seconds, or every quarter of the limit if that is shorter. A value
outside 2 to 300, or not a number, is ignored and the log says so.

### The support bundle in the container install

The dashboard's support bundle (the zip on the Remote page) asks the host for things a container does not have, so it fills
them in another way and says so in the file:

- **USB devices, sound cards and hidraw devices** come from `/sys` when `lsusb`, `/proc/asound` (Docker hides it) and `/dev/hidraw*`
  are not there. The list has the same facts: bus position, vendor and product id, name and speed.
- **The sound card list and the mixer dump** are the core's, read over the socket like the Audio page's.
- **`core.log`** has the core's own log lines, since the core is another process and its lines are not in the dashboard's file.
- **`journal.txt`** says there is no systemd journal for the service here and points to the two log files.
- **The host's kernel log is in it when the core was given it** (see **What the container is allowed to do**): the core reads
  `/dev/kmsg` and the bundle's `usb.txt` shows the USB and HID lines of the last 400 messages, with the time
  each happened, as `dmesg -T | grep -i usb` would. The USB drops that matter show there (`disabled by hub (EMI?)`). When the
  core has none (`MOR_KMSG_DEVICE=/dev/null`, a host without `/dev/kmsg`, a host that restricts it, or the core cannot be
  reached) the section says why, and you can run `dmesg -T | grep -i usb` on the host and add the output.

### When the sound card drops off USB

A USB sound card can drop off the bus for a couple of seconds and come back (RF from the transmitter getting into
the cable, a loose connector, a current dip when the radio keys). The hub logs `disabled by hub (EMI?)`, and what the
core does about it is:

1. **It notices within 2 seconds.** PortAudio does not always say the stream stopped; the callbacks just stop. The
   engine's input queue then stays empty, and after 1.5 seconds with no audio it logs `audio.stream_died` with
   `reason` `no_audio` (`inactive` is the case PortAudio does report) and lets go of the card.
2. **It looks for the card every second and opens it again in the same process,** for the first 20 seconds, then
   less often (5 seconds, doubling to a minute). A card that is not there when the core starts is looked for the same way,
   so a core that restarts a moment after a drop does not give up on audio.
3. **If a container core has not got it back after 6 seconds, it restarts itself** (the `sound card` part of the
   watchdog). A restart gives the container fresh device nodes, which matters when the card came back under a different
   number. A native install does not do this: a card that is unplugged for good should not put the service in a restart loop.
4. **A station ID that was on the air is sent again once audio is back,** but at most once every 5 minutes. Sending it
   keys the transmitter, so a card that drops on every keying would otherwise key up, drop and send the ID again for as long
   as it kept dropping.

Measured on the Pi 3B+ bench with the AIOC keyed from a bare script (5 drops in a row): audio back 3.6 to 4.3 seconds
after each drop, the core never restarted, and the card came back as the same `hw:2,0` and `/dev/moreopenrepeater-ptt` every time
(most likely because the core lets go of the old card within 1.5 seconds, before the kernel registers the new one). Before this, the same
drop took about 19 seconds: 10 seconds for the stall limit to pass, then a container restart.

This recovers from the drop. It does not stop the drops. If the hub logs `EMI?` in the same second the core logs
`ptt.applied` with `active` true, the transmitter itself is upsetting the USB link, and that is a hardware problem (an
antenna close to the cable, the radio's charger or supply, the cable). A dummy load in place of the antenna, or the radio
on its battery, tells you which.

**Power warnings.** The native install asks the Pi's firmware (`vcgencmd get_throttled`) whether the supply has dipped, and
the dashboard warns about undervoltage and throttling. A container has no `vcgencmd`, so the dashboard reads the Pi's own
undervoltage alarm instead, the `rpi_volt` device under `/sys/class/hwmon`, which every container can see. It says only
whether the supply is low right now, and it is not latched, so a dip between two readings (the dashboard looks every 30
seconds) is missed, and "has happened since boot" means "since the dashboard started and saw one". Throttling and a
capped clock are not reported this way.

A restart can only help if the container can start again: a PTT device
path that no longer exists stops it starting, which is why the PTT device has a stable name (see above). `restart: unless-stopped` covers crashes and exits, but not a manual `docker kill`
or `docker stop`. The API container is not restarted this way: it holds no device.

## Voices

**Piper is its own container**, `piper`, on by default: the core asks it for speech over the private network (`PIPER_SERVICE_URL=http://piper:8766`,
`PIPER_SERVICE_EXTERNAL=1`, set in `compose.yaml`) and holds no Piper of its own, so the core's image is 275 MB (no
ONNX runtime or voices; it was 458 MB) and a crash or a slow voice load happens in another container. Nothing is published. The default voice
(`en_US-amy-low`) is baked into the `piper` image so the first start works offline; any other voice the dashboard picks is
downloaded by the Piper container into the `piper-voices` volume the first time (the core asks it to, so the core needs no
internet access for it). **The core never waits for it.** Audio and PTT start at once, whether or not Piper is up, so a restart after a stall or a
sound card drop is not held up by a Piper that is down. A background check looks for Piper (every 30 seconds at most) until it answers. While
it is down, or if it cannot get a voice, the core speaks with `espeak-ng` (still in the core image) and keeps repeating. If a voice ID was
rendered by the fallback in the meantime, it is thrown away and rendered again with the Piper voice when Piper answers, so there is no need
to restart the core. If you want no Piper at all, stop the `piper` service and the core uses `espeak-ng`.

Measured on a Pi 3B+, with the core image built without Piper: the `piper` container is healthy in 36 seconds, and uses about 110 MB of the
905 (about the same as it did inside the core). A test sentence took 5.3 seconds the first time and 2.6 seconds warm. Asking for a second
voice (`en_US-lessac-low`) made the Piper container download it (20 seconds, once), and it was listed afterwards. With Piper stopped, the core's voice
fell back to `espeak-ng` in 1.3 seconds. In the old host layout (`compose.host-network.yaml`) there is no `piper` name to find, so that file puts
Piper back inside the core (it rebuilds the core image with `WITH_PIPER=1`) and does not start the `piper` service. Kokoro is not in either image.

## Measured on a Pi

All of this is on a Raspberry Pi 3 Model B+ (Debian 13, an AIOC, 4 cores, 905 MB of RAM, on Wi-Fi). The
container starts, finds the sound card and the CM108, keys for the scheduled ID and unkeys, and is
followed by `RemoteCore` through the volume's socket. With the core, the API and Piper running, memory
is about 265 MB available with 270 MB of swap in use (the core 70 MB, the API 110 to 140 MB, Piper
210 MB), the CPU is under 1.3 load and 44 to 49 C. The Pi has hit under-voltage at some point since it
booted (`vcgencmd get_throttled` says `0x50000`), though not during any test here.

### Audio glitches

Run as containers, the core logs a pair of `audio glitch` lines (a sound card receive overrun and a
transmit underrun) about every 3.5 minutes while idle. The same code does not do that natively. This
was measured, not guessed. Counting receive overruns:

| Setup | Overruns |
|---|---|
| Native install (API and core in one process), 30 min | 1 |
| The core run on the host with no Docker, same user and priority, 20 min | 0 |
| The core container alone, health check off, 25 min | 1 |
| The core container with the API container, many runs over two hours | one every 3.5 min |
| Core alone, plus two memory-heavy processes at 60% | 158 in 10 min |
| Core alone, plus two CPU-only processes at 60% | 2 in 10 min |
| With `dwc_otg.speed=1`: core alone, plus two memory-heavy processes at 60% | 0 in 10 min |
| With `dwc_otg.speed=1`: core and API together, no extra load | 0 in 19 min |

Docker's default seccomp profile was part of it: with it the core alone glitched every one to four
minutes, and `seccomp=unconfined` (kept in `compose.yaml`) cut that to one in about 30, which is the
core-alone figure above. A tight allowlist profile is a later slice and has to be measured this way.

What the rest of the work ruled out, each with a run of its own: the Python interpreter (an opt-in
diagnostic, below, saw no garbage collection or held thread near any glitch), the scheduler (a trace of
the core's threads showed them running within a millisecond of being woken, 0.03 ms typical and 20 ms
the worst in 2.5 minutes), CPU priority and weight (nice -10 and a cgroup weight matching the native
unit's 200 changed nothing; nice only ranks threads inside one cgroup), memory pressure (no page
faults or swap-ins near a glitch), disk (almost no writes), the health check, the API's polling, the
weather poll, and a larger sound card buffer, which made it worse (265 overruns under the same memory
load that gave 158 with the default). At every glitch the USB interrupt count dips by 500 to 800 in
that second: the card's audio did not arrive on time.

What did matter is memory traffic from other processes. Memory-heavy load caused the glitches and the
same amount of CPU-only load did not. On this Pi the USB controller's DMA shares the memory bus with
the CPU, and the AIOC is a full-speed device behind the Pi's high-speed hub, so every transfer is a
split transaction that the controller's interrupt code has to time exactly. A second busy process,
even a quiet API container, is enough to make it miss one now and then. I have not proved the exact
mechanism, only that the glitches follow memory traffic and nothing else I tried.

**The fix on a 3B+ is `dwc_otg.speed=1`.** Add it to `/boot/firmware/cmdline.txt` (the file is one
line; append it to the end) and reboot. It forces the USB controller to full speed, so the AIOC is
no longer reached through split transactions. It was tried here and it worked: no overruns under the
memory-heavy load that gave 158 without it, and none in 19 minutes of the core and API running
together (two station IDs went out in that time, so the audio engine was alive), where one every 3.5
minutes was normal. It is a boot setting, so it takes a
reboot. On a 3B+, `./setup.sh` says so and `./setup.sh --usb-speed` does it: it backs up `cmdline.txt`,
adds the setting to the one line and leaves the reboot to you. It is never done unasked. The cost is that every USB device on the Pi is held to 12 Mbit/s,
including the wired Ethernet port, which is fine for a repeater on Wi-Fi and worth knowing about on a
wired one. A Pi 4 or 5 has a different USB controller and should not need it; that has not been
tested.

Two smaller things follow from the same work:

- Keep the other processes light. The API polls the core's hardware every 2 seconds and its log every
  second, and builds a new HTTP client for each request, so 17% of its Python time goes on making
  SSL contexts for a local socket. Cutting the polling did not stop the glitches (the USB setting
  did), but it is wasteful and is being fixed.
- **The native service starts at boot.** After the reboot the old `moreopenrepeater` service, still
  enabled, started by itself, took the sound card and port 8000, so the core container logged
  `couldn't open audio devices` and the API restarted every 20 seconds. Two engines cannot share one
  sound card. Run `systemctl disable --now moreopenrepeater` when the containers take over. The
  first zero-glitch result I got after the reboot was invalid for exactly this reason (the container
  had no audio engine), so the numbers above are from reruns that checked the engine was running. A
  second window was cut short by a USB event on the Pi (a low-speed keyboard re-enumerating, which
  re-enumerated the AIOC once and left the engine dead with nothing logged), so each run now also
  checks that no USB events happened in it and that station IDs went out.
- The reboot changed the AIOC's `hidraw` number again (`hidraw2` to `hidraw0`), which left the core
  unable to start until the path was corrected. That is what the stable name (see above) is for.

To see whether a glitch is the process holding the audio thread up, set
`MOREOPENREPEATER_DIAG_LATENCY=1` in `core.env`. The core then logs, next to the glitch lines, a
garbage collection over 10 ms, a thread held 15 ms or more, and an event loop 15 ms or more late. It
is off by default and only logs when a threshold is crossed.

### Also found

- **Device loss.** With the AIOC unplugged and plugged back in, the core's own health reported
  `audio.engine` failed within 10 seconds, but Docker kept showing the container healthy for a minute or
  more, nothing restarted it, and the AIOC came back as `hidraw2` instead of `hidraw0`, so the core's
  PTT path no longer existed and nothing said so. That is what the stable name for the PTT device and the
  core exiting when audio stalls (see Health and restarts) are for.
- Raspberry Pi OS boots with `cgroup_disable=memory`, so memory limits and `docker stats` memory do not
  work until `cgroup_enable=memory cgroup_memory=1` is added to `/boot/firmware/cmdline.txt` and the Pi
  reboots. ADR 0005's per-module resource budgets need that.
- `docker kill` does not restart the container under `unless-stopped`. A crash of the process does
  (tested: the core was back and followed again in about 24 s).

**Stopping mid-transmission:** `docker stop` sent while the repeater was sending its ID (0.2 s in)
logged `audio engine stopped` within 0.2 s, and the container exited 0 in 0.95 s. That is the core's
own shutdown path releasing the audio device and PTT; nothing here observed the radio itself unkeying.

Still to measure: the PTT line observed independently when the container stops (it needs a scope or a
second receiver), and a Pi 4 or 5. Multi-arch publishing and the module containers are later slices.
