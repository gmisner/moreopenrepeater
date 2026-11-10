# ADR 0004: DMR via DVSwitch USRP bridge

Accepted. Cross-mode DMR (open/amateur networks first) is a DVSwitch sidecar
(`Analog_Bridge` + `MMDVM_Bridge`) on a dedicated USRP port pair into the
controller, not a native AMBE/DMR stack and not piggybacked on the AllStar
USRP peer. Audio mixes through a third `LinkAudio` slot beside AllStar
(`set_link`) and Linked Radio (`set_port`); keying uses `RemoteKeyed` with
`node_id="dmr"`. Sidecar ini files live under `/etc/moreopenrepeater/dmr/`
(owned by this project); systemd drop-ins point the DVSwitch units at them.
Commercial/public-safety DMR and local MMDVM RF are out of scope until a
later decision.

## Considered options

- **Through ASL**: rejected — ASL USRP is already the controller's radio channel.
- **Native Python DMR**: rejected — AMBE + HomeBrew duplicate DVSwitch.
- **SvxLink sidecar**: rejected — second repeater controller.
- **Edit `/opt/*/…ini` in place**: rejected — package upgrades overwrite; we own
  `/etc/moreopenrepeater/dmr/` instead.
