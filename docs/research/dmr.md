# DMR cross-mode bridge: research

Research date: 2026-11-07 (updated 2026-11-09). Question: what would it take
for moreopenrepeater's analog `RepeaterController` to bridge audio and keying
to open DMR networks (BrandMeister / TGIF / DMR+)?

Product scope:

- **Shape**: cross-mode bridge (keep the analog controller).
- **Network**: open/amateur first. Commercial/public-safety DMR deferred.

## TL;DR recommendation

**Run a DVSwitch sidecar (`Analog_Bridge` + `MMDVM_Bridge` + `md380-emu`) and
speak USRP to `Analog_Bridge` on a dedicated port pair**, the same protocol
as AllStar (`src/link/usrp.py`). Own the ini files under
`/etc/moreopenrepeater/dmr/`; point systemd units at them with drop-ins.

Do **not** put DMR through the ASL USRP peer. Do **not** write a native AMBE
stack.

## Architecture

```
  RF users ── COS/PTT/audio ── RepeaterController ── USRP :34011/:34012
                                                      │
                                              Analog_Bridge
                                                      │ TLV / AMBE
                                              MMDVM_Bridge
                                                      │
                                         BrandMeister / TGIF / custom
```

Default USRP ports (avoid ASL's 34001/32001):

| Role | Port |
| --- | --- |
| Controller listens (Analog_Bridge `txPort`) | 34011 |
| Analog_Bridge listens (controller peer / `rxPort`) | 34012 |

TLV between Analog_Bridge and MMDVM_Bridge keeps DVSwitch stock ports
(31100/31103) via `DVSwitch.ini`.

## Operator install

`sudo bash scripts/install-pi.sh --dmr` (Debian bookworm/trixie). Then set
callsign, DMR Id, network password, and talkgroup on the dashboard **DMR**
page. Passwords are write-only (never returned on `GET /api/dmr`).

Last-heard: with `exportMetadata=true`, Analog_Bridge sends USRP TEXT/TLV
`SET_INFO` (pyUC layout) carrying radio Id / callsign / TG / slot. Parsed in
`link.usrp` and tracked by `DmrAudio` as `audio.last_heard` on `GET /api/dmr`.

## Out of scope

- Local DMR RF (MMDVM modem as the site radio)
- Commercial/public-safety trunking
- Native AMBE in this repo

## Sources

- https://github.com/DVSwitch/Analog_Bridge/blob/master/Analog_Bridge.ini
- https://github.com/DVSwitch/MMDVM_Bridge
- This repo: `docs/allstar.md`, `docs/research/echolink.md`, ADR 0004
