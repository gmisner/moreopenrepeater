# DMR (DVSwitch bridge)

The controller can bridge the analog repeater to open DMR networks
(BrandMeister, TGIF, or a custom HomeBrew master) through a local DVSwitch
sidecar. Local users hear the talkgroup; talkgroup traffic keys the
repeater. Courtesy tone, hang time, and TOT stay with the analog controller.

This is **not** a local DMR RF modem and not commercial/public-safety
trunking. See ADR 0004 and `docs/research/dmr.md`.

## How it works

1. `Analog_Bridge` speaks USRP (8 kHz PCM) to the controller on ports
   **34011** (to us) and **34012** (from us), separate from AllStar's USRP.
2. `MMDVM_Bridge` speaks DMR to the network; `md380-emu` supplies software
   AMBE when no USB AMBE stick is present.
3. The dashboard writes identity and network settings into
   `/etc/moreopenrepeater/dmr/*.ini` and asks systemd to restart the
   bridges (same request-file pattern as a service restart).

## Setup

On Debian 12/13 (Raspberry Pi OS Bookworm or newer), either:

1. **Dashboard** — open the **DMR** page as an admin and press **Install DMR**
   (same root helper pattern as Tailscale / Updates), or
2. **CLI** — `sudo bash /opt/moreopenrepeater/scripts/install-pi.sh --dmr`

Both install the DVSwitch apt packages, seed `/etc/moreopenrepeater/dmr/`,
add systemd drop-ins so the units load our ini files, and set:

```bash
MOREOPENREPEATER_DMR_DIR=/etc/moreopenrepeater/dmr
MOREOPENREPEATER_DMR_USRP_LISTEN=127.0.0.1:34011
MOREOPENREPEATER_DMR_USRP_PEER=127.0.0.1:34012
MOREOPENREPEATER_DMR_RESTART_REQUEST=/opt/moreopenrepeater/data/dmr-restart-request
```

The dashboard restarts once so it picks up those env vars. Then on the
**DMR** page: callsign, 7-digit DMR Id, network password, default talkgroup
and slot. Save enables and restarts the bridges. Turn off keeps the saved
details for next time.

### Talkgroup favorites

On the DMR page, **Talkgroup favorites** are DMR talkgroup numbers (with an
optional name and slot) — not AllStar node numbers. They live in
`RepeaterConfig.dmr_talkgroup_favorites`. Clicking a favorite posts
`/api/dmr/talkgroup`, which updates Analog_Bridge's static `txTg` / `txTs`
and restarts the bridges.

### Last heard

With `exportMetadata=true` in Analog_Bridge.ini (the default template),
network transmissions arrive with USRP TEXT/TLV `SET_INFO` packets that
carry radio Id, callsign, talkgroup and slot. The controller keeps the
latest station in `GET /api/dmr` → `audio.last_heard` and shows it on the
DMR page. Metadata alone does not key the repeater; voice packets still
do.

### BrandMeister masters

Pick a regional BrandMeister master host (for example `3102.repeater.net`)
in the Host field. Your hotspot password is the one from self-care, not
your login password.

### Firewall

Outbound UDP to the DMR master (often 62031) must be allowed. No inbound
ports are required for a hotspot-style bridge.

## Together with AllStar

AllStar keeps `MOREOPENREPEATER_USRP_*` (34001/32001). DMR uses 34011/34012.
Both can key the repeater at once; audio is mixed on the processor.
