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
| PTT, transmission start and end events | `contracts.core_event_adapter` | [core_event.v1.json](schema/core_event.v1.json) |

Still to come in later Stage 2 PRs: health, capabilities, config and status,
the non-blocking emit path design, and the per-module metrics endpoint.

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
