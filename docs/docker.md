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
configured. The rest does not yet: selecting a DMR talkgroup, the AllStar node and DMR setup (they
edit the host's Asterisk and DMR files), and any audio tool that needs the sound card itself still answers 503
`core.hardware`; listening and the record and score tool work (see Listening below). The weather
macro (a caller dialling for the weather) is worded by the API, which does the NWS polling, and said by
the core; with the API away the caller hears that weather is not available.

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

The core records the activity history (receptions, airtime, IDs, kerchunks) in `activity.db` in
the data folder, because it is the one that sees every transmission, and keeps recording with the
API stopped. If you copy an installed Pi's data, copy `activity.db` too and the history carries over.
The dashboard reads it from the core. Two things follow: a backup made while the API runs apart from
the core holds an empty history file (the history stays in the core's data folder), and restoring a
backup does not put history back.

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
- Gets `/dev/snd` and one `hidraw` node, by the name `/dev/moreopenrepeater-ptt` (see **A stable name for
  the PTT device**; `MOR_PTT_HIDRAW` overrides it with a `/dev/hidrawN` path).
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
  by Docker. Both write one structured event per line, as JSON, to standard output
  (`MOREOPENREPEATER_LOG_FORMAT=json`, set in `compose.yaml`), and the `local` log driver rotates them
  (10 MB, 3 files each). `docker compose logs -f core` for the radio alone. This works with everything
  else down. To read them as lines:
  `docker compose logs --no-log-prefix core | jq -r '[.time, .module, .event, .message] | @tsv'`.
  A line looks like `{"schema_version":1,"time":"...","boot_id":"...","seq":41,"severity":20,
  "event":"legacy.audio","module":"core","module_version":"0.1.0","message":"audio engine started ...",
  "context":{"logger":"moreopenrepeater.audio"}}`. Code that has not been given a proper event name yet
  logs as `legacy.<area>` with its text as the message (bearer tokens and `user:pass@host` masked, cut to 200
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
  docker compose -f compose.yaml -f compose.protect-sd.yaml up -d
  ```

  `/var/lib/logd` becomes a 64 MB tmpfs (the quota drops to 48 MB to fit), so the history starts afresh at
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
| `audio.glitch` | info | `counter`, `count`, `state`, `transmitting` |
| `audio.engine_started`, `audio.engine_stopped`, `audio.stream_died` | info, info, error | rates for the first |
| `receiver.opened` | info | `duration_s`, `open_dbfs`, `peak_dbfs`, `ctcss_hz`, `after_tx_s` (never DTMF digits) |
| `dtmf.digit_received` | info | none: that a digit arrived, never which one (it could be part of a PIN or a code) |
| `command.applied` | info | `command`: the name of a `core.command` the API sent, never its parameters |
| `command.refused` | warning | `command`; `error_code` says why (`command.simulation_disabled`, `command.unknown_clip`, `command.queue_full`, ...) |
| `command.failed` | error | `command`, `exception` (the type, never its text); `error_code` `command.apply_failed` |
| `config.changed` | info | `settings`: the names of what was changed, never the values |
| `config.refused` | warning | `error_code` says why (`config.unsafe`, `config.unknown_setting`, `config.wrong_type`, `config.conflict`) |
| `config.failed` | error | `exception`; `error_code` `config.apply_failed` |
| `speech.accepted` | info | `topic`: the core took a `speak.request` |
| `watchdog.stalled` | error | `part` (`controller`, `audio engine`, `link radio audio engine`), `stall_s`; `error_code` `watchdog.stalled`. The core exits right after |
| `weather.scheduled_due` | info | none: the routine weather announcement came due |
| `weather.alert_announced` | info | `alert` (the kind, such as Flood Watch), `alert_id` |
| `weather.announce_failed` | error | `kind` (`scheduled` or `alert`), `exception`; `error_code` `weather.announce`. Never the text of the alert |
| `weather.check_failed`, `weather.zone_failed` | warning | `exception` (and `zone`); `error_code` `weather.fetch` |
| `aprs.beacon_sent`, `aprs.beacon_failed` | info, error | `server`, `port` (and `exception` when it failed) |
| `link.connected`, `link.connection_lost`, `link.drop_failed` | info, error, warning | `host`, `port`, `node`; `retry_s` and `exception` when it was lost. Never the AMI password |
| `autopatch.dial_requested` | info | none: a DTMF command asked for a call, not which number |
| `<area>.loop_failed` | error | a background loop hit something unexpected and carried on. `area` is `weather`, `link`, `mailbox`, `transcripts`, `net`, `updates`, `alerts` or `sensors`; `exception` is the type; `error_code` is `<area>.unexpected` |
| `autopatch.call_started`, `.call_connected`, `.call_ended` | info | `direction` (`outgoing`/`incoming`); `via` (`DTMF` or `dashboard`) when started; `result` and `talk_s` when ended |
| `autopatch.call_refused` | info | `direction`, `via` or `reason`; the words say why (not allowed, busy, off, wrong access code) |
| `autopatch.call_failed` | error | `direction`, `exception`; `error_code` `autopatch.call` |
| `autopatch.originate_refused`, `.hangup_failed`, `.dtmf_failed`, `.lookup_failed` | error / warning | `error_code` `autopatch.asterisk` |
| `autopatch.render_failed`, `.listening`, `.listen_failed`, `.connection_ended`, `.unknown_call` | | what they say; `call_id` for the last |

The autopatch events never carry a phone number (called or calling), an access code, a DTMF digit sent into
a call, a channel name (it contains the number) or speech text. The dashboard's audit trail records who
called whom; the log does not.

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
with `ATTRS{serial}`. The core is given the one path as `MOREOPENREPEATER_CM108_HIDRAW`, since the default
`auto` reads the host's hidraw numbers, which are not the container's, and no link radio interface is given
to it (`MOREOPENREPEATER_LINK_CM108_HIDRAW: "off"`).

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

A restart can only help if the container can start again: a PTT device
path that no longer exists stops it starting, which is why the PTT device has a stable name (see above). `restart: unless-stopped` covers crashes and exits, but not a manual `docker kill`
or `docker stop`. The API container is not restarted this way: it holds no device.

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
minutes was normal. The setting cannot be changed without a reboot,
and the installer should do it. The cost is that every USB device on the Pi is held to 12 Mbit/s,
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
