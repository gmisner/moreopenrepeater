# Running a GMRS repeater

moreopenrepeater can run a GMRS repeater as well as an amateur one. GMRS
(General Mobile Radio Service) is licensed under FCC Part 95, Subpart E, and
its rules differ from the amateur ones in Part 97 in ways the controller has
to follow. Turn on **GMRS repeater** on the dashboard's **Identification**
page and the controller applies them.

This page is a summary, not legal advice. The rules are at
[47 CFR Part 95 Subpart E](https://www.law.cornell.edu/cfr/text/47/part-95/subpart-E).

## What GMRS mode changes

| Rule | What the controller does |
| --- | --- |
| Identify at the end of a transmission or series of transmissions, and at least every 15 minutes during one, by voice or Morse (§95.1751) | Caps the ID interval at 15 minutes (900 s). A shorter interval is kept. Put the GMRS call sign (such as `WRXX123`) in the **Callsign** field. |
| No connection to the telephone network (§95.1749) | Turns off the autopatch, including incoming calls. |
| No transmitting messages that arrive over a wireline control link (§95.1733(a)(8)) | Turns off AllStarLink and EchoLink linking. Existing links are dropped when GMRS mode is turned on, nodes that link in from outside are disconnected, and DTMF link commands are ignored. |
| APRS is an amateur service | Turns off APRS beaconing and the APRS map. |

The autopatch, linking and APRS settings are kept, so turning GMRS mode off
puts them back as they were. The pages for those features say they're off
while GMRS mode is on.

Some GMRS repeaters are linked over the internet, and people disagree about
whether §95.1733(a)(8) allows it. moreopenrepeater takes the cautious
reading and doesn't link in GMRS mode.

## What's up to you

The controller can't check the radio side:

- **License.** Each GMRS operator needs an FCC GMRS license. There's no
  exam, and one license covers the licensee's immediate family. The
  repeater's users need their own licenses too.
- **Channels (§95.1763).** Repeaters transmit on the eight 462 MHz main
  channels and listen on the matching 467 MHz channel, 5 MHz higher:

  | Repeater output (MHz) | Repeater input (MHz) |
  | --- | --- |
  | 462.5500 | 467.5500 |
  | 462.5750 | 467.5750 |
  | 462.6000 | 467.6000 |
  | 462.6250 | 467.6250 |
  | 462.6500 | 467.6500 |
  | 462.6750 | 467.6750 |
  | 462.7000 | 467.7000 |
  | 462.7250 | 467.7250 |

  Mobile and handheld radios may only transmit on the 467 MHz channels to
  reach a repeater.
- **Power (§95.1767).** Up to 50 W transmitter output on the main channels
  for repeater, base and mobile stations. Fixed stations are limited to 15 W.
- **Certified radios (§95.1761).** Every GMRS transmitter must be certified
  for GMRS. A radio that can also transmit on amateur frequencies can't be
  certified for GMRS, so a ham radio, or a ham repeater, can't be used on
  GMRS even if it tunes there.
- **Sharing.** GMRS channels are shared. Give the repeater a CTCSS or DCS
  input tone (the **Required CTCSS tone** on the **Timing** page) so it
  doesn't come up on other users' traffic, and listen before choosing an
  output channel.

## Features that still work

Everything else works as on an amateur repeater: courtesy tones, the timeout
timer and stuck-carrier lockout, announcements, weather alerts, DTMF macros
that don't link or dial, recordings, the parrot test, net mode, alerts, and
updates.
