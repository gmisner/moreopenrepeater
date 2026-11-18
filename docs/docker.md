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
in its own words (a 409). The rest does not yet: selecting a DMR talkgroup, the AllStar node and DMR
setup (they edit the host's Asterisk and DMR files), saving a trunk, and the audio tools answer 503
`core.hardware`. The SIP trunk page needs the core's Asterisk
files and is unavailable. The core's weather macro cannot see the API's NWS alerts and says weather is
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

What to do about it, in the order I would try:

- Use a Pi 4 or 5 for container installs. Their USB is a different controller. This is the likely
  fix and has not been tested.
- On a 3B+, add `dwc_otg.speed=1` to `/boot/firmware/cmdline.txt` and reboot. That forces the USB
  controller to full speed, so there are no split transactions. It has not been tried: the setting
  cannot be changed without a reboot. Test it with the memory-heavy load above before relying on it.
- Keep the other processes light. The API polls the core's hardware every 2 seconds and its log every
  second, and builds a new HTTP client for each request, so 17% of its Python time goes on making
  SSL contexts for a local socket. That is wasteful on its own, though cutting the polling did
  not stop the glitches.

To see whether a glitch is the process holding the audio thread up, set
`MOREOPENREPEATER_DIAG_LATENCY=1` in `core.env`. The core then logs, next to the glitch lines, a
garbage collection over 10 ms, a thread held 15 ms or more, and an event loop 15 ms or more late. It
is off by default and only logs when a threshold is crossed.

### Also found

- **Device loss.** With the AIOC unplugged and plugged back in, the core's own health reported
  `audio.engine` failed within 10 seconds, but Docker kept showing the container healthy for a minute or
  more, nothing restarted it, and the AIOC came back as `hidraw2` instead of `hidraw0`, so the core's
  PTT path no longer existed and nothing said so. It needs a stable udev name for the PTT device and the
  core exiting when audio stays failed, so that Docker restarts it.
- Raspberry Pi OS boots with `cgroup_disable=memory`, so memory limits and `docker stats` memory do not
  work until `cgroup_enable=memory cgroup_memory=1` is added to `/boot/firmware/cmdline.txt` and the Pi
  reboots. ADR 0005's per-module resource budgets need that.
- `docker kill` does not restart the container under `unless-stopped`. A crash of the process does
  (tested: the core was back and followed again in about 24 s).

**Stopping mid-transmission:** `docker stop` sent while the repeater was sending its ID (0.2 s in)
logged `audio engine stopped` within 0.2 s, and the container exited 0 in 0.95 s. That is the core's
own shutdown path releasing the audio device and PTT; nothing here observed the radio itself unkeying.

Still to measure: the PTT line observed independently when the container stops (it needs a scope or a
second receiver), the 3B+ with `dwc_otg.speed=1`, and a Pi 4 or 5. A stable udev name for the PTT
device, multi-arch publishing, and the API, `logd` and module containers are later slices.
