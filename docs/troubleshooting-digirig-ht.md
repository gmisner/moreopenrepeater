# Digirig Lite + HT bring-up (field notes)

Field notes from bringing up **two Digirig Lite + Baofeng-class HTs** on a
Raspberry Pi 4 with More Open Repeater: one as the local radio (147.12) and
one as a UHF link (444.9). Related short notes already live under
[Digirig Lite + HT: USB drops on TX](raspberry-pi.md#digirig-lite--ht-usb-drops-on-tx)
and GitHub [#119](https://github.com/gmisner/moreopenrepeater/issues/119).
This page is the longer walkthrough: symptoms, what was *not* broken, and
what actually fixed each stage.

## Setup under test

| Role | Radio | Interface | ALSA (typical) |
|------|--------|-----------|----------------|
| Local / “repeater” radio | Baofeng AR-5RM on 147.12 | Digirig Lite | `hw:3,0` |
| Link radio | Baofeng UV-5RM on 444.9 | Digirig Lite | `hw:4,0` |
| COS / PTT | VOX + CM108 GPIO3 on each Digirig | | |
| Host | Raspberry Pi 4, `moreopenrepeater` systemd service | | |

`hw:N` numbers move when USB re-enumerates. Prefer PortAudio names that
include `(hw:N,0)`, and re-pick devices in the dashboard after a disconnect.

## Symptom timeline (what we saw)

1. **Quiet / no audio on key-up** while COS/PTT blinked — Mic capture was
   only ~34% (ALSA step 12 of 35). Idle `rx_db` ~−89; keyed ~−84.
2. **After raising Mic** — brief `rx_db` spikes (e.g. −32, −21, even −1)
   then collapse to ~−60 while still “receiving”.
3. **`arecord` on `hw:3` (service stopped)** — VU jumped hard while talking.
   So Digirig → Pi audio was fine when the HT stayed in RX.
4. **Root cause for (2)** — one Digirig + one HT is **half-duplex**. In
   `node_mode: repeater`, MOR keys PTT on that same HT as soon as COS opens;
   the HT stops receiving and `rx_db` falls off a cliff.
5. **Fix for live local use without a second TX radio** —
   `node_mode: simplex`. Local users are heard and sent to the link / network
   **without** keying the local HT (`ptt False` while `receiving`).
6. **Choppy audio on the link** with simplex working — `rx_db` swung
   −3 ↔ −80 every half-second while `link tx True`. `dmesg` showed the
   classic Digirig RFI pattern (`error -32`, `error -71`, C-Media disconnect).
   That is RF into USB when an HT transmits near the Pi, not a gain bug.

## What was not broken

| Hypothesis | Result |
|---|---|
| Wrong PortAudio device / engine not running | Devices present; engine `running: true` after service restart |
| Need serial RTS/DTR PTT on Digirig Lite | Lite has no USB-serial; use CM108 PTT |
| Software Mic gain alone can fix half-duplex | Mic max (35 / +23 dB) still collapses when the HT goes to TX |
| `tx_delay_ms: 3000` keeps PTT off for 3 s | API caps at **500 ms**, and it delays **audio after PTT**, not PTT itself |
| Choppy link audio is VOX alone | Tight VOX made mid-sentence courtesy tones worse; with link off, levels still swung — `dmesg` USB errors are the smoking gun |

## Working configuration (simplex node + link)

Useful settings after this bring-up (adjust VOX to your idle floor):

```json
{
  "node_mode": "simplex",
  "tx_delay_ms": 0,
  "vox_threshold_db": -48,
  "vox_hold": 1.0,
  "audio_input_levels": { "Mic": 35 },
  "link_radio_enabled": true,
  "link_radio_ptt": "cm108",
  "link_radio_cos": "vox"
}
```

Also turn CM108 Auto Gain Control **off** on the local card (not currently a
dashboard control):

```bash
amixer -c 3 sset 'Auto Gain Control' off
```

Save Mic via MOR so it survives restarts:

```bash
curl -sS -b "$COOKIE" -X PUT http://127.0.0.1:8000/api/audio/levels \
  -H 'Content-Type: application/json' \
  -d '{"side":"input","control":"Mic","value":35}'
```

Raise **link** Mic the same way (`side: link_input`) when tuning inbound
link audio (it was left at 12 / 0 dB in this bring-up).

### Status poll while testing

```bash
read -s -p "MOR admin password: " MOR_PASS; echo
COOKIE=$(mktemp)
curl -sS -c "$COOKIE" -X POST http://127.0.0.1:8000/api/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$MOR_PASS\"}" >/dev/null

while true; do
  date '+%H:%M:%S'
  curl -sS -b "$COOKIE" http://127.0.0.1:8000/api/status | python3 -c \
    'import json,sys; s=json.load(sys.stdin); print("rep", s.get("state"), "cos", s.get("cos_active"), "ptt", s.get("ptt_active"))'
  curl -sS -b "$COOKIE" http://127.0.0.1:8000/api/audio/engine | python3 -c \
    'import json,sys; e=json.load(sys.stdin); print("rx_db", round(e.get("rx_level_db") or -999,1))'
  curl -sS -b "$COOKIE" http://127.0.0.1:8000/api/link-radio | python3 -c \
    'import json,sys; l=json.load(sys.stdin); print("link tx", l.get("transmitting"), "rx", l.get("receiving"))'
  sleep 0.5
done
```

**Healthy simplex key-up:** several seconds of
`receiving` / `cos True` / `ptt False`, `rx_db` roughly **−25…−8**,
`link tx True`, then idle when you unkey — no courtesy tone in the middle
of a sentence.

**Half-duplex trap (repeater mode, one HT):** first sample may show hot
`rx_db` with `ptt True`, then immediate collapse to ~−60.

## USB RFI checklist (choppy audio / disconnects)

When `dmesg` shows lines like:

```text
device descriptor read/64, error -32
device not accepting address … error -71
USB disconnect … C-Media Electronics Inc. USB Audio Device
```

treat it as layout, not mixer math:

1. Both HTs on **low power** for bring-up
2. Antennas **away** from the Pi and USB run (dummy load on the link HT is a
   good bench test)
3. Snap-on **ferrites** on both Digirig USB cables, at the dongle end
4. Digirigs on a **powered USB hub** when possible
5. Unplug other flaky full-speed gadgets (wireless KB/mouse dongles) from the
   same hub while testing — they often reset with the same `-32` errors
6. After a disconnect storm: unplug/replug Digirigs if needed, then
   `sudo systemctl restart moreopenrepeater` so PortAudio reopens the cards

Watch live:

```bash
sudo dmesg -w
```

You want **no new** `-32` / `-71` / C-Media disconnect lines while someone
talks and the link keys.

Software already softens some failures (stream restart after USB audio drop;
CM108 hidraw recovery — see [#96](https://github.com/gmisner/moreopenrepeater/issues/96),
[#111](https://github.com/gmisner/moreopenrepeater/issues/111)). The bus still
has to stay attached.

## Paths if you need a true local repeater

One Digirig + one HT **cannot** live-repeat (RX and TX at once). Options:

| Goal | Approach |
|------|----------|
| Local users → link / AllStar / EchoLink only | **`node_mode: simplex`** (what worked here) |
| Live local retransmit | Separate **RX** radio into Digirig capture; separate **TX** radio from Digirig playback + PTT — or a duplex-capable radio |
| Echo test only | Parrot macro (store-and-forward; not a repeater) |

With two Digirigs already on hand, you can temporarily dedicate one HT to
RX-only and one to TX-only and give up the RF link until you add another
radio.

## Access gotcha (seen earlier in the same bring-up)

`require_ctcss_hz` still gates **local** access even when `access_mode` is
`carrier`. Clearing TX CTCSS is not enough; clear the required access tone
(dashboard, or `clear_require_ctcss_hz` / set `require_ctcss_hz` null via
config). Remote/link key-ups bypass that check.

## Code / product follow-ups

None of the failures above required a controller bugfix to get audio
moving. Shipped afterward:

1. Dashboard / Timing / Audio warnings for Digirig-style half-duplex + repeater mode
2. Auto Gain Control as a saved receive-side switch on Sound card levels
3. Digirig Lite board preset: Speaker 75%, Mic 18 dB, Auto Gain off
4. Link radio status on the main Dashboard
5. Grouped sound-card level sliders (receive / transmit / link)

## See also

- [Sound card levels](raspberry-pi.md#sound-card-levels)
- [Audio devices / first key-up](raspberry-pi.md#audio-devices)
- [Link radio](raspberry-pi.md#link-radio)
- [Digirig Lite + HT: USB drops on TX](raspberry-pi.md#digirig-lite--ht-usb-drops-on-tx)
- README: **Simplex node mode**
- Issues [#96](https://github.com/gmisner/moreopenrepeater/issues/96),
  [#111](https://github.com/gmisner/moreopenrepeater/issues/111),
  [#119](https://github.com/gmisner/moreopenrepeater/issues/119)
