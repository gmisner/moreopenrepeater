# ADR 0005: Container-based module runtime

## Status

Proposed. Needs maintainer approval before any process, transport or hardware
change (issue #221). Nothing here is verified on physical hardware yet; the
gates at the end say what must be measured first.

## Context

Issue #221 asks for a modular framework where an optional integration failure
cannot stop local repeating, IDs, timeout handling or safe PTT control. Today
`moreopenrepeater-api` is one process that holds the controller, the audio
engine, GPIO/CM108, FastAPI and about twenty background loops
(`api/runtime.py` lifespan). Moving integrations out alone would leave an API
crash able to take the radio with it.

Constraints we have already agreed:

- The same architecture on every install (Pi OS, Debian, Ubuntu, x86
  servers), so there is one thing to support.
- Raspberry Pi stays a first-class target, including `--protect-sd`.
- Logging and troubleshooting are first-class and never block the radio.
- ASL3 and DVSwitch only ship Debian 12 and 13 packages
  (`docs/os-support.md`), which is why Ubuntu is unsupported for them today.

## Decision

1. **One runtime: Docker with Compose, on every install.** No Podman, and no
   systemd-versus-compose split. A thin host layer stays outside Docker: a
   systemd unit that starts the stack at boot, and the existing request-file
   pattern (`.path` units) for updates and restarts. The dashboard never gets
   `docker.sock`.
2. **Base image `python:3.13-slim-trixie`** (Debian 13) for Python services,
   pinned by digest, built multi-arch (`amd64` and `arm64`). Non-Python
   sidecars (DVSwitch, ASL3) use `debian:trixie-slim`. 64-bit Pi OS is the
   tested Pi baseline; `armv7` is undecided.
3. **Containers:**
   - `core`: controller, audio/DSP, PTT. Sole owner of radio, audio and PTT.
   - `api`: FastAPI, dashboard, config, alerts, monitoring and analytics,
     remote management, diagnostics, integration services.
   - `logd`: structured-event collector (see Logging).
   - One container per protocol or service module: AllStar, EchoLink, DMR,
     Winlink, Packet/Data, `geo` (weather alerts, METAR, other location
     services), `gps`.
4. **Platform services are never core dependencies.** The core starts from a
   local copy of its configuration and keeps repeating with the API, `logd`,
   any registry and every module down.
5. **Single owner per device (per address on shared buses).** Other modules
   get data through the module contract and never open the device.
6. **Module manifest** declares id, version, contract version, ports,
   dependencies, capabilities, resource budget, health endpoint and a devices
   section (stable udev name or match rule, access mode, exclusive owner,
   required or optional, safe state on loss). Compose overrides derive from
   it, and the dashboard shows device ownership.
7. **Transmit requests from modules** (for example a weather announcement) go
   to the core as expiring, prioritized, idempotent requests with a deadline,
   an idempotency key and a correlation ID. The core arbitrates and drops
   expired requests.
8. **One site-location source** (`fixed` or `gps`, with freshness). Weather,
   APRS, the map and other services read it and never touch the GPS device.
   Location is treated as sensitive in logs and support bundles.
9. **Host prerequisites stay on the host** and are checked by the installer:
   udev rules and stable device names, Pi device-tree overlays (I2C, 1-wire,
   PPS), UART setup, chrony and gpsd for GPS time, kernel modules.

## Logging

- Layer 1: every container writes structured JSON lines to stdout; Docker's
  `local` driver rotates them. Works with everything else down.
- Layer 2: `logd` receives events over a local Unix datagram socket through a
  bounded queue, stores them in SQLite with full-text search, a disk quota
  and retention (draft: 14 days routine, 30 days critical). If `logd` is
  down or full, the core drops events and counts the drops.
- No disk or network logging on controller ticks or audio callbacks. Audio
  metrics are aggregated.
- A per-transmission correlation ID follows the session across modules. PTT
  commanded and PTT feedback observed are separate events.
- Each module keeps an in-memory DEBUG ring that dumps on a fault or on
  request. Log level changes at runtime per module.
- With `--protect-sd`, logs live in RAM; a small bounded on-disk file keeps
  critical events only, and the UI says plainly when retention is volatile.
- Existing live SSE, MCP polling, HTTPS remote shipping and `/metrics`
  become consumers of `logd`; each module exposes its own metrics endpoint.
- ELK, Jaeger and full tracing stacks are out of scope. OpenTelemetry export
  is an optional, off-by-default extra.

## Considered options

- **All systemd, no containers.** Smallest Pi footprint, but it keeps the
  Debian-only limit on ASL3 and DVSwitch and leaves dependency drift to each
  install. Rejected for the "same everywhere" goal, pending the gates below.
- **Mixed: systemd on Pi, compose elsewhere.** Rejected: two runtimes to
  support.
- **Containers only for sidecars, core native.** Safer for the radio path but
  two architectures. Kept as the fallback if the core fails the gates.
- **Podman.** Rejected: rootless device handling differs and would reopen the
  support split.
- **In-process plugin interface only.** Cannot contain a crash or resource
  exhaustion (#221).

## Gates before approval

Each needs measurement on real hardware, not simulation:

1. Core in a container: audio latency and glitches against the native
   baseline on the smallest supported Pi.
2. Safe PTT: `docker kill` and daemon crash leave the transmitter unkeyed.
3. Docker daemon restart or crash does not stop the core (`live-restore`).
4. Realtime scheduling parity (`Nice`, `CPUWeight` today) and a watchdog
   replacement for `sd_notify`.
5. USB re-enumeration (CM108, sound card, GPS): whether container device
   nodes go stale and how the host helper restarts only the affected
   container.
6. `--protect-sd`: image storage and log placement.
7. Docker daemon and `logd` RAM and CPU on the smallest Pi.
8. Wheel availability for numpy, onnxruntime and vosk on each architecture.
9. Packet and Winlink radio access: mediated lease through the core or a
   dedicated radio (VARA is usually run under Wine; licensing and
   containerization not yet checked).

If the core fails a gate, fix the container setup or reopen this decision with
the measurements. Do not split into two architectures silently.

## Remaining shared failure domains

Host power, kernel, the Docker daemon on first start, a shared USB hub or
sound device, and the SD card. These are documented, not eliminated.

## Consequences

- Updates and rollback become image tag changes.
- Resource budgets, restart policy and health checks look the same on every
  install.
- The installer grows host prerequisite checks and a Pi-specific optional
  section (overlays, UART, PPS, RTC).
- A GPIO pin conflict check is needed when the GPS/PPS board is enabled
  (for example GPIO18 for PPS, GPIO14/15 for UART, GPIO2/3 for I2C).
