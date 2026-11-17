# Stage 3 design: extracting the autonomous core (#229)

Status: proposal for maintainer review. Docs only. This PR changes no code, no
control behavior and no hardware behavior. Parent epic: #221. Related:
[ADR 0005](../adr/0005-container-module-runtime.md) and the stage 1 inventory
(PR #244), whose finding numbers (F1, F3, ...) are used below.

## What Stage 3 must prove

1. Stopping the API process does not interrupt local repeat, ID, time-out
   timer or PTT.
2. The core starts from a local copy of its configuration with every other
   component down.
3. Nothing a user can reach today stops working, and one setting flips back to
   the single-process mode for rollback.

Item 1 can only be shown on a real Pi with a real PTT line. Simulation can show
the logic, not the result.

## What "core" is

By the inventory (section 3 and 4), these are real-time or gate PTT, repeating,
ID or time-out, so they belong in `core`:

- the controller tick (50 ms) and `RepeaterService`
- the audio engine, DSP and PTT/COS hardware (CM108, GPIO, serial)
- the clock-driven announcement and ID loops (they must keep running with the
  API down)
- the AllStar and DMR audio senders that carry the lost-unkey guard (F11)
- the link radio
- the watchdog, and a local copy of the config and state

Everything else stays in `api` or moves to a module: the dashboard and routes,
auth, backups, updates, notifications, hub polling, recordings, transcripts,
APRS receive, and weather fetching (which becomes the `geo` module and asks the
core to speak through `speak.request`).

Decided by the maintainer: autopatch belongs in `core`, because it holds PTT up
for a call. Still open: how its SIP leg is split from it, the mailbox, and the
monitor receiver.

## How coupled the API is to the core today

Measured on `main`, outside `src/api/service`:

- The API reaches the core through one object, `RepeaterService`, using about
  45 distinct members. The most used are `config` (33 uses), `saved_config`
  (15), `snapshot` (14) and `programming` (11). The rest are one to five each.
- Only 9 places reach through the service to `controller` directly.
- `src/controller/**` already imports only stdlib and itself (F2).

So the seam is narrow. The work is mostly in two places: untangling start-up
(F3 and the 800-line `AppRuntime` constructor) and the lifecycle, not in the
routes.

## The design

### 1. A narrow interface first, in the same process

Define a `CoreHandle` protocol containing exactly the members the API uses
(read status and config, change config, queue announcements, simulate events,
subscribe to updates). Today's `RepeaterService` implements it. The routes
depend on the protocol, not the class.

This is a mechanical refactor that changes no behavior. It is also the
compatibility adapter the issue asks for: later, a second implementation that
talks to a separate process can stand behind the same interface, and the API
code does not change.

### 2. Split the runtime in two, still one process

`AppRuntime` builds everything and its lifespan starts every loop. Split it into
`CoreRuntime` (tick, audio, watchdog, announcement and ID loops, link radio,
local config) and `ApiRuntime` (everything else). The single-process entry
point builds both, exactly as now. Start-up order stays the same.

### 3. A core entry point that runs alone

`python -m core` builds only `CoreRuntime`, loads its own local config copy and
state, and runs with nothing else up. It needs its own validation boundary,
because today config validation lives in the API (inventory section 4, edge 3).
This ships as an option and a separate unit, off by default. The
single-process mode stays the default until the hardware tests pass.

### 4. The API talks to a remote core

A second `CoreHandle` implementation sends commands and reads status and events
from the core process, using the Stage 2 contracts. The transport is the
decision ADR 0005 leaves open. Config changes flow API to core, and the core
persists its own copy first, so a core restart with the API down comes back with
the last config.

## Prerequisites that change control behavior

The inventory says the core needs these fixed before or during extraction. Each
changes control behavior and already has an issue:

| Issue | Change |
|---|---|
| #245 | background loops that stop permanently on an exception keep running or fail safe (tick, announcement, ID, link radio, AllStar and DMR senders) |
| #246 | a transmit limit below the controller, and watchdog coverage for the link radio |
| #248 | the start-up timeout mismatch and the updater rollback unit list |
| #252 | the missing PTT, time-out and shutdown tests |
| #250 | supervise the Piper and Kokoro child processes |

A core in its own process makes these matter more, because nothing else is
watching it.

## Proposed slices

Each is a separate small PR. "Needs approval" means it changes control or
hardware behavior or start-up order, per the #221 rules.

| # | Slice | Behavior change | Needs approval | Verified by |
|---|---|---|---|---|
| 1 | Characterization tests: with the HTTP layer absent, the service still repeats, IDs and times out on a fake clock | none (tests only) | no | simulation |
| 2 | Remove the import side effect: `import api.service` must not run `create_app()` and open the CM108 (F3) | none at runtime; changes import behavior | yes, low risk | simulation, run on a Pi |
| 3 | `CoreHandle` protocol and in-process implementation; routes use the protocol | none | no | simulation |
| 4 | Fix the loop hardening and the transmit limit (#245, #246, #248) | yes | **yes** | simulation, then a Pi |
| 5 | Split `AppRuntime` into `CoreRuntime` and `ApiRuntime`, one process | start-up order must stay identical | **yes** | simulation, then a Pi |
| 6 | `python -m core`, core-only unit, local config copy, validation boundary; off by default | new deployment path | **yes** | simulation, Docker, then a Pi |
| 7 | Remote `CoreHandle`, API in its own process | new deployment path | **yes** | Pi only |
| 8 | Make the split the default, keep the single-process flag one release | yes | **yes** | Pi only |

## Hardware verification needed (cannot be done in simulation)

On the smallest supported Pi with the real radio and PTT line, with the core
running alone:

1. Stop the API during a transmission: PTT stays as commanded, the time-out
   timer still runs, the ID still goes out on schedule.
2. Kill the core during a transmission: PTT drops (the CM108 or GPIO safe
   state), and the watchdog and systemd restart it.
3. Start the core with no network, no API, no TTS: it repeats and IDs.
4. Audio latency and CPU against the native baseline (ADR gate 1).

None of this is claimed anywhere until it is measured.

## Rollback

Slices 1 to 3 change nothing at runtime. Slices 5 to 7 keep the
single-process mode available behind one setting. Slice 8 flips the default
and keeps the flag for one release. Reverting any slice restores the previous
behavior.

## Decisions and open questions

Decided by the maintainer:

- **Autopatch is in `core`.** Its SIP leg is split out when that slice is
  designed.
- **Scheduled announcements stay in `core`.** Weather announcements arrive as
  `speak.request` from the `geo` module.

Still open:

1. **Order.** Should the hardening fixes (slice 4) go before the runtime split,
   as shown, or alongside it?
2. **Mailbox and monitor receiver.** In `core` or `api`?
3. **One process or two for audio.** This design keeps the audio engine inside
   the core process. Moving it to its own process is a larger step and is not
   proposed here.
4. **Approval.** Slices 1 and 3 need none and can start now. Which of the
   others do you approve, and when?
