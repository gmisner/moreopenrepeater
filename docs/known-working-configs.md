# Known-working configurations

Hardware setups tested with moreopenrepeater. Use these as a starting point,
then trim levels on **Audio & tones**. If yours works and isn’t listed, open a
PR that adds a section like the ones below.

Related wiring notes: [Raspberry Pi](raspberry-pi.md), [CM108](cm108-wiring.md),
[Digirig Lite + HT](troubleshooting-digirig-ht.md).

## Digirig Lite + Baofeng HT + Raspberry Pi 4

| | |
|---|---|
| **Role** | Local repeater radio (or simplex node) |
| **Host** | Raspberry Pi 4, Raspberry Pi OS Lite 64-bit |
| **Radio** | Baofeng / AR-5RM class HT |
| **Interface** | Digirig Lite (CM108 audio + GPIO3 PTT) |
| **Board preset** | Digirig Lite |
| **PTT** | CM108 GPIO3 |
| **COS** | Audio level (VOX); wire the COS pad for CM108 COS if preferred |
| **Levels** | Speaker ~75%, Mic ~18 dB, Auto Gain off |
| **Sample rate** | Device native (dashboard shows the PortAudio name with `hw:N,0`) |

### Notes

- Digirig Lite has no USB-serial port — do not look for RTS/DTR PTT.
- One HT is half-duplex: for live local use without a separate TX radio, set
  **Timing → Simplex node** so COS does not key that same HT.
- Digirig documents unreliable Lite detection on Raspberry Pi (including
  after reboot). Prefer a **powered USB hub**, Digirig Mobile, or ask Digirig
  about next-revision Lite / the upcoming dual-radio HAT — see
  [troubleshooting Digirig + HT](troubleshooting-digirig-ht.md#digirigs-guidance-pi-usb-detection).
- If USB drops when the HT transmits near the Pi, that is usually RFI into
  USB — see [Digirig Lite + HT: USB drops on TX](raspberry-pi.md#digirig-lite--ht-usb-drops-on-tx).
- Longer field notes: [troubleshooting Digirig + HT](troubleshooting-digirig-ht.md).

### HT menu starting points

| Setting | Value | Why |
|---|---|---|
| Squelch | Low / open enough for VOX | VOX needs muted audio when idle |
| VOX (on the HT) | Off | The controller handles VOX; radio VOX fights PTT |
| Roger beep / STE | Off | Corrupts the end of transmissions |
| Dual watch / TDR | Off | Keeps the radio on one frequency |

## Digirig Lite local + Digirig Lite link (UHF)

| | |
|---|---|
| **Role** | Local VHF + UHF [Linked Radio](../GLOSSARY.md) |
| **Host** | Raspberry Pi 4 |
| **Local** | Digirig Lite + Baofeng on VHF |
| **Link** | Second Digirig Lite + Baofeng on UHF |
| **Local COS / PTT** | VOX + CM108 GPIO3 |
| **Link COS / PTT** | VOX + CM108 GPIO3 on the second dongle |

Pick each Digirig by PortAudio name (they re-enumerate after USB resets).
Use **Audio & tones → Linked Radio** for the second pair. Put both Digirigs
on a **powered USB hub** so they survive Pi reboot; keep HT antennas and
USB cables short and ferrite-clamped if TX near the Pi unlocks the Digirig.

## DMK URI / USB-RIM Lite + Raspberry Pi

| | |
|---|---|
| **Board preset** | DMK URI or USB-RIM Lite |
| **PTT** | CM108 GPIO3 |
| **COS** | CM108 VOL_DN, active low |
| **Levels** | Speaker 75%, Mic 18 dB |

Full-duplex capable when the radio is a proper repeater pair. Prefer hardware
COS over VOX when the interface wires VOL_DN.

## Generic CM108 / CM119 USB dongle (homebrew)

| | |
|---|---|
| **Board preset** | Generic CM108/CM119 |
| **PTT** | CM108 GPIO3 (chip pin 13 via transistor) |
| **COS** | CM108 VOL_DN (chip pin 48), active low |
| **Levels** | Set by ear; save from **Sound card levels** |

Wiring details: [cm108-wiring.md](cm108-wiring.md). Same pinout as AllStarLink
SimpleUSB and Dire Wolf.

## AIOC

| | |
|---|---|
| **Board preset** | AIOC |
| **PTT** | CM108 GPIO3 |
| **COS** | Virtual COS on VOL_DN |

Leave mixer levels alone unless the dashboard meter shows clipping or
near-silence.

## SVXLink Card (F5UII/F8ASB) on Pi GPIO

| | |
|---|---|
| **Board preset** | SVXLink Card |
| **PTT** | GPIO16 (port 2: GPIO17) |
| **COS** | GPIO19 active low (port 2: GPIO18) |
| **Levels** | Speaker 75%, Mic 69% |

Port 2 becomes the link radio when its sound devices are chosen. See the
board table in [raspberry-pi.md](raspberry-pi.md#setup-wizard-and-interface-boards).

## SVXLink Basic Board

| | |
|---|---|
| **Board preset** | SVXLink Basic Board |
| **PTT** | GPIO24 (port 2: GPIO18) |
| **COS** | GPIO23 active high (port 2: GPIO25) |
| **Levels** | Speaker 75%, Mic 69% |

## Submit yours

Add a section with:

1. Host OS and board
2. Radio and interface
3. Board preset, PTT, and COS choices
4. Mixer levels that worked
5. Any radio-menu gotchas or RFI notes

PR against `docs/known-working-configs.md`.
