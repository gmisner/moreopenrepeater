# Wiring a bare CM108 USB dongle

A plain USB sound dongle built on a C-Media CM108-family chip (CM108,
CM108AH, CM108B, CM119; the Acxico LY1623 and many other cheap ones) can
become a complete radio interface: audio both ways, PTT and COS, all over
one USB cable. It needs a transistor, two resistors and some careful
soldering. Ready-made interfaces (DMK URI, RIM-Lite, AIOC and similar) do
the same job without the soldering.

The chip does this through:

| Chip pin | Function | Used for |
| --- | --- | --- |
| 13 (GPIO3) | Output, high while transmitting | PTT, through a transistor |
| 48 (VOL_DN) | Input with a pull-up; "pressed" when pulled low | COS |

These pin numbers are the same on the CM108 variants and match
AllStarLink's SimpleUSB driver, Dire Wolf and every commercial interface
based on the chip, so the finished dongle also works with those.

Sources: [Dire Wolf Radio Interface Guide](https://raw.githubusercontent.com/wb2osz/direwolf-doc/main/Radio-Interface-Guide.pdf),
[Repeater-Builder's StarTech fob mod](https://www.repeater-builder.com/projects/fob/startech-fob.html),
[NEDNet's CM108 fob mod](https://nednet.org.uk/how_to/CM108_mod),
[AllStarLink simpleusb.conf](https://allstarlink.github.io/config/simpleusb_conf/).

## Parts

- 2N3904 (or 2N2222, 2N4401) NPN transistor, one for PTT and one more if
  the radio's COS output isn't already an open collector pulled low on
  carrier (see below)
- 4.7 kΩ to 10 kΩ resistor for the PTT transistor's base, and 10 kΩ for a
  COS transistor
- 30 AWG wire-wrap (Kynar) wire for the chip pins
- A cable and connector for the radio, and optionally ferrite beads and
  100 pF to 1 nF capacitors on the PTT, COS and audio lines against RF

## Find the pins

Open the case and read the chip's marking to confirm it's a CM108-family
part. It's a 48-pin square package. Pin 1 is next to the dot in one
corner, and the numbers run counterclockwise with the chip's writing the
right way up, 12 to a side. Pins 13 and 48 are both corner pins: 13 starts
the second side, and 48 is the last pin, next to pin 1.

Corner pins are the easy ones to tack a wire onto. Slide the stripped end
of the Kynar wire under the row of pins so it lies alongside the one you
want, then solder quickly with a fine tip. Check with a meter afterwards
that it isn't bridged to the pin next to it.

On many dongles pin 48 already runs to a pad or a volume button through a
trace, which is easier to solder to than the pin itself. Confirm with the
meter's continuity setting that it goes to pin 48.

## PTT

```
chip pin 13 ──[ 4.7k-10k ]── base
                               2N3904   collector ── radio PTT
                                        emitter ──── ground
```

The transistor pulls the radio's PTT line to ground while transmitting,
which is what nearly every radio's mic or accessory PTT input wants.

On the dashboard, set **PTT** to "CM108 interface".

## COS

COS (carrier-operated squelch, or COR) tells the repeater that the receiver
hears a signal. Look up what your receiver's COS output does:

- **It pulls to ground on carrier** (an open-collector or open-drain
  output, the most common kind): connect it straight to pin 48. The chip's
  own pull-up holds the pin high the rest of the time. Set **COS polarity**
  to "Active low".
- **It goes to a positive voltage on carrier** (5 V, 8 V, 12 V): don't
  connect that to the chip. Put a second transistor in between:

  ```
  radio COS ──[ 10k ]── base
                          2N3904   collector ── chip pin 48
                                   emitter ──── ground
  ```

  The transistor pulls pin 48 low on carrier, so COS polarity is still
  "Active low".

Use "Active high" only if the line into pin 48 is pulled low while the
receiver is *idle* and released on carrier.

On the dashboard, set **Carrier detect** to "CM108 COS input". If you
can't wire COS at all, "Audio level (VOX)" or "CTCSS tone present" work
from the receive audio instead.

## Audio

**Remove the microphone bias resistor.** The dongle feeds a few volts to its
mic jack through a resistor (often around 1 to 2 kΩ, labelled R3, R6 or R10
depending on the board) to power an electret microphone. A radio's audio
output doesn't want that DC, and removing the resistor raises the input
impedance too. To find it, plug the dongle in and measure DC on the mic
jack's tip. If there's voltage, follow the trace from the jack to a nearby
resistor that has the same voltage on one side and a higher one on the
other, and lift or remove it. Measure again: the jack should read near 0 V.

- **Receive audio:** radio audio out to the dongle's mic input. For CTCSS
  decoding, use discriminator or "flat" data audio, since a speaker output
  usually filters the tone out. A speaker output is much louder than a mic
  input expects: pad it down (for example 10 kΩ in series, then 1 kΩ to
  ground) and set the level with the meter on the Radio interface card.
- **Transmit audio:** the dongle's headphone output to the radio's mic or
  data input, through a similar pad and a 1 µF capacitor if the radio's
  mic input carries DC. Set the level with **Transmit gain**.

Connect the dongle's ground to the radio's ground.

## Test before going on the air

With a dummy load on the transmitter and the repeater stopped:

```
sudo systemctl stop moreopenrepeater
sudo -u moreopenrepeater /opt/moreopenrepeater/.venv/bin/python /opt/moreopenrepeater/scripts/cm108_check.py
```

It keys PTT for a second, then shows COS live while you key a radio on the
receive frequency. If COS shows "OPEN" when nothing is transmitting and
"closed" while you transmit, flip **COS polarity** on the dashboard. The
raw byte it prints has bit `0x02` set while pin 48 is pulled low.

Then start the service again (`sudo systemctl start moreopenrepeater`) and
point it at the dongle as described in
[raspberry-pi.md](raspberry-pi.md#3-cm108-usb-sound-card-interface-permissions).
