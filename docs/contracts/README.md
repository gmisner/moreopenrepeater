# Message contracts (Stage 2 of #221)

Status: draft. This is plain data and test fixtures. Nothing in production
imports `src/contracts` yet, so it changes no core, audio, PTT or hardware
behavior. Design context: [ADR 0005](../adr/0005-container-module-runtime.md).

## What is here so far

| Piece | Where | Schema |
|---|---|---|
| Common header (IDs, UTC time, order, source) | `contracts.Header` | [header.v1.json](schema/header.v1.json) |
| Structured error | `contracts.StructuredError` | [error.v1.json](schema/error.v1.json) |
| Freshness (stale-data rule) | `contracts.Freshness` | |
| Structured log event | `contracts.LogEvent` | [log_event.v1.json](schema/log_event.v1.json) |
| Shared redaction filter | `contracts.redact` | |
| Version compatibility | `contracts.is_compatible` | |
| Transmit request from a module | `contracts.SpeakRequest` | [speak_request.v1.json](schema/speak_request.v1.json) |
| Core's answer to a request | `contracts.SpeakDecision` | [speak_decision.v1.json](schema/speak_decision.v1.json) |
| Health report | `contracts.HealthReport` | [health_report.v1.json](schema/health_report.v1.json) |
| Module manifest and startup announce | `contracts.ModuleAnnounce` | [module_announce.v1.json](schema/module_announce.v1.json) |
| Whole configuration and a change to it (API to core only, carries secrets) | `contracts.ConfigState`, `ConfigChange` | [config_state.v1.json](schema/config_state.v1.json), [config_change.v1.json](schema/config_change.v1.json) |
| One-shot commands and their result (API to core only) | `contracts.CoreCommand`, `CommandResult` | [core_command.v1.json](schema/core_command.v1.json), [core_command_result.v1.json](schema/core_command_result.v1.json) |
| Config delivery and acknowledgement | `contracts.ConfigSnapshot`, `ConfigApplied` | [config_snapshot.v1.json](schema/config_snapshot.v1.json), [config_applied.v1.json](schema/config_applied.v1.json) |
| Core status | `contracts.CoreStatus` | [core_status.v1.json](schema/core_status.v1.json) |
| Site location (fixed or GPS) | `contracts.SiteLocation` | [site_location.v1.json](schema/site_location.v1.json) |
| PTT, transmission start and end events | `contracts.core_event_adapter` | [core_event.v1.json](schema/core_event.v1.json) |

The non-blocking emit path and the per-module metrics registry are designed in
[emit-path.md](emit-path.md), with a standalone prototype (`contracts.Emitter`,
`contracts.Metrics`) and `scripts/benchmark_emit.py`.

## Rules

**Ordering.** Wall-clock time can jump (a Pi without a clock, an NTP step), so
order events by `boot_id` then `seq`. `seq` restarts at zero when `boot_id`
changes, which lets a collector tell a restart from lost events.

**Correlation.** One `correlation_id` follows a transmission, or one user
action, across modules. A reply sets `request_id` to the `message_id` it
answers.

**Transmit requests.** A module never keys the radio. It sends a
`SpeakRequest` with an idempotency key, a priority and a required `deadline`
(at most one hour after it was emitted). The core answers with a
`SpeakDecision`: `accepted`, `duplicate`, `expired` or `rejected`. Every core
applies two rules first, in `screen_speak_request`: an expired request is
dropped unspoken, and a key already accepted is never spoken twice. The
idempotency ledger forgets a key at its deadline and has a fixed size, so
memory stays bounded. The request text is message content and is not logged by
default.

**Transmission ID.** Only the core mints the `transmission_id`, when it accepts
a request or starts a transmission of its own (ID, announcement, link). It is
the `correlation_id` on every event for that transmission: `tx.started`,
`ptt.commanded`, `ptt.feedback`, `tx.ended` and any log event.

**PTT commanded is not PTT keyed.** `ptt.commanded` records that the core asked.
`ptt.feedback` records what was observed and how (`evidence`).
`observed_keyed: null` means "could not tell", which is not "unkeyed". Only
independent evidence (`current_sense`, `rf_detect`) counts as proof of RF
(`PttFeedback.proves_rf`). A `gpio_readback` shows the pin, not the
transmitter. Nothing should claim RF went out on a GPIO write alone.

