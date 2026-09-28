# moreopenrepeater

A modern, Python-native ham radio repeater controller for Raspberry Pi --
a from-scratch reimagining of [OpenRepeater](https://github.com/OpenRepeater/openrepeater)
with custom repeater-control logic (not a wrapper around Asterisk's `app_rpt`).

## Architecture

- **`dsp`** -- CTCSS/DTMF Goertzel-algorithm tone decode and encode. Pure
  functions/classes over numpy arrays; no I/O, no threads.
- **`controller`** -- the repeater state machine (idle / receiving /
  courtesy tone / hang time / timeout / transmitting ID), the DTMF
  command-macro dispatcher, and the timeout/hang/ID timers. Driven entirely
  by plain-data events and commands (`src/controller/events.py`) -- it has
  no knowledge of audio hardware or the network link, so it's testable with
  synthetic event sequences and a fake clock (`handle_event(event, now)` /
  `tick(now)`, no real `time.sleep`).
- **`audio_io`** -- sounddevice stream lifecycle (`AudioStream`/
  `AudioBlockPump`, a disciplined non-blocking PortAudio callback that only
  moves numpy blocks through queues) and a CM108-family USB sound-card GPIO
  driver (`CM108Interface`) for PTT output / COS input, confirmed against
  direwolf/SvxLink/uridiag's real wire protocol (5-byte hidraw write/read,
  not the HIDIOCSFEATURE/HIDIOCGFEATURE ioctls). Linux-only at the hardware
  layer (`/dev/hidrawN`); the GPIO bit-packing logic is unit tested via a
  fake in-memory device, no real hardware required. `AudioProcessor` is the
  per-20 ms-block logic of a live repeater (software COS by audio level,
  CTCSS or an external pin; DTMF; repeat audio with TX gain; clip playback),
  with no threads or devices so it's tested on synthetic signals.
  `AudioEngine` runs it against real devices: the PortAudio callback only
  moves blocks, a worker thread does the processing, and PTT follows what
  is actually being transmitted.
- **`playout`** -- turns the controller's `PlayAudio` clip names into samples:
  courtesy/timeout tones, CW or text-to-speech IDs (`say`, `espeak-ng` or
  `pico2wave`, whichever is installed), uploaded clips, and spoken
  announcements. Rendered clips are cached, so the controller knows each
  clip's real length and the live engine can start it without delay.
- **`wx`** -- National Weather Service active-alert polling
  (`api.weather.gov`), severity filtering and alert-to-speech text.
- **`link`** -- bridges to a local Asterisk + `app_rpt` sidecar process
  (used only as the AllStarLink/IAX2 network transport) via `AMIClient`
  (control events/actions) and `link.audiosocket` framing (raw PCM audio).
  **Verified against a real AllStarLink ASL3 instance** (Debian 12 arm64
  Lima VM -- see `spikes/audiosocket_asl3_spike.py` for the reproducible
  setup): `AMIClient` connects, logs in, sends actions, and reads events
  against live Asterisk; `link.audiosocket`'s framing correctly parses the
  UUID handshake from real `chan_audiosocket`, and proactively sending
  audio (mirroring how `audio_io` will behave in practice) satisfies
  `app_audiosocket.c`'s 2-second keep-alive requirement. This spike also
  caught a real bug: Asterisk interleaves unsolicited Events with action
  Responses on the same connection (e.g. a `FullyBooted` event landing
  right after login, ahead of the next action's response), which broke a
  naive "read the next message and assume it's the response" client --
  fixed by correlating responses via `ActionID` instead of read-order (see
  `tests/link/test_ami_client.py`'s interleaving regression test).
  `link.node_link.NodeLinkClient` bridges a real app_rpt node's AMI events
  to `controller.events` and back: it consumes `RPT_ALINKS` (re-triggered by
  app_rpt on every link connect/disconnect *and* every keyed-state flip --
  confirmed against app_rpt's own source, `apps/app_rpt/rpt_link.c`'s
  `rpt_update_links`/`__mklinklist`, and a live two-node link/unlink test)
  and diffs successive snapshots into `LinkStateChanged`/`RemoteKeyed`
  events; `SendLinkCommand` (from a matched DTMF macro) goes back out as an
  AMI `Command` action running `rpt fun <node> <command>` -- see
  `spikes/node_link_ami_spike.py` for the reproducible two-node setup this
  was verified against. Two more real bugs turned up doing this: (1)
  `AMIClient` silently kept only the last line of a multi-line `Command`
  response (e.g. `rpt stats`) because repeated `Output:` header lines
  overwrote each other in a plain dict -- fixed to collect repeats into a
  list; (2) FastAPI runs synchronous (`def`) path operations in a worker
  thread with no asyncio event loop, so the `SendLinkCommand` sink's
  `asyncio.create_task` call from inside `POST /api/simulate/dtmf` raised
  `RuntimeError: no running event loop` the first time a macro actually
  fired for real -- fixed by converting every request handler that mutates
  `RepeaterService` to `async def`, which is what actually gives them the
  "same event loop, no locking needed" guarantee `RepeaterService` was
  already documented (but not enforced) to rely on.
  **Still open**: `rxchannel = audiosocket/...` node-channel-driver
  integration (letting `audio_io` feed a node's actual repeated audio
  instead of only its control-plane events) hits an unresolved upstream bug
  in app_rpt's channel-state handling per the community's own testing --
  worth re-checking against a newer ASL3 release before relying on it.
- **`api`/`web`** -- FastAPI backend (`RepeaterService` runs a
  `RepeaterController` in-process on the asyncio event loop) exposing REST
  config/status endpoints, a `/ws/status` WebSocket pushing live state on
  every change, and `simulate_*` endpoints (COS/CTCSS/DTMF/remote-node-keyed)
  that inject synthetic events -- a dev/demo aid for whichever of
  `audio_io`/`link` isn't wired into a given deployment yet. `link` *is* now
  wired in for real: set `MOREOPENREPEATER_AMI_HOST` (+ `_PORT`/`_USER`/
  `_SECRET`/`_NODE`) and `create_app` starts a background task that connects
  `NodeLinkClient` to the local app_rpt node, feeds its translated events
  into `RepeaterService.handle_link_event`, and dispatches DTMF-macro
  `SendLinkCommand`s back out over the same connection -- reconnecting
  every 5s on a dropped connection. Deliberately not part of `RepeaterConfig`/
  the dashboard: AMI credentials are a deployment secret, not something a
  browser client should be able to read back via `GET /api/config`.
  `audio_io` is wired in too: `api.live_audio.LiveAudio` starts the
  `AudioEngine` when live audio is enabled on the dashboard, feeds detected
  carrier/CTCSS/DTMF into the controller, and carries out its PTT and clip
  commands (`MOREOPENREPEATER_CM108_HIDRAW` adds CM108 hardware PTT/COS).
  The `simulate_*` controls keep working alongside it.
  Frontend (`web/`) is a plain HTML/JS dashboard (no build step) served by
  FastAPI's StaticFiles, driven by that same WebSocket. Manually verified
  live: COS key-up correctly drives the state machine through `receiving`
  with a real-time push to the dashboard, config load/save round-trips
  through the REST API, and -- against the real Lima VM -- a real AMI
  connect/disconnect updated the dashboard's linked-nodes indicator live
  over the WebSocket in both directions, and a real DTMF macro reconnected
  two live app_rpt nodes (confirmed via the VM's own `rpt stats` DTMF
  counter and `Nodes currently connected to us` field).

## Feature parity with OpenRepeater

Researched OpenRepeater's actual feature set (it's SVXLink-based with EchoLink-only
linking, not Asterisk/AllStar as we'd assumed) and are porting the useful bits in over
several phases (see the plan file for the full breakdown):

- **Phase A -- CW/Morse ID (done)**: `dsp/morse.py` generates a standard PARIS-timing
  keyed CW tone from text; `RepeaterConfig` gained `callsign`, `id_mode`
  (`voice`/`cw`/`both`), `cw_wpm`, `cw_tone_hz`, exposed through the config API/
  dashboard form. `controller`'s `_enter_id` is unchanged -- it still just emits
  `PlayAudio(clip="id")`, and `playout.renderer` decides how that clip sounds.
- **Phase B -- DTMF macro management (done)**: `controller.macros.Macro` changed from
  holding a Python callable to plain JSON-serializable fields (`pattern`, `description`,
  `command`, `node_id`) so macros can be created/edited through the API, not just at
  Python startup; `RepeaterController.list_macros`/`set_macros` delegate to the
  decoder so callers never reach into private state. Dashboard gained a macros table
  with add/delete, backed by `GET/POST /api/macros` and `DELETE /api/macros/{pattern}`
  (adding a macro with an existing pattern replaces it). Manually verified live:
  add-macro form submit, and the table's delete button, both round-trip through the
  real API.
- **Phase C -- config/macro backup-restore snapshots (done)**: `RepeaterService.
  export_snapshot`/`import_snapshot` round-trip config + macros as a plain JSON dict,
  exposed via `GET`/`POST /api/snapshot`. Dashboard has "Download configuration" (native
  Blob-URL download) and a file-upload input that POSTs the parsed JSON back.
- **Phase D -- audio asset upload/management (done)**: `api.assets.AudioAssetStore` is
  a filesystem-backed store (`data/audio/<id>.wav` + a JSON sidecar index, no database)
  for uploaded courtesy-tone/ID/timeout-tone/custom clips, exposed via `GET/POST
  /api/assets`, `DELETE /api/assets/{id}`, and `GET /api/assets/{id}/audio` (multipart
  upload -- added `python-multipart` as a dependency). `RepeaterConfig` gained
  `courtesy_tone_asset_id`/`id_asset_id`/`timeout_tone_asset_id`, which the renderer
  uses in place of the built-in sounds. Dashboard has an upload form, a table with inline
  `<audio controls>` preview and delete, and the three config dropdowns populate from
  the live asset list. Manually verified live: uploaded a real .wav through the API,
  confirmed it rendered with a working preview player and appeared in the dropdown,
  assigned it, and confirmed the assignment persisted through a real config save.
- **Phase E -- structured logging + log viewer (done)**: `api.logging_config.
  configure_logging` sets up the `moreopenrepeater` logger with a rotating file handler
  (`data/moreopenrepeater.log`) plus console output; `controller.state_machine` logs
  every state transition at INFO via a new `_set_state` helper (replacing scattered raw
  `self.state = ...` assignments), and `api.service` logs every `simulate_*`/config/
  macro mutation. `GET /api/logs?lines=200` tails the file; the dashboard's new Logs
  panel polls it every 3s into a scrolling `<pre>` block. Manually verified live: a
  real `simulate_cos` call produced both a `service` and a `controller` log line,
  visible through the actual `/api/logs` endpoint and rendered in the dashboard panel.
- **Phase F -- auxiliary GPIO on `audio_io.cm108` (done)**: `CM108Interface` gained
  generic `set_gpio(pin, active)`/`read_gpio(pin)`; `set_ptt`/`read_cos` are now thin
  wrappers over them (`set_ptt(active)` == `set_gpio(self.ptt_pin, active)`),
  backward-compatible with existing callers/tests. Driver-only change -- no
  dashboard control for the spare GPIO pins yet.
- **Phase G -- APRS position/status beaconing (done)**: `link.aprs_client` implements
  APRS-IS login, the passcode checksum algorithm, and classic uncompressed
  position/status packet formatting -- verified against the official aprs-is.net spec,
  aprslib's reference passcode implementation, and hand-traced against the well-known
  "N0CALL -> 13023" reference value before trusting it. `RepeaterConfig` gained
  `aprs_enabled`/`aprs_server`/`aprs_port`/`aprs_callsign`/`aprs_lat`/`aprs_lon`/
  `aprs_comment`/`aprs_beacon_interval`; a background beacon loop in `api.app`
  (parallel to the tick loop, same `start_background_tick` gate so tests never make
  real network calls) connects and sends one packet per `aprs_beacon_interval`,
  logging failures rather than crashing the app. Dashboard has the full field set
  including an enable checkbox. Manually verified live: saved APRS config through the
  real form/API including the checkbox-unchecked-means-disabled edge case (a plain
  form-field iteration would silently miss unchecked checkboxes since browsers omit
  them from form submission entirely -- handled explicitly). Live beaconing against a
  real APRS-IS server was not exercised (optional per the plan, unlike the AllStar
  spike which was load-bearing for an architecture decision).
- **EchoLink linking**: researched in `docs/research/echolink.md`. The recommendation is
  to enable `chan_echolink` in the existing Asterisk sidecar first (EchoLink stations
  become ordinary app_rpt links, so `NodeLinkClient` should cover them), and write a
  native Python client (estimated 3-5 weeks) only if that path's gaps matter. Either
  way it's gated on getting a `-R` callsign validated by EchoLink.

## Beyond OpenRepeater

- **Persistent settings**: config, DTMF macros and announcements save to
  `data/state.json` on every change and load at startup.
- **Real transmit audio**: every clip the controller plays renders to real samples,
  and the dashboard's preview buttons play exactly what would go out on air,
  including unsaved changes.
- **Text-to-speech voice ID**, using whichever TTS engine is installed. The ID text
  can include `{callsign}`, which is spoken phonetically if you want it to be.
- **Scheduled announcements**: interval ("every 30 minutes") or weekly ("Tuesdays at
  19:55") messages, spoken or from an uploaded clip. They queue for a clear channel
  and never interrupt a user.
- **NWS weather alerts**: polls active alerts for the repeater's location, announces
  new ones at or above a chosen severity, optionally repeats them, and shows the
  current alerts on the dashboard with a Play button.
- **APRS map** (optional, needs internet): shows stations heard on APRS-IS within a
  set range of the repeater, with trails for moving stations, weather-station
  readings, and filters for repeaters, digipeaters, mobiles, fixed stations and
  weather. Active NWS alert areas are drawn on the same map. A DTMF macro can say
  how many stations are nearby and which is closest. It only receives; nothing is
  transmitted. Without internet, leave it off: the rest of the repeater doesn't
  depend on it. If the map tiles can't be reached, it falls back to range rings
  only, and the tile URL can point at your own tile server.
- **Airtime statistics**: every transmission, ID and announcement is logged to
  SQLite (`data/activity.db`, 400 days kept). The Activity page charts usage by hour
  and day, and counts kerchunks and timeouts.
- **Live audio on any sound device**, with software carrier detect (VOX or CTCSS)
  for interfaces with no COS wire, a live level meter, and CM108 PTT/COS when one is
  plugged in. Devices open at whatever rate they support; audio is resampled to
  16 kHz internally. `scripts/benchmark_audio.py` times the per-block work (about 1%
  of the real-time budget on a Mac).
- **Kerchunk filter**: a key-up must last a set time before the repeater comes up.
  Filtered kerchunks are counted on the Activity page.
- **CTCSS encode**: an optional sub-audible tone on everything transmitted, with
  received audio high-passed so an incoming tone isn't repeated alongside it.
- **DTMF local control**: macros can speak the time, read the weather alerts, play
  an announcement or any text, send the ID, start a parrot test, say which APRS
  stations are nearby, or turn the transmitter off and on (with a PIN in the macro
  code).
- **Parrot / echo test**: after the parrot macro, the next transmission is recorded
  instead of repeated, then played back.
- **Recordings**: optionally save every repeated transmission, with playback on the
  Activity page and automatic deletion after a set number of days.
- **Listen live**: stream what's on the air (or what the receiver hears) to the
  dashboard in the browser.
- **Users and roles**: admin, operator and read-only viewer accounts, plus an audit
  log of every change and DTMF command with who made it.
- **One-command Raspberry Pi install** (`scripts/install-pi.sh`), which CI runs on
  every push.
- **CI**: GitHub Actions runs the test suite on Python 3.11-3.14.

### Why a local Asterisk sidecar for linking?

AllStarLink's network protocol is IAX2 plus an undocumented, reverse-engineered
`app_rpt` extension for exchanging node-keying/link status. Reimplementing
that from scratch (framing, auth, jitter buffering, retransmission) would be
a multi-month project duplicating two decades of hardened Asterisk code. So a
minimal Asterisk + `app_rpt` instance runs locally as a dumb network modem --
no `chan_usbradio`, no DAHDI, no app_rpt DSP/state logic -- while 100% of
actual repeater behavior (COS, CTCSS/DTMF, courtesy tones, timers, macros,
state machine) is custom Python in `controller`/`dsp`.

## Status

Phase 1 (`dsp`, `controller`), Phase 2 (`audio_io`), Phase 3 (`link`), and
Phase 4 (`api`/`web`) are complete and unit tested, including a hands-on
spike against a real AllStarLink ASL3 instance (Debian 12 arm64 VM via
Lima) confirming AMI and AudioSocket wire-level interop, and a manual
browser check of the live dashboard. `link` is now wired into
`RepeaterService` for real (`NodeLinkClient`, gated behind
`MOREOPENREPEATER_AMI_HOST`) -- node connect/disconnect and remote keyup
drive the dashboard live, and DTMF macros dispatch real AMI commands, both
confirmed against a live two-node app_rpt link. Still open: the tighter
`rxchannel=audiosocket` node-channel-driver integration (letting
`audio_io` feed a node's actual audio, not just its control-plane events --
blocked on an unresolved upstream app_rpt bug per the community's own
testing). `audio_io` now runs the repeater on real sound devices, verified
on a Mac (BlackHole loopback and speakers); it hasn't been tried with a
CM108 and a real radio yet.
Raspberry Pi packaging is done too: `packaging/moreopenrepeater.service`
(systemd unit), `packaging/moreopenrepeater.env.example` (config, incl. the
new `MOREOPENREPEATER_HOST`/`_PORT`/`_DATA_DIR`/`_LOG_PATH`/`_AMI_*` env
vars `api/app.py` now reads), and `packaging/99-cm108.rules` (a udev rule
-- lifted verbatim from direwolf's own, vendor ID and all -- granting the
`audio` group access to the CM108's normally-root-only `/dev/hidrawN`).
Full walkthrough in `docs/raspberry-pi.md`, including a security note: the
env file defaults to binding loopback-only regardless. Verified locally end
to end: the console script honors all four new env vars (custom host/port,
custom data/log directories), confirmed by actually starting it with them
set and checking the log file landed in the right place.

**Sign-in** (`api/auth.py`, `api/users.py`): set both
`MOREOPENREPEATER_AUTH_USER`/`MOREOPENREPEATER_AUTH_PASSWORD` to require
signing in (setting only one is treated as a misconfiguration and refuses
to start); leaving both unset runs with no auth, matching every other
env-gated feature here (`link`/APRS are opt-in the same way), so the test
suite and dev workflow stay credential-free. With auth on, `/` redirects to
a `/login` page; `POST /api/login` issues an opaque token in an HttpOnly,
`SameSite=Strict` cookie backed by an in-memory `SessionStore` (12h TTL,
really revoked by `POST /api/logout`, cleared on restart). Browsers *do*
send cookies on a `new WebSocket(...)` handshake -- unlike a custom
`Authorization` header -- so `/ws/status` authenticates the same way as
every REST route, plus an `Origin` check since WebSocket handshakes aren't
covered by CORS. HTTP Basic still works for scripts (`curl -u`), but 401s
no longer send `WWW-Authenticate: Basic`, which would pop the browser's
native dialog over the login page. Failed logins are logged and delayed
by 1s. Admins can add more accounts (admin / operator / viewer) on the Users
page; viewers are refused any change server-side, and every change lands in
the audit log (`api/audit.py`).

The dashboard (`web/`) is still plain HTML plus ES modules with no build
step: a sidebar app with separate views for Dashboard (live state, linked
nodes, and a recent-activity feed), Activity (airtime charts), Timing,
Identification, Audio & tones (sounds, clip library and the live radio
interface), DTMF macros (add/edit/rename/delete), Announcements, Weather
alerts, APRS, APRS map, Simulator, Logs (filter/level/pause), Backup & restore, and for
admins Users and Audit log. Each settings view saves only
its own fields through `PUT /api/config`, with unsaved-change tracking.
Verified live in a real browser: login rejection/acceptance, WebSocket via
session cookie, every view's save round-tripping through the API, the
phone-width layout, and sign-out revoking API access.

## Installing on a Raspberry Pi

On Raspberry Pi OS Bookworm (or any recent Debian/Ubuntu):

```
curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash
```

It installs the system packages, creates a `moreopenrepeater` service user,
checks out the code in `/opt/moreopenrepeater`, sets up the CM108 udev rule,
writes `/etc/moreopenrepeater/env` with a generated admin password (and the
CM108's device if one is plugged in), and starts the systemd service. It
prints the password and how to reach the dashboard at the end. Run it again
to update. The manual steps, and how to reach the dashboard securely from
other devices, are in `docs/raspberry-pi.md`.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest

# Run the API + dashboard:
PYTHONPATH=src .venv/bin/python3 -m uvicorn api.app:app --reload
# then open http://127.0.0.1:8000
```

### Trying live audio on a Mac

[BlackHole](https://github.com/ExistentialAudio/BlackHole) (`brew install
blackhole-2ch`) is a virtual cable: whatever plays into it comes back out as
an input. To check detection end to end:

```
.venv/bin/python scripts/loopback_test.py
```

This plays a voice burst, DTMF `147#` and a 100 Hz CTCSS tone into BlackHole
and checks that the engine reports each one. To drive the whole repeater,
choose BlackHole as the input on **Audio & tones → Radio interface**, your
speakers as the output, and play audio into BlackHole from any app. Don't use
BlackHole as the output too, or the repeater will hear its own courtesy tone
and key itself.

macOS gives silence to apps without Microphone permission, so the app running
the server or script (Terminal, iTerm, Cursor...) needs it in System Settings
› Privacy & Security › Microphone. Restart the app after granting it.

For a real Raspberry Pi deployment (systemd service, not `--reload`), see
`docs/raspberry-pi.md`.
