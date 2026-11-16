# Modular migration inventory (#221, stage 1)

Discovery for [#227](https://github.com/gmisner/moreopenrepeater/issues/227),
part of the #221 modular-framework epic. Documentation only: no code, config
or hardware behavior changed.

## Scope and method

- Snapshot: `main` at `1323ed8`. Four read-only passes over the repository
  (processes and failure propagation, devices and PTT, logging and metrics,
  network and state).
- **Nothing was run on hardware and nothing was measured.** Every statement is
  from reading code, tests, scripts and docs.
- Labels: **[C]** confirmed by reading code, **[I]** inferred from code,
  **[U]** could not be determined. A baseline measurement plan is in
  [section 11](#11-baseline-what-exists-and-what-is-not-measured).
- Line numbers are from the snapshot and will drift.

## 1. Findings that shape the design

Ordered roughly by effect on the radio path. Each is a candidate for an
issue or an ADR gate.

| # | Finding | Evidence |
| --- | --- | --- |
| F1 | **There is one process.** Controller tick, audio engine, links, weather, APRS, backups and the API all run in `moreopenrepeater-api`. An API crash takes the radio with it. [C] | `app.py:531-536`, `runtime.py:1112-1185`, `docs/raspberry-pi.md:3-5` |
| F2 | **`src/controller` is already isolated.** It imports only stdlib and itself. The cut to a FastAPI-free core is in `src/api`, not in the controller. [C] | imports of `src/controller/**` |
| F3 | **`api/__init__.py` imports the whole app at import time**, running `create_app()`. That opens the CM108 and unkeys PTT, loads `state.json`, and starts the log-shipper thread, as a side effect of `import api.service`. [C] | `api/__init__.py:1`, `app.py:493`, `runtime.py:592-727`, `live_audio.py:159-166` |
| F4 | **`tick_loop` has no try/except.** An exception kills TOT, ID, hang time and announcements for good while PTT stays as it was. Only the systemd watchdog recovers it, and only when run under systemd. [C] | `runtime.py:1117-1122`, `watchdog.py:87-105` |
| F5 | **No TOT below the controller.** The engine and hardware layers have no independent transmit limit; a played clip keys PTT by itself. The watchdog covers the main radio only, not the link radio. [C] | `processor.py:300`, `engine.py`, `watchdog.py`, `runtime.py:757-759` |
| F6 | **PTT status is intent, not confirmation.** `ptt_active` is set before any hardware write. `engine.transmitting` means the write did not raise. Nothing reads back PTT, RF or current. [C] | `facade.py:316-321`, `engine.py:254`, `metrics.py:90` |
| F7 | **Blocking work runs on the event loop that also runs the tick:** `fsync` on every JSON save, a synchronous SQLite audit write in the HTTP middleware, `systemctl` calls in the DMR status route, engine restart (thread joins, `amixer`, PortAudio open) on a config change, blocking DNS in two audio modules, and an activity-store lock that a scheduled backup can hold. [C] | `persistence.py:91-104`, `app.py:231`, `dmr.py:301-312`, `live_audio.py:604-633`, `activity.py:66-120`, `allstar_audio.py:104` |
| F8 | **Logging is synchronous and unbounded in two places.** All three handlers run on the calling thread. Two `.exception()` calls can fire at audio-block rate if a failure persists. No drop counters exist. [C] | `logging_config.py:42-56`, `engine.py:209`, `receiver.py:232` |
| F9 | **No redaction layer** between log calls and the log file, live stream, shipped logs, audit database and support bundle. Specific cases are tracked privately under `SECURITY.md`. The shared redaction filter is part of Stage 2 (#228). [C] | `logging_config.py`, `log_hub.py`, `support_bundle.py` |
| F10 | **Startup timeout mismatch.** The lifespan waits up to 240 s for Piper or Kokoro before `READY=1`, but `TimeoutStartSec=180`. A cold TTS load over 180 s would get the unit killed. [C] for the constants, [I] for the effect. | `piper_service.py:25`, `kokoro_service.py:24`, `moreopenrepeater.service` |
| F11 | **Several tasks die permanently on an unexpected exception:** `announcement_loop`, `long_id_loop`, `LinkRadio.run`, the AllStar and DMR `_send` loops (which also carry the 0.5 s lost-unkey guard), and `node_link_loop` for non-OSError errors. [C] | `runtime.py:941-948,1068,862-896`, `link_radio.py:203-212`, `allstar_audio.py:115,145-164`, `dmr_audio.py:148,179-200` |
| F12 | **An AMI drop while a remote is keyed does not release `RemoteKeyed`**, so the controller waits for TOT. [I] | `runtime.py:866,893-895`, `node_link.py:74` |
| F13 | **Piper and Kokoro children are unsupervised.** If one dies, synthesis falls back to the CLI, then espeak. Nothing restarts it. [C] | `piper_service.py`, `kokoro_service.py`, `tts.py:292-323` |
| F14 | **JSON state stores are single-process.** Each loads once and saves a whole file with no lock or reload. Sessions and token caches are in memory. Two processes sharing a data directory would overwrite each other. [C] | `persistence.py:68-102`, `auth.py:92` |
| F15 | **Pin conflict checks exist only at runtime and are asymmetric.** Only BCM 0 and 1 are reserved. Nothing blocks I2C, UART or the usual 1-wire pins, or cross-checks them against configured sensors. [C] | `models/types.py:18-19`, `live_audio.py:548-562`, `link_radio.py:63-107`, `monitor_receiver.py:35-44`, `tx_fan.py:37-65` |
| F16 | **Two fields are likely dropped from the API response.** `LiveAudio.status()` returns `ptt_error` and `retry_in_seconds`, the frontend reads them, but `AudioEngineResponse` does not declare them. Not exercised. [C] for the mismatch, [I] for the effect. | `live_audio.py:288-289`, `models/audio.py:134-151`, `audio/types.ts:26-27` |
| F17 | **The update rollback snapshot omits two units.** `update.sh` snapshots 7 units; the installer installs 9 (the dmr-restart pair is missing). [C] | `update.sh:89-91`, `install-pi.sh:487-490` |
| F18 | **The service user may lack the `i2c` group.** The ADS1115 reader returns `None` on a permission error, which would look like missing hardware. [I] | `install-pi.sh:359-363`, `ads1115.py:40-77` |
| F19 | **Documented Pi 3B+ timing is "TIGHT" by the benchmark's own rule.** The docs say p99 is about 6 ms of a 20 ms block (30%); the script calls anything over 25% TIGHT. [I] | `docs/raspberry-pi.md:1118-1121`, `benchmark_audio.py:100-102` |

## 2. Processes and units

### Installed units

| Unit | What it is | Started by | Talks over |
| --- | --- | --- | --- |
| `moreopenrepeater.service` | The only controller process. `Type=notify`, `WatchdogSec=30`, `TimeoutStartSec=180`, `Restart=on-failure`, `RestartSec=5`, `Nice=-10`, `CPUWeight=200` | boot, updater, restart helper | HTTP and WebSocket, default `0.0.0.0:8000`. MCP mounted in-process |
| `moreopenrepeater-restart.path/.service` | Root oneshot that runs `systemctl restart moreopenrepeater` | request file `data/restart-request` | request file |
| `moreopenrepeater-update.path/.service` | Root oneshot, 30 min timeout. Snapshots, runs `install-pi.sh`, rolls back on failure | request file `data/update-request` | request file, status dir `/var/lib/moreopenrepeater-update` |
| `moreopenrepeater-tailscale.path/.service` | Root oneshot for Tailscale install and `up` | request file | request file, status dir |
| `moreopenrepeater-dmr-restart.path/.service` | Root oneshot for DVSwitch install, enable and restart | request file `data/dmr-restart-request` | request file, status dir |
| Pi hardware watchdog drop-in | `RuntimeWatchdogSec=15s` | installer, Pi only | `/dev/watchdog0` |
| journald drop-in (`--protect-sd`), `99-cm108.rules` | config only | installer | none |

The update gate (`service-readiness.sh`) polls `GET /api/session` and requires
the same `MainPID` after 5 s. So the API being up is part of the release
health check. [C]

### External services (not unit files in this repo)

| Service | Link to the controller |
| --- | --- |
| `asterisk` (ASL3, optional) | AMI TCP 5038 (controller is client); USRP UDP, controller listens 34001, node 32001; AudioSocket TCP 9092 (controller listens, Asterisk connects per call); `/etc/asterisk/*.conf` edited directly via the `asterisk` group |
| `analog_bridge`, `mmdvm_bridge`, `md380-emu` (DVSwitch, optional) | USRP UDP, controller listens 34011, Analog_Bridge 34012; bridge to MMDVM on 31100 and 31103; MMDVM to the DMR master outbound UDP 62031. Packaged drop-ins redirect `ExecStart` to `/etc/moreopenrepeater/dmr/*.ini` |
| `rigctld` (optional, user-run) | TCP 4532, one short connection per tune |
| `tailscaled` | not controller IPC |

Their restart policies come from external packages. [U]

### Children of the main process

Piper (`127.0.0.1:8766`) and Kokoro (`127.0.0.1:8765`) HTTP servers, `ffmpeg`
for the optional Icecast stream, and short per-call subprocesses (`piper`,
`kokoro`, `espeak-ng`, `amixer`, `vcgencmd`, `git`, `systemctl`, `tailscale`,
`journalctl`). [I] The unit sets no `KillMode`, so children die with every
restart.

## 3. In-process concurrency

"Real-time" means the controller tick, the audio callback, or anything gating
PTT, repeating, ID or TOT.

| Thread or task | Does | Real-time? |
| --- | --- | --- |
| asyncio loop (uvicorn main thread) | **The controller runs only here**: tick every 50 ms, COS and DTMF events, config apply, every `async def` route | Yes |
| PortAudio callback | `put_nowait` and `get_nowait` only, counts drops | Yes, non-blocking by design |
| `audio-worker` | per 20 ms block: process, resample, **write PTT hardware**, emit events | Yes |
| `audio-cos` | polls CM108 COS every 20 ms | Yes |
| link-radio and monitor engines | own streams, worker and COS threads | Link: partly. Monitor: no |
| `watchdog` | pings systemd every 15 s if tick and audio progressed within 10 s | Safety net |
| `log-shipper`, hub pool, anyio threadpool, default executor | housekeeping, HTTP calls, TTS, recording, backups | No |

All background loops start in the lifespan (`runtime.py:1112-1185`). The
default executor is shared and unconfigured, so a slow TTS render, backup or
update check competes with everything else on it. [I]

Per-task exception handling is in finding F11. The full task table is in the
research notes for this PR (kept out of the repo to avoid drift).

## 4. Import structure and the cut to a FastAPI-free core

- `src/controller/**` imports only stdlib and itself. Reverse edges (packages
  that depend on it): `audio_io`, `link`, `playout`, and many `api` modules. [C]
- The wiring that makes the controller do anything lives in `src/api`:
  `service/` (`RepeaterService`), `live_audio.py`, `link_radio.py`,
  `allstar_audio.py`, `dmr_audio.py`, `autopatch.py`, `runtime.py`.
  `service/*` has no FastAPI import. [C]
- Edges to cut:
  1. `api/__init__.py:1` imports `app.py` (F3).
  2. `runtime.py:19-21` imports FastAPI and starlette. They are used for
     `HTTPException` as control flow in the announcement path (`:934-990`),
     auth dependencies (`:828-860`), and MCP attach (`:1287-1293`).
  3. Config validation lives in the API (`app.py:148-176`, pydantic). The
     core's `update_config` and `import_snapshot` bypass it, so a core-only
     process needs its own validation boundary. [I]
- Core-adjacent dependencies that are not FastAPI but would have to move
  together: `audio_io.patch`, `playout.renderer`, `wx.nws`, `playout.tts`
  (imported by `api/service/*`).

## 5. Failure propagation

What happens to local repeating, ID, TOT and PTT. "Loop" is the event loop
that also runs the tick.

| Component | If it raises | If it hangs or blocks | Memory exhaustion |
| --- | --- | --- | --- |
| Controller tick | Task dies. COS events still reach the controller, but TOT, ID, hang, courtesy and announcements stop; PTT flag unchanged. Watchdog releases PTT after more than 10 s and systemd restarts | A block over 10 s trips the watchdog. Under 10 s, timers are late by that amount | n/a |
| API request handlers | 500 for that request | `async def` handlers block the loop (F7). Sync handlers use the threadpool | no limits found |
| Config change | n/a | Runs on the loop: `fsync`, then possibly an engine restart (thread joins up to 1 s each, PTT-release sleep up to 0.35 s, `amixer` up to 5 s, PortAudio open) | n/a |
| Audio engine | A per-block exception is logged and the loop continues, but `last_block_at` still updates, so **repeated block failures are invisible to the watchdog**. Stream death: recovery on the loop with 5 to 60 s backoff; while down, no audio is repeated and the watchdog cannot trip | A hung hid write or `process()` leaves `last_block_at` stale, so the watchdog fires | OOM kills the whole process; CM108 keeps PTT until the next start unkeys it. No `MemoryMax` set |
| AllStar AMI client | Only OSError and ConnectionError reconnect; other errors kill the task (F11). Local repeating, ID and TOT unaffected | `connect()` and `send_action()` have no timeout. Route calls wrap in 5 s | `_events` queue is unbounded [I] |
| AllStar and DMR USRP audio | `_send` has no try/except; if it dies the lost-unkey guard stops too (F11) | blocking DNS in `start` | bounded deques |
| Autopatch and SIP | broad `finally` ends PATCH | an awaited step in the `finally` that hangs leaves the controller in PATCH with PTT asserted and no TOT (PATCH has none) [I] | bounded |
| TTS | falls back Piper HTTP, CLI, espeak; a built-in ID that cannot render falls back to CW | 90 s, then 30 s, then 30 s; one lock serializes synthesis and a voice download can hold it for 180 s | model resident in the child counts against the shared cgroup [I] |
| Mailbox, transcribe | loops catch and log | DTMF handlers and `remind()` do disk work on the loop; Vosk in the executor (GIL release [U]) | Vosk model in-process |
| Hub poller | route-level errors only | 5 s timeout, on demand only | per request |
| Notifier | per-channel errors recorded; `post` only schedules a task | 10 s per channel in the executor | rate-limited, bounded |
| Logging | synchronous on the calling thread, including the audio worker | SD or journal stalls propagate to the logging thread [I] | bounded queues |
| Link radio | `run()` has no try/except; if it dies, pump and link PTT updates stop | config apply runs PortAudio and `amixer` on the loop | n/a |

### Watchdog

`watchdog.py`: pings every 15 s if the tick and the audio engine progressed
within 10 s. On a stall it stops pinging for good, releases PTT, and records
the reason for the next-boot alert. From the constants, PTT release is about 10
to 25 s after a stall begins and the systemd kill about 25 to 40 s. [I] Not
watched: link radio, monitor receiver, HTTP responsiveness, AMI, USRP, DMR,
TTS, autopatch, and a not-running audio engine.

### Startup and shutdown

- Startup: units have no ordering against `asterisk` or the DVSwitch services;
  they are soft dependencies. Import runs `create_app()` (F3), then the
  lifespan runs **before the HTTP socket binds** (checked in the installed
  uvicorn, not in this repo). Audio and PTT are not live until the TTS child
  is ready (F10). The watchdog starts only after `READY=1`.
- Hard needs for repeating: the sound device, the configured PTT and COS
  hardware, a writable data directory, and the env file (no `-` prefix on
  `EnvironmentFile`, so the unit fails without it).
- Shutdown (`runtime.py:1186-1208`): tasks are cancelled but not awaited, then
  AllStar, DMR and autopatch stop (awaited, so a hang delays the next step),
  and PTT is released only after that. `timeout_graceful_shutdown` is not set
  and `TimeoutStopSec` and `KillMode` are not overridden. [U for the effect]

## 6. Devices and PTT

All hardware access is in the one Python process. Threads, not processes, are
the ownership units.

| Device | Opened by | Access | Recovery |
| --- | --- | --- | --- |
| Sound (PortAudio duplex), main radio | `AudioEngine` | read and write | `_recover_engine`, backoff 5 to 60 s, re-match by USB id. No PortAudio re-init found |
| Sound, link radio | second `AudioEngine` | read and write | one restart; a second death within 5 s stops with no retry |
| Sound, monitor | `InputStream` | read | none, no stream-death check |
| CM108 hidraw (PTT on GPIO3, COS on VOL_DN, spare pins) | one `CM108Interface` per process, one in-process lock | read and write | `_reopen` on ENODEV, polled every 30 s by health |
| Second CM108 (link) | looked up lazily | same | same logic; an explicit env path is not compared with the repeater's |
| Pi header GPIO (`/dev/gpiochipN`, raw ioctls, no GPIO library) | `LinuxGpioLine` | PTT and fan out, COS and monitor in | none |
| Serial RTS/DTR and CTS/DSR/DCD | `LinuxSerialPort` | write and read | none; no `TIOCEXCL` |
| CAT (rigctld) | TCP, 3 s timeout, executor | write | error string |
| DS18B20, ADS1115 | open per read | read | returns `None` on any failure |
| `amixer`, `/proc/asound`, `/sys/...` | subprocess or read | | |

No code opens an exclusive lock on a tty, hidraw or sound device. Exclusivity
is by config checks (F15) and the kernel only. [C]

### Who can drive PTT

There is **no single PTT authority** today. Intent comes from the controller,
from any clip that is playing (`transmitting = ptt or playing`), and from
`LinkRadioController` for the link engine. Each engine has one hardware writer
(`AudioEngine._write_ptt` under a lock). The CM108 handle is shared with the
spare-pin and fan writers, serialized by its own lock, and every write carries
all output bits.

### PTT behavior by event

| Event | What the code does | Tested? |
| --- | --- | --- |
| Tick exception | See F4 | wiring not tested |
| Audio block exception | logged, loop continues; PTT unchanged for that block [I] | no |
| Watchdog stall | `release_ptt` (retries at 0, 50, 100, 200 ms) and blocks re-keying for that process | yes |
| Link radio on watchdog stall | not released; its TOT and unkey live on the loop | no |
| SIGTERM | no signal handlers or `atexit`; uvicorn graceful shutdown reaches `live_audio.shutdown`, which unkeys only if transmitting or in error (`force=False`) | shutdown tested; real SIGTERM not |
| Lifespan teardown | no try/finally; an earlier failure (for example `autopatch.stop`) could skip `live_audio.shutdown` [I] | no |
| SIGKILL, OOM | nothing runs; CM108 keeps its outputs until the next start unkeys | start-up unkey tested for CM108 only |
| Crash with Pi GPIO PTT | kernel releases the line on fd close; electrical result not verified here. Docs assert it is low | no |
| Crash with serial PTT | HUPCL logic for active-high; tested against a fake port only | fake only |
| CM108 unplug | failed key never replays after reopen; unkey reopens with PTT low; critical alert | yes |
| Sound-card unplug | recovery waits for a confirmed idle PTT | yes |
| Serial adapter unplug | not handled explicitly [I] | no |
| TOT | in `RepeaterController` only; default 180 s; no upper cap in the API model. PATCH has no TOT | yes (controller) |

### "Command issued" versus "confirmed"

Every layer reports command success only. A CM108 write succeeding means the
kernel accepted the HID report. A Pi GPIO write is an ioctl with no readback.
Serial has no `TIOCMGET` readback. `read_inputs` could verify the CM108 GPIO
level but nothing uses it for PTT. No path reads RF power, current or a
self-carrier detect. Three statuses differ: `ptt_active` (intent),
`LiveAudio.set_ptt` (silently dropped when there is no engine), and
`engine.transmitting` (last write did not raise). The UI shows "PTT unknown"
when `ptt_error` is set.

### Not covered by any test

Runtime wiring from a stalled tick to `release_ptt`; tick-loop exceptions; real
SIGTERM through uvicorn; link-radio release on a watchdog stall; an engine-level
TOT or a clip longer than TOT; Pi GPIO and serial state after crash or unplug;
serial hotplug; a hung `os.write` to hidraw (the lock has no timeout); PortAudio
re-enumeration on real hardware; `PUT /api/gpio` against the PTT pin; startup
unkey for GPIO and serial. `tests/audio_io/test_pi_gpio.py` checks struct
layout only.

### Config fields that name pins and devices

Defaults: PTT GPIO17, COS GPIO27, link COS 23, link PTT 24, monitor 22, fan
GPIO26, CM108 fan pin 1. Validation at save time: BCM 2 to 27, a serial path
regex, CM108 spare pins limited to 1, 2, 4 to 8, `fan_cm108_pin` not 3,
sensor ranges. Runtime-only conflict checks are listed in F15. Gaps: no
reserved list beyond 0 and 1, checks are asymmetric, the fan does not check
serial lines, audio devices compare by name string, `tot_duration` has no
maximum, and the controller facet does not enforce "no pin 3" (only the API
model does).

### What the API and dashboard show

Devices, engine status, GPIO, link radio, monitor and sensor state, health
alerts for CM108 and PTT faults, and the support bundle's device listings. Not
shown: which component owns which device, the hidraw path per radio, the
gpiochip, any hardware PTT confirmation, or link-radio PTT beyond
`transmitting` and `error`.

## 7. Logging and metrics

- One logger, `moreopenrepeater`, `propagate=False`, level fixed at INFO
  (`logging_config.py`). Three synchronous handlers: a rotating file (2 MB x 3),
  stderr (journal), and the `LogHub` ring (2000 lines). Free-text format, local
  time, no timezone, no process role. No structured logging anywhere. No
  `QueueHandler`.
- 225 log calls in about 49 modules, 40 logger names. Several names are shared
  across modules (`audio`, `link`, `service`, `tts`, `monitor_receiver`).
- Nothing logs in `src/dsp/`, the PortAudio callback, `processor.py` or
  `resample.py`. Real-time-path log calls are transitions, with two exceptions
  (F8). Per-DTMF-digit and per-link-event info logs exist on the loop.
- `LogHub`: subscriber queues of 500 and a ship queue of 4000 drop silently
  with no counter. `LogShipper` is a daemon thread with a 10 s blocking POST
  and drops failed batches.
- `/api/logs` reads the file; `/api/logs/since` and the SSE stream read the
  hub; the MCP `recent_logs` tool uses the hub. All need sign-in.
- **Not captured:** Piper and Kokoro output (`DEVNULL`), uvicorn logs (likely
  stderr only, [I]), third-party library logs (`propagate=False`), native
  PortAudio and ALSA noise, `update.log` (separate file).
- `--protect-sd`: a journald drop-in (`Storage=volatile`, `RuntimeMaxUse=32M`)
  and the log path moved to `/run/moreopenrepeater`. Lines are written twice
  (file and journal), both in RAM in that mode. `MOREOPENREPEATER_PROTECT_SD`
  only affects the health report.
- **Redaction:** no generic redaction layer exists. See F9. Settings responses
  hide secret fields. Details of what can reach logs are tracked privately.
- **Correlation:** no request, trace or session ID is logged. Process-local IDs
  exist (`seq`, autopatch `call_id`, AMI `action_id`, net and clip ids) but none
  spans a transmission.
- **Metrics:** hand-rendered Prometheus text at `/metrics` (state, PTT and COS,
  linked nodes, audio glitch counters, 24 h activity totals typed as counters
  though they behave like gauges). No timing metrics: no tick duration, block
  time, loop lag or PTT latency. Health loop every 30 s feeds the notifier
  (30 min dedup, 12 per hour). Activity rows buffer for up to 60 s.
- **Support bundle** (`support_bundle.py`): runs `journalctl -u
  moreopenrepeater`, `journalctl -k`, `lsusb`, `udevadm` and `amixer`, and reads
  in-process objects (snapshot, engine status, openings, glitches, health).
  With the controller in another process or container: the in-process objects,
  the hub, `/metrics` and the `journalctl -u` unit name all stop working
  without an RPC or a shared volume, and under `--protect-sd` the log path is
  not visible to a second container. The README text says passwords are not
  included but there is no scrub step (F9).
- **Tests:** hub sequencing and auth, `configure_logging` into the hub, metrics
  text, support bundle file list and admin gating. Not covered: dropped lines,
  shipper failure, handler blocking, rotation, redaction, log flood.

## 8. Network

### Outbound (all stdlib `urllib` unless noted)

| Feature | Target | Timeout | Runs on |
| --- | --- | --- | --- |
| NWS alerts and zones | `api.weather.gov` | 15 s, no retry (next poll in 120 s) | executor |
| METAR | `aviationweather.gov` | 10 s, 10 min cache, stale up to 3 h | executor |
| ASL node list | `allmondb.allstarlink.org` | 30 s, 20 MB cap, hourly retry | `to_thread` |
| Update check | `api.github.com` | 15 s each (up to 2) | Starlette threadpool (UI) or executor (auto) |
| Alerts | ntfy, Telegram, SMTP, webhook (admin-set) | 10 s | executor per channel |
| Home Assistant | admin-set URL | 5 s | executor |
| Log shipping | admin-set HTTPS URL | 10 s | dedicated thread |
| APRS-IS receive | `rotate.aprs2.net:14580` | 20 s connect, 60 s read, backoff to 120 s | loop |
| APRS-IS beacon | same | **none** on connect or drain | loop |
| Hub polling | per-site HTTP via `httpx` | 5 s per request, up to 4 per site, on demand only | `to_thread`, pool of 8 |
| Transcription | `api.openai.com` by default | 120 s | executor |
| Piper and Kokoro | localhost | 90 s and 2 s probes | executor, probes awaited in lifespan |
| Icecast via `ffmpeg` | admin-set | **none set** | loop (subprocess) |
| AMI persistent and one-shot | `host:5038` | persistent: **none**; routes 5 s; one-shots 10 s | loop |
| rigctld | `host:4532` | 3 s | executor |

Also: `scripts/update.sh` runs `install-pi.sh` (git, apt, pip from PyPI, voice
and model downloads, the ASL3 package repository) with no per-command timeouts
and only the unit's 30 min bound; `scripts/tailscale.sh` and
`scripts/dmr-install.sh` fetch installers. SIP, EchoLink and DMR networks are
not spoken by this process: `sip_trunk.py`, `echolink.py` and `dmr.py` write
configuration for Asterisk and MMDVM. MCP makes no network calls. Map tiles
are fetched by the browser from OpenStreetMap.

Calls without timeouts or that block: see F7 and the rows above (APRS-IS
beacon, AMI `connect` and `send_action`, `gethostbyname` on the loop,
`ffmpeg`).

### Inbound

| Listener | Bind | Notes |
| --- | --- | --- |
| FastAPI HTTP and WebSocket | `0.0.0.0:8000` default, plain HTTP | authentication is optional; see the README and `docs/raspberry-pi.md` |
| `/mcp` | same port | needs an identity; refuses requests with an `Origin` header |
| AudioSocket | `127.0.0.1:9092`, only if AMI host is set | call UUID must match a known call |
| USRP AllStar / DMR | `127.0.0.1:34001` / `34011` | source-IP filter only |
| Piper / Kokoro | `127.0.0.1:8766` / `8765` | no auth, localhost by default |

Unauthenticated HTTP: `/metrics` (by design), `/api/session`, `/api/login`,
`/api/logout`, `/api/public/status` and `/ws/public/audio` (gated by
`public_page_mode`), the SPA routes and static files. `/ws/status` and
`/ws/audio` authenticate inside the handler.

## 9. Persistent state and multi-writer risk

`StateStore` is the JSON writer: load once, save the whole file through a temp
file, `fsync` and `os.replace` (atomic), owner-only by default, corrupt files
moved aside. No lock, no reload.

| Group | Files | Owner |
| --- | --- | --- |
| Config and accounts | `state.json`, `users.json`, `tokens.json`, `agent-access.json`, `control-codes.json` | `RepeaterService`, `UserStore`, `TokenStore`, `AgentAccess`, `ControlCodes` |
| Integration settings (contain credentials, owner-only) | `alerts.json`, `log-shipping.json`, `stream.json`, `hub-sites.json` | `Notifier`, `LogShipper`, `Streamer`, `HubSiteStore` |
| Operational | `nets.json`, `auto-update.json`, `run-state.json`, `mailbox/` | `NetMode`, `AutoUpdater`, `RunMarker`, `MailboxStore` |
| SQLite | `activity.db`, `audit.db`, `aprs.db`, `sensors.db` | `ActivityStore`, `AuditLog`, `StationStore`, `SensorHistoryStore` (WAL, buffered rows up to 60 s) |
| Media | `audio/`, `recordings/`, `monitor-recordings/` | `AudioAssetStore`, `RecordingStore` |
| Non-atomic writers | `audio/index.json`, `audio-device-ids.json` | `AudioAssetStore`, `audio_devices.py` |
| Models | `piper/`, `kokoro/`, `vosk-model/` | installer and engines |
| Outside `data/` | `/etc/moreopenrepeater/env`, `/etc/moreopenrepeater/dmr/*.ini`, `/etc/asterisk/*.conf`, `/var/lib/moreopenrepeater-*`, request files | installer, service, root helpers |

If an API process and a core process shared a data directory: every JSON store
would be last-writer-wins; sessions, tokens and users cached in memory would
not be shared; buffered SQLite rows would look stale to the other process; two
processes would rotate the same log file; and exclusive resources (PortAudio
and hidraw devices, GPIO and serial lines, UDP 34001 and 34011, TCP 9092 and
8000, Piper and Kokoro ports, rigctld) can have only one owner. Asterisk's
`modules.conf` is written both directly and via AMI. [I]

`/etc/moreopenrepeater/dmr/` is created `750 root:<service group>` by the DMR
installer while the service rewrites files there with a temp file and
`os.replace`. Whether the service can write on a real install is not verified.
[U]

## 10. Environment variables

All read in `src/` unless noted. Grouped by area:

- **Bind and auth:** `MOREOPENREPEATER_HOST`, `_PORT`, `_AUTH_USER`,
  `_AUTH_PASSWORD`.
- **Paths:** `_DATA_DIR` (read once at import), `_LOG_PATH`, `_BACKUP_DIR`,
  `_PROTECT_SD` (health report only).
- **Helper request files and status:** `_UPDATER`, `_UPDATE_REQUEST`,
  `_UPDATE_CHANNEL`, `_TAILSCALE`, `_TAILSCALE_REQUEST`, `_RESTART_REQUEST`,
  `_DMR_RESTART_REQUEST`, `_DMR_STATUS`, `_DMR_DIR`.
- **AllStar and phone:** `_AMI_HOST`, `_AMI_PORT`, `_AMI_USER`, `_AMI_SECRET`,
  `_AMI_NODE`, `_AUDIOSOCKET_LISTEN`, `_AUDIOSOCKET_ADDRESS`, `_USRP_LISTEN`,
  `_USRP_ADDRESS`, `_USRP_NODE`, `_ASTERISK_CONFIG_DIR`, `_ASTERISK_SHELL`.
- **DMR:** `_DMR_USRP_LISTEN`, `_DMR_USRP_PEER`.
- **Hardware:** `_CM108_HIDRAW`, `_LINK_CM108_HIDRAW`.
- **Integrations:** `_HOMEASSISTANT_TOKEN`, `_TRANSCRIPTION_API_KEY`,
  `_VOSK_MODEL`.
- **TTS:** `PIPER_VOICE`, `PIPER_DATA_DIR`, `PIPER_SERVICE_URL`, `KOKORO_HOME`,
  `KOKORO_SERVICE_URL`.
- **systemd:** `NOTIFY_SOCKET`, `WATCHDOG_USEC`, `WATCHDOG_PID`.
- **Other:** `OPENBLAS_NUM_THREADS` (unit and benchmark),
  `FORWARDED_ALLOW_IPS` (consumed by uvicorn).
- **Installer and updater only:** `REPO_URL`, `BRANCH`, `INSTALL_DIR`,
  `PIP_CACHE_DIR`, `STATE_DIRECTORY` and the `INSTALL_*` variables.

There is no env variable for log level, format or rotation size.

## 11. Baseline: what exists and what is not measured

| Tool | What it measures | Hardware |
| --- | --- | --- |
| `scripts/benchmark_audio.py` | wall time of `engine.process_one` per 20 ms block on synthetic audio (resample, COS, CTCSS and DTMF decode, CTCSS encode, repeat, clip mix). Reports mean, p99 and max against the 20 ms budget; OK under 25%, TIGHT under 60%. Excludes PortAudio callback and queue effects, event-loop lag, TTS and the API | none; meant to run on the target Pi |
| `scripts/loopback_test.py` | burst, DTMF and CTCSS through a loopback device; asserts `dropped_input_blocks == 0` | a real audio loopback device |
| `scripts/cm108_check.py` | functional CM108 check; **keys the transmitter** | a CM108 on Linux 5.11 or later |
| `scripts/service-readiness.sh` | start stability gate, not a latency measure | systemd |

Runtime counters: dropped input blocks, starved output blocks, input
overflows, output underflows, shown on the dashboard, `/metrics` and the
support bundle. **Not instrumented anywhere:** tick duration, per-block time,
event-loop lag, PTT or TX-start latency. CI runs pytest on 3.11 to 3.14 and an
installer job on an x86 Ubuntu runner; it does not run the benchmark.

Existing recorded numbers (`README.md:297-299`, `docs/raspberry-pi.md:1118-1121`):
about 1% of budget on a Mac, about 20% on a Pi 3B+ (average about 4 ms, 99%
under 6 ms). These are reported in the docs; they were not reproduced here.

**Proposed baseline plan for the hardware spike (#234), not done:**

1. Run `benchmark_audio.py` on each supported Pi, native, idle and under
   synthetic load (a TTS render, a backup and an update check at the same
   time).
2. Add (as a separate, approved change) tick-duration, loop-lag and
   per-block-time metrics so the same numbers can be compared with the core in
   a container.
3. Record startup time to `READY=1`, restart recovery time, and idle CPU and
   RAM, native versus containerized, on the smallest supported Pi.
4. Record simulation and physical results separately.

## 12. Platform matrix (as documented)

| Item | Documented |
| --- | --- |
| Python | `>=3.11`; Pi OS Bookworm ships 3.11.2. CI runs 3.11 to 3.14 |
| Base OS | Raspberry Pi OS Bookworm 64-bit; README says "any recent Debian/Ubuntu"; the installer needs `apt-get` |
| ASL3 and DMR installers | Debian 12 and 13 only; CI checks the installer refuses ASL3 on Ubuntu |
| Architectures | "64-bit" Pi OS; CI is x86_64 only; no arm check in `install-pi.sh`; no armhf statement |
| Pi models | 3B+ benchmarked; Pi 4 appears in known-working configs and the transcript doc; Pi 5 appears only as a code comment about the `rp1` GPIO chip; Zero, 2 and 400 are not mentioned [U] |
| Kernel | 5.11 or later for CM108 hidraw reads |
| macOS | development and loopback testing |
| Docs | `docs/os-support.md` is on the Ubuntu-support branch, not yet on `main`. `docs/raspberry-pi.md:1112-1117` says CM108 and GPIO "haven't run on real hardware yet" while `docs/known-working-configs.md` lists a working setup, so one of them is stale [I] |

## 13. Container suitability (input to ADR 0005)

| Component | Proposed home | Notes |
| --- | --- | --- |
| Controller, audio engine, PTT, CM108, GPIO, serial | `core` | gates 1 to 5 in the ADR; needs the F3, F4, F5, F11 fixes before or during extraction |
| FastAPI routes, auth, config validation, MCP | `api` | needs its own config validation boundary (section 4) |
| Alerts, activity, audit, health, backups, updates, hub, sensors, streaming | `api` | JSON and SQLite stores need a single owner or a shared-state contract (F14) |
| Piper and Kokoro | own container or `core` sidecar | currently unsupervised children (F13) |
| `ffmpeg` Icecast | `api` child or small module | no timeout, argv password |
| AMI client, USRP link audio, autopatch, SIP | `allstar` module | PATCH holds PTT, so it must use the core lease (F11, F12) |
| DMR audio and DVSwitch | `dmr` module | already a sidecar |
| NWS, METAR, node directory | `geo` module | all stateless polling, good first candidate with the expiring `speak.request` |
| APRS-IS receive and beacon | `api` or a `packet` module | beacon connect has no timeout |
| Log hub and shipper | `logd` | hub and `/api/logs*` become `logd` consumers |

## 14. Unknowns

- Restart policies of `asterisk`, `analog_bridge`, `mmdvm_bridge`, `md380-emu`.
- Whether hidraw reads or writes can block indefinitely; PortAudio and `amixer`
  open times on target hardware.
- Electrical PTT state after SIGKILL or reboot for Pi GPIO and serial PTT.
- Whether PortAudio sees a replugged device without re-init.
- Whether Vosk releases the GIL; default executor size and saturation behavior.
- Uvicorn graceful-shutdown behavior with open WebSockets; effect of default
  `TimeoutStopSec` and `KillMode`.
- Real latencies of anything on the event loop; Pi 2, Zero and 5 support; 32-bit
  OS behavior.
- Whether `/docs`, `/redoc` and `/openapi.json` respond (not run).
- Where `httpx2` is imported from in `agents.py:31`.
- Whether the DMR directory is writable by the service on a real install.
- Whether frontend log pages parse line format; whether docs quote log message
  strings that a structured contract must preserve.

## 15. Suggested follow-ups

Each needs maintainer approval before any behavior change. None is done here.

- Wrap `tick_loop`, `announcement_loop`, `long_id_loop`, `LinkRadio.run` and the
  USRP `_send` loops so an unexpected exception is logged and the loop
  continues or the process fails safe (F4, F11).
- Add a transmit limit below the controller, and watchdog coverage for the link
  radio (F5).
- Add timeouts to AMI `connect` and `send_action`, the APRS-IS beacon, and
  Icecast (F7).
- Fix the startup timeout mismatch (F10) and the update rollback unit list
  (F17).
- Add redaction before any log or bundle leaves the process (F9).
- Move blocking disk and subprocess work off the loop (F7) and bound the two
  block-rate log calls (F8).
- Decide the `AudioEngineResponse` fields (F16) and the `i2c` group (F18).
- Extend pin validation to reserved pins and cross-role checks (F15).
- Add the missing PTT safety tests listed in section 6.
