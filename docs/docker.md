# Running the core in Docker (experimental)

Stage 3 of the modular migration ([#229](https://github.com/gmisner/moreopenrepeater/issues/229)).
This runs the **core alone** (see [core-only.md](core-only.md)) as a container,
the way [ADR 0005](adr/0005-container-module-runtime.md) plans for every install.
It is off by default, `install-pi.sh` does not use it, and the normal service is
unchanged. There is no dashboard or API in this container yet.

Files:

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
  read-only root, no capabilities and `no-new-privileges`.
- Gets `/dev/snd` and one `hidraw` node (`MOR_PTT_HIDRAW`, default `/dev/hidraw0`).
  The group numbers for `audio` and `dialout` are the same on Debian and Pi OS;
  the `gpio` group number differs, so it is `MOR_GPIO_GID`. GPIO pins are not
  mapped yet: add `/dev/gpiochip0` under `devices:` if you use them.
- Writes only to `/data` and the control socket volume.
- Uses **host networking** for now. The link audio paths (AudioSocket on
  `127.0.0.1:9092`, USRP) go over loopback to a host Asterisk. This goes away when
  AllStar is its own container.

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

## Measured on a Pi, and not yet explained

On a Pi 4 (Debian 13, AIOC, 4 cores) the container starts, finds the sound card and
the CM108, keys for the scheduled ID and unkeys, and is followed by `RemoteCore`
through the volume's socket. One result is **not good yet**: while idle the
container logs a pair of `audio glitch` lines (a sound card receive overrun and a
transmit underrun, "not transmitting") about every one to four minutes. The same
core run directly by systemd on the same Pi, over nine minutes, logged none. Turning
off the Piper server did not change it, and the scheduled ID in the container
transmitted without a glitch, so the cause is something about running in the
container. Candidates to try: Docker's default seccomp profile, the container's CPU
weight (the systemd unit sets `Nice=-10` and `CPUWeight=200`), and the log driver.
Do not rely on the container for a repeater people use until this is understood.

Also found:

- Raspberry Pi OS boots with `cgroup_disable=memory`, so memory limits and
  `docker stats` memory do not work until `cgroup_enable=memory cgroup_memory=1`
  is added to `/boot/firmware/cmdline.txt` and the Pi reboots. ADR 0005's
  per-module resource budgets need that.
- `docker kill` does not restart the container under `unless-stopped`. A crash of
  the process does (tested: the core was back and followed again in about 24 s).

Still to measure: memory and CPU on a Pi 3B+, the PTT release when the container is
stopped mid-transmission, and device loss. A stable udev name for the PTT device,
multi-arch publishing, and the API, `logd` and module containers are later slices.
