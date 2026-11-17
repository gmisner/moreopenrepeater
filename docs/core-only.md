# Running the core alone (experimental)

Stage 3 of the modular migration ([#229](https://github.com/gmisner/moreopenrepeater/issues/229)).
`moreopenrepeater-core` runs the repeater with **no dashboard, no API and no
accounts**. It is an option for testing the split. It is **off by default** and
`install-pi.sh` does not install it. The normal service is unchanged.

## What it runs

The controller tick, IDs and time-outs, announcements, text-to-speech
(Piper), the audio engine and PTT, the link radio, AllStar and DMR audio,
autopatch, GPIO and the TX fan, and the systemd watchdog. It starts from the
saved configuration file (`data/state.json`) and needs no network and no other
service.

## What it does not run

The dashboard and API, sign-in, backups, updates, alerts, weather fetching,
APRS, the hub, mailbox, transcripts, streaming, Home Assistant, and the
AllStar AMI link-control loop (link audio still works; link state shown in the
dashboard and macro-driven link commands over AMI do not). Scheduled weather
announcements need the weather module and do not run.

## Control socket

When `MOREOPENREPEATER_CORE_SOCKET` is set (the unit sets it to
`/run/moreopenrepeater/core.sock`), the core serves **health and status** as
HTTP/1.1 with JSON over that Unix socket. This is the transport chosen in
[ADR 0005](adr/0005-container-module-runtime.md). The bodies are the
[Stage 2 contracts](contracts/README.md) (`core.status`, `health.report`).

```
curl --unix-socket /run/moreopenrepeater/core.sock http://core/status
curl --unix-socket /run/moreopenrepeater/core.sock http://core/health
```

- `GET /status` and `GET /health` are read only. The one command is
  `POST /speak`: a `speak.request` in, a `speak.decision` out (below).
  Configuration changes are not available over the socket yet.
- Access is the file mode: the socket is `0660` in a `0750` directory, so only
  the core's user and group can open it. The API's user must share the group.
- `ptt_commanded` is what the controller asked for. `ptt_observed` is null and
  `ptt_evidence` is `none`: nothing here observes PTT or RF, so nothing is
  claimed.
- It is built so a client cannot hurt the repeater: it uses only the standard
  library, requests are small, every connection has a deadline, at most 16 are
  served at once, and a slow, silent, malformed or vanishing client affects only
  its own connection. If the socket cannot start (a file is in the way, a
  permission problem) the core logs it and runs without it.
- Unset, there is no socket.

### `POST /speak`

A module (the weather module, for example) asks the core to say something; the
core decides. Send a [`speak.request`](contracts/README.md) with
`Content-Type: application/json` and a `Content-Length` (16 KiB at most; no
chunked bodies). A well-formed request always gets `200` with a
`speak.decision`:

| status | meaning |
|---|---|
| `accepted` | queued, or held until the net ends if a net is holding announcements. The `transmission_id` is minted by the core |
| `duplicate` | that idempotency key was already accepted, so it is not spoken twice |
| `expired` | the deadline passed, before or while the speech was prepared. Nothing was spoken |
| `rejected` | with an `error`: `speak.transmitter_disabled`, `speak.queue_full`, `speak.render_failed` or `speak.render_timeout`. A rejected request can be retried with the same key |

A malformed body gets `400` with the field locations and error kinds only. The
text is content: it is never logged and never echoed back. Speech is prepared
in the executor with a 30 second deadline, so a slow synthesis never holds up
the controller tick.

Known gap: `priority` is validated but the core's announcement queue has no
priority yet, so it is not used. The `transmission_id` is not yet the
correlation ID on PTT and transmission events (those events are not emitted yet).

## Before you use it

1. Set the repeater up with the normal service first. The core alone cannot be
   set up, and it **refuses to start** if the saved configuration is missing,
   unreadable, not valid JSON, or has no callsign. It never falls back to
   defaults, because that would put an unidentified repeater on the air.
2. A time-out, ID interval, hang time or link or autopatch limit that is not a
   usable number is replaced by its default (logged as a warning), so a bad
   value can't key the transmitter and drop it at once.
3. Stop the normal service. The unit declares `Conflicts=moreopenrepeater.service`,
   so systemd does this for you, but check:
   `sudo systemctl stop moreopenrepeater`.

## Try it

```
sudo cp /opt/moreopenrepeater/packaging/moreopenrepeater-core.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl stop moreopenrepeater
sudo systemctl start moreopenrepeater-core
journalctl -u moreopenrepeater-core -f
```

To go back: `sudo systemctl stop moreopenrepeater-core && sudo systemctl start moreopenrepeater`.
Do not `enable` it until you have tested it on your hardware.

Exit code 2 means the saved configuration is unusable. systemd will not restart
it (`RestartPreventExitStatus=2`); the journal says what to fix.

## Status

Verified by tests only (simulation). Not yet run on a Raspberry Pi. See the
[design](research/core-extraction-design.md) for what must be shown on real
hardware: stopping the API during a transmission does not interrupt PTT, the
time-out or the ID, and killing the core drops PTT.