**Health.** A module reports `ok`, `degraded`, `failed` or `starting`, plus the
checks behind it. The overall state may not look better than its worst check,
so a dashboard can trust the summary.

**Manifest and devices.** A module declares its capabilities, resource budget
and the devices it needs. A device is named by a stable identity (a udev
symlink such as `/dev/serial/by-id/...`, or a bus address such as `i2c:1:0x52`),
never `/dev/ttyUSB0`. Each device says whether it is exclusive, required, and
what the module does if it is lost. `find_device_conflicts` enforces one owner
per device: an exclusive device wanted by two modules is reported. Compose
overrides and the dashboard's ownership view are meant to be generated from
this. The module's `contract_version` is checked with `is_compatible`.

**Configuration.** The core starts from its own local copy and never waits for
a config message. `ConfigSnapshot` carries plain values only. A key that looks
like a credential is rejected, not silently replaced, so a config can never
lose a value without anyone noticing. Secrets go by name (`secret_refs`) and are
read from a Docker secret or environment file. A module answers with
`ConfigApplied`: `applied`, `restart_required` or `rejected` (with an error).

**Contract version 1.3.** `ProgrammingState` (the macros and scheduled announcements) and
`CoreCommand` and `CommandResult`: the API's one-shot commands
to the core (clear a lockout, net mode, the simulator), on the same trusted channel as
1.2 and not for modules.

**Contract version 1.2.** Two messages for the trusted channel between the API and the
core (below): `ConfigState` and `ConfigChange`. Nothing else changed.

**Contract version 1.1.** `core.status` gained optional fields (`ctcss_hz`,
`linked_nodes`, `net_active`, `gmrs_mode`, `callsign`), the things the dashboard
shows beside the state. They are optional, so a module built for 1.0 is
unaffected (`is_compatible` allows a lower minor).

**The API's channel to the core is different.** `ConfigState` (`GET /config`) and
`ConfigChange` (`POST /config`) carry the whole configuration, the autopatch PIN and
other credentials included, because the API manages those settings. They exist only
on the core's control socket, whose access control is its file permissions, and are
never sent to a module. `values` is excluded from the messages' `repr`, and nothing
may log it. The core checks every change itself (`core.config_boundary.check_change`):
unknown settings, wrong kinds of value and unsafe numbers are refused, and a refused
change applies nothing. The answer is a `ConfigApplied` pointing back at the change.
A change may carry `expected_version`; the core refuses it if the configuration has
changed since.

**Core status.** `ptt_commanded` is what the core asked for. `ptt_observed` and
`ptt_evidence` say what was seen, and `null` means "could not tell". The
existing dashboard `ptt_active` is an intent, not a measurement, so it maps to
`ptt_commanded`.

**Site location.** One source of truth, either `fixed` or `gps`. A GPS location
carries a fix quality and freshness, and `usable(now)` is false for no fix or a
stale fix, so consumers stop or fall back instead of acting on an old position.
The coordinates are real; the log filter coarsens them.

**Names.** Event names and error codes are stable dotted lowercase
identifiers such as `ptt.commanded` or `tts.unavailable`. Variable data goes
in `context`, never in the name.

**Redaction.** `LogEvent.context` and `StructuredError.details` are redacted
when the object is built, so a sender cannot forget:

- keys that name a credential, token or PIN become `[redacted]`;
- message and audio content (text, transcripts, DTMF digits) becomes
  `[omitted]`;
- latitude and longitude round to one decimal (about 11 km), grid squares cut
  to four characters;
- `Bearer ...` values and `user:pass@` URLs are masked inside strings;
- values that are not plain JSON become a type placeholder, never `repr()`;
- depth, item count and string length are capped.

Redaction is key-name based, so it is a safety net, not a license to log
secrets under a harmless key name.

**Versions.** Each message has an integer `schema_version`. The contract set
has a `major.minor` version that a module manifest declares. Adding an
optional field is a minor change. Removing or retyping a field, or changing
what it means, is a new major. A module is compatible when the majors match
and its minor is not newer than the core's.

## JSON Schemas

The files in `schema/` are generated:

```
PYTHONPATH=src python -m contracts.schema
```

A test checks that the field names and required fields still match the
models. It does not compare the whole file, because Pydantic words other parts
of the schema differently between releases.
