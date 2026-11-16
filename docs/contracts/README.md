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

Still to come in later Stage 2 PRs: commands and events (PTT commanded versus
PTT feedback), `speak.request`, health, capabilities, config and status, the
non-blocking emit path design, and the per-module metrics endpoint.

## Rules

**Ordering.** Wall-clock time can jump (a Pi without a clock, an NTP step), so
order events by `boot_id` then `seq`. `seq` restarts at zero when `boot_id`
changes, which lets a collector tell a restart from lost events.

**Correlation.** One `correlation_id` follows a transmission, or one user
action, across modules. A reply sets `request_id` to the `message_id` it
answers.

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
