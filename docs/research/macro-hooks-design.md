# Stage 3 slice 7c-6 design: macro actions that belong to the API

Part of [#229](https://github.com/gmisner/moreopenrepeater/issues/229). Read
[remote-corehandle-design.md](remote-corehandle-design.md) first. Nothing here is built.

## The problem

The core runs macros: a DTMF string arrives, the controller finds the macro, and
`Programming.run_action` does what it says. Most actions are the core's own (speak the
time, disable the transmitter, switch a GPIO). But eleven are **hooks** that the API's
modules plug into `service.programming`, and `run_action` calls them from inside the core:

| Hook | What it does | Where it lives today |
|---|---|---|
| `net_hook` | `net_start`, `net_end` | API (`api.net`) |
| `homeassistant_hook` | call a webhook | API |
| `metar_hook` | speak an airport's weather | API |
| `sensor_hook` | speak a site sensor | API |
| `mailbox_hook` | record, play and delete messages | API |
| `aprs_summary` | text to speak about APRS stations | API |
| `remote_base_hook` | tune the remote base | core side (needs the radio) |
| `gpio_command` | switch a CM108/GPIO output | core side (needs the pins) |
| `code_checker`, `codes_locked` | verify a one-time code | API (`api.control_codes`) |
| `audit_hook` | record who did what | API (`api.audit`) |

In one process these are function calls. With the core in its own process, the hooks that
live in the API become calls **from the core into another process**, which can be down.
That is the opposite direction from everything built so far (the API calls the core).

## Rules that settle most of it

1. **The core never waits on the API.** A macro runs inside the controller; a slow or
   missing API must not stall the tick or the audio (the same rule as the control socket
   in the other direction).
2. **An API that is down means the action does not happen, and the caller hears so.** Today
   a macro whose hook is `None` says "The mailbox is not available." Keep that wording.
3. **Fail closed for anything that authorises.** A macro that needs a one-time code is
   denied if the code cannot be checked.

## Proposal

**Stay in the core** (no change): `remote_base_hook`, `gpio_command`, and the actions
`Programming` already does itself. They need the radio or its pins.

**Events out, fire and forget** for the actions the API owns: `net_hook`,
`homeassistant_hook`, `metar_hook`, `sensor_hook`, `mailbox_hook`, `aprs_summary`.
The core emits a `macro.action` event on `GET /events` (`action`, `argument`, `source`,
`pattern`) and carries on. An API module subscribes and does the work, using the
commands that already exist: `queue_clip` to speak, `set_net_active` for a net.
`aprs_summary` becomes the same thing: the API builds the sentence and speaks it.
The core knows whether any event stream is open. If none is, it speaks "That is not
available" itself, as it does today for a missing hook. Delivery is at most once with no
acknowledgement: an event that arrives as the API restarts is lost, which for "speak the
weather" is acceptable.

**One-time codes move into the core.** This is the part that needs a decision. A macro
that needs a code is exactly the control you want when the dashboard is down
(`tx_disable` is one), so it must not depend on the API. The core would read
`data/control-codes.json` (the secrets file, already readable only by the service) and do
the TOTP check, the replay record ("accepted once") and the lockout counters itself. The
API still enrolls users and writes the file; the core re-reads it when it changes.
Rejected alternative: the core asks the API to check, with a short deadline, failing
closed. It keeps one copy of the logic but makes a needs-code macro unusable whenever the
API is down, which is when it matters most.

**Audit** (`audit_hook`): the core writes what it did to its own structured log (the
logging design already makes that a first-class path) and also emits an `audit` event.
The API records the ones it hears into the dashboard's audit trail. While the API is down
the trail has a gap; the core's log does not. When `logd` exists, the API reads the gap
from there.

## What it costs

- A new event kind and an API-side subscriber (`macro.action`, `audit`).
- Control codes in the core: the TOTP code moves to a module both sides import, and the
  core takes the secrets file as a read-only input. A one-time code that was accepted
  must not be accepted again after the core restarts, so the replay record needs to
  survive a restart (a small file in the data directory).
- The in-process app keeps working unchanged: the same hooks, set to the same functions.

## Slices

| # | Slice |
|---|---|
| 7c-6a | `links.recent_events` over the socket (the one read on `service.links`) |
| 7c-6b | `macro.action` events out, the API subscriber, "not available" when no stream is open |
| 7c-6c | One-time codes in the core, fail closed, replay record kept across restarts |
| 7c-6d | `audit` events and the API recording them |

## Decisions needed

1. **Events out, fire and forget** for the API-owned actions (the six above)? The cost is
   that an action can be lost if the API is restarting at that moment.
2. **One-time codes checked in the core**, with the replay record kept in a file, rather
   than the core asking the API? Recommended: yes.
3. **Audit** as a log plus an event, accepting a gap in the dashboard's trail while the API
   is down?
