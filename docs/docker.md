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
macros, announcements, nets, link activity, the backup export and restore, accounts, alerts, logs,
and the pages that are not the radio. **The pages about the core's hardware show what the core
reports** (audio engine, GPIO and fan, link radio and remote base, monitor receiver, autopatch, the
AllStar node, DMR), read over the socket every couple of seconds. With the core away they say so
(a 503 `core.unavailable`) instead of showing an old state, and the status panels are left out.
**Acting on them mostly works**: switching a GPIO output, re-tuning the remote base, and placing and
ending an autopatch call are commands the core takes over the socket and checks itself (an output must
be set up as one, the transmitter must be on, the number must be allowed), and its refusal comes back
in its own words (a 409). **The SIP trunk is the API's own**: it is set up over Asterisk's AMI, a
network call, so it works from the API container. Give the API the same `MOREOPENREPEATER_AMI_*`
settings the core has (see `docker/api.env.example`); without them the page says it is not
configured. The rest does not yet: selecting a DMR talkgroup, the AllStar node and DMR setup (they
edit the host's Asterisk and DMR files), and the audio tools answer 503 `core.hardware`. The core's weather macro cannot see the API's NWS alerts and says weather is
not set up; the API's own weather page and alerts work.

Files:

- `docker/Dockerfile.api`: the same base image, the project's own dependency list (read from
  `pyproject.toml`), the app and the built web assets. No audio libraries.
- `docker/Dockerfile.core`: `python:3.13-slim-trixie` (pinned by digest, so one
  line builds for amd64 and arm64), the core's four Python packages, Piper and
  its default voice, `espeak-ng` and `libportaudio2`.
- `docker/compose.yaml`: the service, its devices, its volumes.
- `docker/core-requirements.txt`: what the core imports. No FastAPI, uvicorn or mcp.

## Run it

Stop the normal service first. Both would claim the same sound card and PTT line.

```
sudo systemctl stop moreopenrepeater
cd docker
cp .env.example .env        # then edit: PTT device, gpio group, data folder
docker compose up -d --build
docker compose exec core python -m core.healthcheck
```

`MOR_DATA_DIR` must hold a `state.json` (the saved configuration). The core
refuses to start without one and exits with status 2, so Compose will not
restart-loop it. To start from an installed Pi's configuration, copy it and give
it to uid 999:

```
sudo mkdir -p /srv/mor-data && sudo cp /opt/moreopenrepeater/data/state.json /srv/mor-data/
sudo chown -R 999:999 /srv/mor-data
echo MOR_DATA_DIR=/srv/mor-data >> .env
```

## What the container is allowed to do

- Runs as uid 999 (the uid of `install-pi.sh`'s `moreopenrepeater` user on a Pi), with a
  read-only root, no capabilities and `no-new-privileges`. Docker's default seccomp
  filter is off (see Measured on a Pi).
- Gets `/dev/snd` and one `hidraw` node (`MOR_PTT_HIDRAW`, default `/dev/hidraw0`).
  The group numbers for `audio` and `dialout` are the same on Debian and Pi OS;
  the `gpio` group number differs, so it is `MOR_GPIO_GID`. GPIO pins are not
  mapped yet: add `/dev/gpiochip0` under `devices:` if you use them.
- Writes only to `/data` and the control socket volume.
- Uses **host networking** for now. The link audio paths (AudioSocket on
  `127.0.0.1:9092`, USRP) go over loopback to a host Asterisk. This goes away when
  AllStar is its own container.

## Logs

Three places, from the quickest to the most durable:

- **`docker compose logs -f`** (from `docker/`) follows the core and the API together, time-ordered
  by Docker. Both write to standard output, and the `local` log driver rotates them (10 MB, 3 files
  each). `docker compose logs -f core` for the radio alone. This works with everything else down.
- **The dashboard's Logs page** (and its live stream, the support bundle and remote shipping) shows
  the core's lines with the API's. The API reads the core's recent log over the control socket
  (`GET /logs`, a page at a time from a cursor) about once a second and publishes the lines into its
  own log hub, with the time the core logged them. The core keeps its last 2000 lines in memory, so
  lines logged while the API was away are read when it is back, as far as that reaches; after the core
  restarts, its new log is read from the start. The core never waits on this.
- **Files in the data folder:** `moreopenrepeater.log` (the core) and `moreopenrepeater-api.log` (the
  API), each rotated at 2 MB with three backups. Separate files on purpose: two writers must not share
  one.

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

## Health and restarts

`python -m core.healthcheck` asks the core's own `/health` and exits 1 when it
reports `failed` (a stalled controller tick, say). Docker marks the container
`unhealthy`, but **Docker does not restart an unhealthy container by itself**.
(The check has to stay light: it once imported the core's own modules, which took
over 5 s of CPU on a Pi, timed out and competed with the audio. A test guards that.)
The systemd install does, through its watchdog (`WatchdogSec`). Until the host
layer covers this, treat `unhealthy` as something to alert on. `restart:
unless-stopped` covers crashes and exits, but not a manual `docker kill` or `docker stop`.

## Voices

The default Piper voice (`en_US-amy-low`) is baked into the image at `/opt/piper`
so the first start works offline. For another voice, set `PIPER_DATA_DIR=/data/piper`
and `PIPER_VOICE=<name>` in `core.env`; it downloads into the data folder when
first needed. Build with `--build-arg WITH_PIPER=0` for a smaller image that
uses `espeak-ng` only. Kokoro is not in the image.

## Measured on a Pi

On a Pi 4 (Debian 13, AIOC, 4 cores) the container starts, finds the sound card and
the CM108, keys for the scheduled ID and unkeys, and is followed by `RemoteCore`
through the volume's socket.

**Docker's default seccomp profile broke the audio.** With it, the container logged a
pair of `audio glitch` lines (a sound card receive overrun and a transmit underrun)
every one to four minutes while idle, across several runs. The same core run by
systemd logged none in nine minutes. Turning off the Piper server and turning off
the health check changed nothing; turning off seccomp (`seccomp=unconfined`) cut it to
one pair in about 32 minutes of running (15 minutes with none after start-up, then 17
with one). The same core under systemd had none in about 25 minutes, so the two are
not yet distinguishable. The audio worker makes a steady stream of small system calls, and
the filter adds a cost to each that a Pi cannot always afford. So `compose.yaml`
sets `seccomp=unconfined`, and keeps the rest: non-root user, read-only root, no
capabilities, `no-new-privileges`. A tight allowlist profile for this container
(the core uses few system calls) would restore the filter; that is a later slice,
and it must be measured the same way.

Also found:

- Raspberry Pi OS boots with `cgroup_disable=memory`, so memory limits and
  `docker stats` memory do not work until `cgroup_enable=memory cgroup_memory=1`
  is added to `/boot/firmware/cmdline.txt` and the Pi reboots. ADR 0005's
  per-module resource budgets need that.
- `docker kill` does not restart the container under `unless-stopped`. A crash of
  the process does (tested: the core was back and followed again in about 24 s).

**Stopping mid-transmission:** `docker stop` sent while the repeater was sending its
ID (0.2 s in) logged `audio engine stopped` within 0.2 s, and the container exited 0
in 0.95 s. That is the core's own shutdown path releasing the audio device and PTT;
nothing here observed the radio itself unkeying.

Still to measure: memory and CPU on a Pi 3B+, the PTT line observed independently
when the container stops, and device loss. A stable udev name for the PTT device,
multi-arch publishing, and the API, `logd` and module containers are later slices.
