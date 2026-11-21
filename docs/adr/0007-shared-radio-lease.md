# ADR 0007: Sharing a radio between the repeater core and a packet modem (a lease), designed, not built

Proposed (issue #240, with ADR 0006 and the modular framework #221). **Design only. Nothing in this ADR is built, and nothing
about the core, its audio, or its PTT changes until the preconditions below are met and the maintainer approves the build.**
ADR 0006 decided that the first Winlink slice uses a dedicated radio. This records what a shared radio would take, so the
decision to build it (or not) can be made with the real costs in view.

## Why it is not just "give the modem the sound card"

Three facts make a shared radio harder than it sounds:

1. **The core is the only owner of radio, audio and PTT.** That is what keeps one safe place that unkeys a stuck transmitter
   (the transmit cap, the watchdog, release on shutdown). A second process that can key the same radio breaks that.
2. **A repeater's radio is tuned to the repeater.** Winlink packet usually needs its own channel, so "the same radio" really means
   retuning it, or using the core's *link radio*, which is already a second radio the core owns and whose channel the owner sets.
   The link radio is the realistic candidate. The repeater's own radio is not (it would leave the repeater deaf and off frequency).
3. **The core's audio path processes audio for voice.** CTCSS decode and encode, de-emphasis, limiting, courtesy tones, and ID all
   change or interrupt a data signal. A packet modem needs a clean, unprocessed path of its own, with its own timing (TXDELAY).

## Options

- **A. Dedicated radio per module (ADR 0006, shipping).** No core change. Costs a second radio and sound card.
- **B. Core-mediated lease on the link radio (this design).** The core grants the modem exclusive use of the link radio's audio and
  PTT for a bounded time, and takes it back on any problem. One owner of PTT stays the core.
- **C. Hand the device to the modem container outright (a switch).** Rejected: no arbitration, the core cannot unkey it, and it
  gives up the safety properties in fact 1.

## Design for B

**Scope.** Only the **link radio** can be leased, never the repeater radio. Only when the station service is `amateur`; refused for
GMRS (same guard as ADR 0006, and the lease request carries the service class so the refusal is the core's, not the modem's).
Off by default. Configuration lives in the core's own config, so turning it on is a visible, logged setting (`config.changed`).

**The modem never opens a device or a PTT.** It is an audio client of the core over the private services network. The exact
transport is a spike (Dire Wolf reads audio from the sound card, stdin or UDP; whether it can *write* transmit audio to something other than a sound device,
or needs a virtual device, has to be checked on the pinned 1.8.1 build before anything is promised). If Dire Wolf cannot be fed
that way, the modem is not Dire Wolf for this mode, or the audio crosses a loopback device owned by the core's container, which is
a core change of its own.

**Lease states** (the core is the only place that moves them):

```
IDLE  ->  LEASED  ->  IDLE
          |  ^
          v  |
        REVOKING   (unkey first, then tell the modem)
```

- `lease.request(service, max_seconds)`: granted only when the link radio is idle (not receiving a remote, not transmitting for the
  repeater) and the link radio is not mid-ID. Otherwise `lease.denied(reason, retry_after_s)`. Never queues behind voice.
- `lease.granted(token, expires_in_s)`: exclusive. The core keeps the link radio's voice processing off for the duration.
- **Heartbeat.** The modem must send `lease.heartbeat(token)` at least every 1 s. Two missed (about 2 s) revokes. This is the "modem
  crashed while keyed" case, and the core, not the modem, is the one that notices and unkeys.
- **Keying.** The modem asks `ptt.request(token, on)`. The core keys the link radio and every existing limit applies unchanged: the
  per-transmission cap (`max_transmit_seconds`), `ptt_allowed`, the watchdog. A packet transmission is just another source for the
  same cap.
- `lease.release(token)`: modem done. The core unkeys, restores voice processing, and returns to IDLE.

**Voice has priority, always.** Revocation happens (unkey, then notify) when any of these occurs, in this order of safety:

1. link radio hears a carrier or the repeater needs the link (a local user is talking and the link must carry it),
2. an ID is owed and due (47 CFR 97.119: the repeater's ID wins over data),
3. heartbeat lost, lease expired (`max_seconds`, default 60 s, hard ceiling 300 s), or the token is stale,
4. core stopping, restarting, or any PTT write failure (the PTT failure paths already unkey; the lease just ends with them),
5. the maintainer turned the setting off.

The core never waits for the modem to agree before unkeying. The modem finding out is a courtesy.

**Failure behavior is fail-closed.** After a core restart no lease exists; the modem must request again. After any revoke, the
modem gets a cool-down (`retry_after_s`, grows on repeated revokes) so a flapping modem cannot hold the radio or hammer the core.
A lease never survives a PTT hardware error.

**Observability.** Named events, no payloads: `lease.granted`, `lease.denied` (reason), `lease.revoked` (reason, held_s),
`lease.released`, plus `ptt.commanded`/`ptt.applied` with a source of `lease` so a support bundle shows who keyed. Counters on the
status page: lease count, seconds held, revokes by reason, denials by reason.

**Contracts.** New commands and events go through `src/contracts/` with schemas generated and checked in CI, like every other
core interface. The core socket stays private and unauthenticated-by-network-isolation, as now; the lease token is not a
security boundary, only a stale-handle guard.

## Preconditions (all must hold before any build)

1. The current PTT problem on the maintainer's AIOC (USB drops when it keys) is root-caused and fixed, and PTT is verified
   reliable on air. A new transmit source should not be added to a path that is not trusted yet.
2. The Dire Wolf (or other modem) audio-in and audio-out spike answers the transport question above on the pinned build, on the Pi.
3. The maintainer approves the core change in writing (#240 stays `ready-for-human` until then).

## Test plan (when built)

- **Fakes first:** a fake link radio, fake PTT, fake clock, fake modem client. Every revoke reason above has a test that proves PTT is
  unkeyed *before* the modem is told, and that a modem that never answers cannot keep the radio keyed.
- **Fault injection:** modem killed while keyed; heartbeat stops; PTT write fails mid-lease; core restart mid-lease; COS during a
  packet transmission; ID due during a lease; flapping modem (cool-down grows); two modems asking at once (second is denied).
- **Mutation check** on the arbiter, same as the transmit-cap tests: removing any one of the unkey-first, heartbeat, expiry or
  priority rules must fail a test.
- **Bench:** the link radio into a dummy load, scope or a second receiver watching the carrier, with the modem killed on purpose at
  random points. Verified numbers recorded before anything on the air.
- **On air** (authorized station only): short transmissions only, after the bench passes. Not claimed from simulation.

## What it does not do

No lease on the repeater radio. No GMRS. No VARA (it runs under Wine and has its own PTT and licensing, to be checked separately).
No change to how the link radio works for linking when no lease is active.

## Consequences

Costs: a core change in the transmit path, a new contract surface, and a spike. Benefit: a Winlink or packet gateway on the link radio
you already own, with one place that can always unkey. If the maintainer decides that a second radio is acceptable, A is simpler and
safer and this ADR can be closed as "not needed".
