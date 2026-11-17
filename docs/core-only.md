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

### `GET /events`

A server-sent event stream for a client that wants to follow the core (the API in
remote mode, in a later slice):

```
curl -N --unix-socket /run/moreopenrepeater/core.sock http://core/events
```

- It starts with the current `status`, then sends a `status` event whenever
  something an operator would see changes (at most ten a second; a burst is
  coalesced to the latest), and a `heartbeat` every 5 seconds.
- A `status` event is `{"status": <core.status contract>, "local": {...}}`.
  `local` (the last clip name and a timestamp) is for the API only: the clip name
  can carry the text of an announcement, so it is not in the module-facing
  contract.
- Countdowns (time-out, hang time, next ID) are not pushed every tick. A client
  carries them forward by the time since the event.
- At most 4 streams are open at once (a fifth gets 503) so they cannot use up the
  connection limit. A client that cannot take an event within 5 seconds, or that
  hangs up, is dropped, and it never slows the controller. Stopping the core ends
  every open stream.

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

### Priority

`priority` is `low` (10), `normal` (20, the default), `high` (30) or `emergency`
(40). It orders what is waiting. **A radio transmission always comes first**
(maintainer decision), so **it never interrupts anything:** a priority
only moves a clip ahead of the others in the queue, and it plays at the next idle
like any announcement. It does not cut off a user, the station ID or a
transmission in progress.

- Highest priority plays first; the same priority plays in the order received.
- The queue holds 10. When it is full, a request with a **higher** priority than
  the lowest one waiting takes the place of the oldest of that lowest priority
  (a warning is logged). Otherwise it is `rejected` as `speak.queue_full`.
  An emergency is never dropped to make room for another request.
- An accepted request can therefore be dropped later by a higher-priority one
  when the queue is full. It is logged, and the requester is not told.
- During a net that holds announcements, `emergency` is queued at once (to play
  at the next idle); every other priority is held until the net ends.
- A disabled transmitter refuses every priority, an emergency included.

Known gap: the `transmission_id` is not yet the correlation ID on PTT and
transmission events (those events are not emitted yet).
