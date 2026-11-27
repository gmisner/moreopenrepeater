# ADR 0009: Phase out the native install and move everyone to the container install

Status: proposed. The direction is the maintainer's (2026-11-26): the native install goes away eventually and the modular
container architecture is the one runtime. The gates, the order and the guardrails below are proposals for the maintainer to
confirm. There is no removal date, and none should be set until the gates are met.

## Context

Two installs exist today.

- **Native:** `scripts/install-pi.sh`, a systemd service, code in `/opt/moreopenrepeater`. It follows the `beta` and `stable`
  channels, and `stable` is the default for any repeater on the air.
- **Container:** `docker/`, Compose, with `core`, `api`, `logd` and `piper` plus optional sidecars. Prebuilt images come from GHCR.
  For now it follows the `docker` channel (ADR 0005 and issue #221 set the one-runtime direction; #367 is the registry and
  channel plan).

Supporting both doubles the test and support matrix, and it blocks modules that need their own container (AllStar, DMR, Winlink,
Dire Wolf). ASL3 and DVSwitch only ship Debian 12 and 13 packages, which is the same pressure from the other side. A repeater
is also not a web app: a bad update can leave a transmitter on the air or a site dark, so the switch has to be something an
owner chooses, not something an update does to them.

## Decision

1. **The container install is the one runtime going forward.** The native install is deprecated now and removed later, when
   the gates below are met. Until then it is maintained, not extended.
2. **Nothing converts a native install on its own.** Moving is a deliberate act (`docker/migrate-from-native.sh`, which only
   reads the native data and can be undone by restarting the native service). `update.sh` never changes the install type, and the
   native updater cannot be asked for the `docker` channel (a test pins this).
3. **`beta` and `stable` stay safe for native installs until the gates are met.** The `docker` channel is a pointer that can run
   ahead of them, so the container install can move quickly without anyone's native repeater following it.
4. **Before beta or stable become container-first, these gates are met** (proposed):
   - The engine safety changes made since `beta` (the transmit cap, the keyed-failure limit, input silence detection) have had a
     bench test on a real radio on a native install. The simulation tests do not count as hardware verification.
   - A container install has soaked on the `docker` channel for days on real hardware without unexplained restarts, and
     `update.sh --rollback` has been tried there (it was, 2026-11-25).
   - The known hardware costs of the container install are written down and handled where the installer can handle them: the
     Pi 3B+ USB audio glitches (see "Measured on a Pi" in `docs/docker.md`) and the AIOC USB drop at key-up, whose hardware
     cause is still open. The core already restarts itself on a stall.
   - Anything the native install does that the container install does not is on a list, and each item is either built or dropped
     on purpose. Services with unpublished images (Winlink, DMR, AllStar, Dire Wolf, pending license review, #344) are
     part of that list.
5. **Native owners get notice and a last native release.** Before `stable` carries container-only code, a final native-compatible
   release ships that tells the owner in the dashboard what is coming and how to move. After it, the native updater stops
   there with a plain message and does not pull code it cannot run. This is not built yet.
6. **At the switch, `docker` folds into `beta` and `stable`** and the extra channel is retired. The native-only paths
   (`install-pi.sh`, the native updater and its units) are deleted then, so new work should not grow them.

## Considered options

- **Keep both installs for good.** Rejected: it keeps the doubled matrix and keeps module-per-container work second class.
- **Convert native installs when they update.** Rejected: the repeater goes off the air during the move, device passthrough
  differs from the native service, and there is no unattended way back if it goes wrong.
- **Point `stable` at the container code now.** Rejected: `stable` is what repeaters on the air follow, and the evidence so far is
  one bench Pi. Native `stable` has not had the engine safety changes on a radio yet.

## Consequences

- Until the gates are met the project carries two installs. Wording that is only true for one of them has to be gated on the
  install type (for example the time container card, #380). Winlink is already container-only.
- New features may be container-only from now on. A native owner who wants them has to move.
- The Updates page and docs need to say which channel and install an owner is on. A native warning page is part of decision 5.
- If a gate fails (for example the bench test finds a problem), the switch waits. Nothing here is a commitment to a date.
