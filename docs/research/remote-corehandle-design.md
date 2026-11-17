# Stage 3 slice 7c design: the API against a remote core

Status: proposal for maintainer review. Docs only. This PR changes no code. It
follows the merged core control socket (slice 7a, read only status and health;
slice 7b, `POST /speak` with priority). Design context:
[ADR 0005](../adr/0005-container-module-runtime.md) ("Control transport") and the
Stage 3 design (PR #262).

## Goal

The API and dashboard run in their own process and talk to a core that runs
alone (`moreopenrepeater-core`). The single-process service stays the default
and stays supported. A setting selects the remote mode. Stopping, restarting or
crashing the API must not touch the core, and a core that is down must not
break the API's startup.

## What the measurement says

`CoreHandle` (merged in #265) has 65 members. I split every use outside
`src/api/service` into who calls it:

- **26 members are used by core-side code** that stays in the core process and
  keeps using the service in process: the audio engines, GPIO and TX fan,
  autopatch, AllStar and DMR audio, the link radio, the remote base, the core
  loops. These need no remote version at all.
- **28 members are used by the routes, `app.py`, backup and the support bundle**,
  and about 10 more by API-side modules (net, airport weather, site sensors, Home
  Assistant, mailbox, metrics, transcripts). The union that exists **only** on
  the API side is 27 members.

So a remote handle is about 27 members, not 65, and they fall into five groups:

| Group | Members | How a remote handle serves it |
|---|---|---|
| Live reads | `snapshot`, `subscribe`, `unsubscribe`, `wall_now`, `linked_nodes` | A local cache fed by the core's event stream (below). Instant, never blocks |
| Config reads | `config`, `saved_config`, `list_macros`, `list_announcements`, `next_announcement_run`, `announcement_clip`, weather reads | The same cache, refreshed when the core reports a change |
| Writes | `update_config`, `add_macro`, `delete_macro`, `save_announcement`, `delete_announcement`, `import_snapshot`, `clear_lockout`, `set_net_active` | A request to the core, answered with the new state |
| Simulate | `simulate_cos`, `simulate_ctcss`, `simulate_dtmf`, `simulate_remote_keyed` | A request to the core. Debug only |
| Objects | `programming`, `links`, `weather_tracker`, `export_snapshot` | `programming` and `links` are rich objects the routes call into (about 12 call sites). They are the hard part and come last |

## Problems to solve first

**1. The handle is synchronous and a socket call is not.** Many routes are
`async def` and call service members directly. A blocking socket call there
would stall the API's event loop. The API loop is not the radio, but a stalled
dashboard is still a bad outcome. Two rules:

- Reads never touch the socket. They read the local cache, so they stay
  instant.
- Writes go through an async client. Their route handlers become
  `await`-based or run in the thread pool. This is the one real code change in
  the routes, and it is mechanical.

**2. The routes validate, the core must too.** Config is validated by pydantic
in the API before it reaches the service. The core has its own check at start
(slice 6). A change that arrives over the socket gets the same check inside the
core, so a bug or a hand-written client cannot send the controller a zero
time-out.

**3. The trusted channel carries secrets; the contracts reject them.** The
`config.snapshot` contract refuses credential-like keys, on purpose, for
messages that go to untrusted modules. The API-to-core channel is a different
trust level (same machine, same group, file permissions). It needs its own
message, `config.state`, that carries the full configuration, and the docs must
say plainly why the two differ. The secrets never go to modules.

**4. Live updates.** `subscribe` today hands out an asyncio queue of status
snapshots, fed in process. Remotely this becomes server-sent events on the same
socket (`GET /events`): `status`, `config.changed`, and a periodic heartbeat. The
cache marks itself **stale** if the heartbeat stops, so the dashboard can say
"core not responding" and show a last-known value with its age (the `Freshness`
contract already exists for this).

## Proposed shape

- Split the protocol by who uses it: `ApiCoreHandle` (the 27) and the core-side
  rest. This is a type-level change like #265.
- `RemoteCore` implements `ApiCoreHandle` over the control socket. The existing
  in-process `RepeaterService` already satisfies it.
- `MOREOPENREPEATER_CORE_SOCKET` set on the API process selects the remote
  mode. Unset, nothing changes.
- New core endpoints, each read or write with the contract messages:
  `GET /config`, `PUT /config`, `GET /events`, then macros, announcements,
  lockout, net, snapshot import and export.
- When the core is unreachable: reads serve the cache marked stale, writes fail
  with `503` and a clear message, and the API keeps retrying in the background.
  The API starts even if the core is not up yet.

## Slices (each a small PR, each with tests)

| # | Slice | Needs a Pi |
|---|---|---|
| 7c-1 | `ApiCoreHandle` protocol split. Types only | no |
| 7c-2 | `GET /events` on the core and the `RemoteCore` cache: status and heartbeat, read only. The dashboard shows live status from a remote core | to confirm |
| 7c-3 | `GET`/`PUT /config` with the core-side check, `config.state` contract, async write path | yes |
| 7c-4 | Macros, announcements, lockout, net, simulate | yes |
| 7c-5 | Snapshot import and export (backup and restore) | yes |
| 7c-6 | `programming` and `links` objects | yes |
| 7c-7 | The remote mode documented and a unit for the API alone, off by default | yes |

## How to test it without hardware

A **compatibility suite**: run the existing API route tests twice, once against
the in-process service and once against a `RemoteCore` talking to a real
in-process core over a real Unix socket. Any behavior difference between the two
is a bug in the remote path. This is the strongest check available in
simulation and is part of slice 7c-2 onward. It also covers the failure cases:
core stopped mid-request, core restarted, core slow, core never started.

## What must be shown on a real Pi (not in simulation)

1. Stop the API during a transmission: PTT, the time-out and the ID continue.
2. Kill and restart the core while the API is up: the API recovers, and the
   dashboard shows the stale state meanwhile.
3. Change config from the dashboard in remote mode and see the radio apply it.
4. The group permission setup between the API and core users.
5. Audio glitch counters under dashboard load.

## Rollback

Unset `MOREOPENREPEATER_CORE_SOCKET` on the API and start the normal service.
Every slice leaves the in-process path untouched and the default.

## Decisions for the maintainer

1. **Split the protocol** into `ApiCoreHandle` and core-side members. Yes?
2. **Write path.** Make the write routes await or thread-offload (mechanical,
   touches about 12 route handlers), or keep a sync client in the thread pool?
   I recommend awaiting.
3. **`config.state`** as a separate message for the trusted channel, with the
   rule that credentials never go to modules. Agreed?
4. **Scope for the first release.** Is the remote mode needed through 7c-6, or
   is read-only live status (7c-2) enough to show the split on a Pi first and
   decide after? I recommend stopping at 7c-2, running the Pi checks, then
   continuing.
