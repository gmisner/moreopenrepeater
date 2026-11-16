# Emit path and metrics (Stage 2 of #221)

Status: design plus a standalone prototype (`src/contracts/emit.py`,
`src/contracts/metrics.py`). Nothing in production uses it. It changes no
core, audio or PTT behavior. Rules come from the radio safety invariants in
`AGENTS.md` and the logging section of
[ADR 0005](../adr/0005-container-module-runtime.md).

## The rule

A log call from a controller tick or an audio callback must never wait on a
disk, a socket, a collector or another thread, and must never fail. If logging
cannot keep up, events are dropped, counted and reported. The repeater keeps
repeating.

## Shape

```
controller tick / audio callback            drain thread (one per process)
        |                                          |
   Emitter.emit()  --- bounded deque --->  redact, build LogEvent, to JSON
   (tuple + append)                                |
                                    +--------------+---------------+
                                    |                              |
                              stdout (layer 1)         logd datagram socket (layer 2)
```

- **Hot path (`emit`).** Takes a timestamp and a sequence number, appends one
  tuple to a deque, returns. No I/O, no Pydantic, no redaction, no JSON, no
  lock on the normal path, and it catches every exception. On the development
  machine it costs about 1 microsecond at the median.
- **Drain thread.** Takes batches off the queue, redacts and validates each
  event into a `LogEvent`, and writes JSON lines to every sink. A sink that
  raises is counted and skipped. One bad event is counted and skipped, and it
  does not take its batch with it.
- **Sinks.** `stdout` is layer 1: it works with everything else down and Docker
  rotates it. `UnixDatagramSink` is layer 2: one event per datagram to `logd`,
  non-blocking, so a missing or full `logd` costs a counted error and nothing
  else. A line over 8 KiB is not sent.

## When it is full

The queue holds 1000 events by default.

| Incoming | Queue full | Result |
|---|---|---|
| DEBUG, INFO | yes | the new event is dropped and counted |
| WARNING or worse | yes, routine events queued | the oldest routine event is evicted instead |
| WARNING or worse | yes, all warnings | the new event is dropped and counted |

So a flood of routine events cannot push out a fault. The eviction scans the
queue, which is bounded at the capacity and only happens when the queue is full.

Loss is reported, not hidden. After a drop, the drain thread adds a
`log.dropped` WARNING event to the next batch with the counts by reason and
severity. It does not go through the queue, so reporting loss never costs
another event. Counters are also exposed as metrics.

## Levels and the flight recorder

Every event, DEBUG included, also goes into an in-memory ring (500 by default)
before the level check. `set_level` changes what reaches the sinks at runtime,
per module. `dump_ring` returns recent events of every level, for a fault dump
or a support request. The ring is never written to disk by this code.

## Metrics endpoint

Each module serves `GET /metrics` on its manifest `health_port`, in Prometheus
text format, from a `Metrics` registry. Metric names and label names are fixed
when a metric is created, so a caller cannot grow the series count by inventing
label values at runtime. The emit path registers:

| Metric | Type | Labels |
|---|---|---|
| `mor_log_events_emitted_total` | counter | `severity` |
| `mor_log_events_dropped_total` | counter | `reason`, `severity` |
| `mor_log_sink_errors_total` | counter | `sink` |
| `mor_log_queue_depth` | gauge | |

`GET /health` on the same port returns a `HealthReport`. Serving these two
paths is part of the Stage 4 module foundation, not this PR.

## Known limits (read before depending on this)

1. **What the counters mean.** `emitted` counts events the drain thread handed
   to the sinks. Events still queued, or below the level, are not in it.
   `dropped` counts events lost to a full queue or a failed validation. Both
   are updated under a lock that is never held across I/O.
2. **Worst-case latency is not the median.** A benchmark run on a development
   Mac showed a worst single call of 2 to 5 ms, against a median near 1 us.
   That is most likely the drain thread competing for Python's global
   interpreter lock. A 20 ms audio block leaves room, but a Pi 3B+ will be
   slower. `scripts/benchmark_emit.py` measures it; it must be run on the
   smallest supported Pi before any controller or audio code calls `emit`.
   If the worst case is too high there, the fix is to move serialization to a
   separate process, not to add locks.
3. **Audio callbacks.** Even at about 1 us, the audio callback should emit
   aggregated metrics, not per-block events (ADR 0005: "audio metrics are
   aggregated"). This design does not change that.
4. **Dropped is not the same as delivered.** A datagram accepted by the kernel
   can still be lost if `logd` is slow. `logd` must report its own receive
   gaps by checking `seq` against `boot_id`.

## Not decided here

- Whether the controller runs the drain thread or hands events to a separate
  process. Decide after the Pi benchmark.
- The `logd` wire format beyond "one JSON line per datagram".
- Where the `--protect-sd` on-disk critical-events file is written. That
  belongs with `logd`.
