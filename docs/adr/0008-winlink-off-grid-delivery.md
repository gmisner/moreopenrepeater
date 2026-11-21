# ADR 0008: Off-grid Winlink delivery, no RMS Relay, and an optional LinBPQ mail mode that is designed, not built

Proposed (issue #345, with ADR 0006). **Design only. Nothing here is built, and the gateway keeps working as it does today:
an internet-connected RMS that relays a radio session to Winlink and stores nothing.**
When the internet is down, the gateway delivers nothing. Winlink's answer to that is RMS Relay. This records what it is, why
it is not an option for this project, what could be built instead, and what has to be decided first.

## What RMS Relay is

- Optional sysop software that works with Winlink's RMS Packet or RMS Trimode. It keeps a local message database and routes
  locally when the connection to Winlink's message servers (the CMS) is lost. It can also hold messages until the internet
  returns and then send them on.
- Winlink's own FAQ (March 2023) says it needs Windows Vista or later and .NET 3.5 or later, works alongside RMS Packet 2.0.0.0 or
  later, and is free. It mentions no Linux or arm64 build.
- **Its license and redistribution terms are unverified.** Winlink's site refuses scripted downloads, so the license text was not
  read. Until someone reads it, assume it may not be redistributed.

## Decisions

- **No RMS Relay support, and no work toward it.** It is a Windows program tied to another Windows program. Running it here would
  mean Wine and x86 emulation on a Pi, with no vendor support and an unclear license. It would break the rules this project
  keeps: one container per service, nothing from the host, nothing shipped without clear terms.
- **If off-grid delivery is built, LinBPQ does it.** LinBPQ's own documentation says its store and forward system "can provide
  similar facilities to RMS Relay", and the Telnet server page has a section for it (`RELAYAPPL=BBS`, `FALLBACKTORELAY=1`,
  `CMS=1`, a BBS record with the `RMS` connect script, and the user's "Poll RMS" setting). No second program and no second
  container would be needed: the mode would be a setting of the gateway we already have. Key names must be checked against the
  pinned build before anything is written.
- **Not built now.** The reasons are below. Building waits for the operator (the maintainer) to answer them.
- **Off by default, always.** The gateway stays memory-only (a tmpfs) with nothing to back up. Turning this on is a deliberate
  setting, and turning it off returns to today's behavior.

## What a mail store changes

Today the gateway is a conduit. It never holds a message, so it holds no one's text. A store changes that, and the decisions
below must be made and written down before a build:

- **Where.** A named volume (or a folder the owner picks) in the Winlink container only. It is the first state in that module that
  must survive a restart, so it is the first thing that writes to the SD card on purpose. The core, the dashboard and the support
  bundle never see it.
- **How big.** A hard cap on the volume and on one message (LinBPQ has per-message size limits), with a refusal and a plain
  event when it is full.
- **How long.** A message lifetime and a "delete after delivery" rule. LinBPQ's housekeeping can leave deleted messages on disk
  for a while, so the docs must say that.
- **The off switch.** One setting, off by default. Off also means the volume is not mounted.
- **Message text is never logged.** Not by MOR, not by `logd`, not in a support bundle, and not on the dashboard beyond counts
  (held, delivered, waiting). The same redaction rule as the rest of the project.
- **Who is responsible in between.** LinBPQ holds the message once it accepts it. The senders cannot tell it is held and not
  delivered, which Winlink's own FAQ names as a risk for emergency traffic. The dashboard and the docs must not describe this as
  guaranteed or timely delivery.
- **LinBPQ's license.** The same open question as #344 applies, since a mail mode makes LinBPQ more central.

## Amateur rules (for the operator to check, not a compliance claim)

These are the sections of 47 CFR Part 97 that bear on holding third party messages in a station that runs unattended. The docs
point at them and must not claim compliance. Read the current text at ecfr.gov.

- Section 97.109(d): an automatically controlled station's control operator need not be at the control point, and only stations
  designated elsewhere in Part 97 may be automatically controlled.
- Section 97.221(b): automatic control of a station sending data on 6 m and shorter wavelengths (which covers 2 m and 70 cm).
- Section 97.115(c): a station under automatic control may send third party communications only if it is sending RTTY or data.
- Section 97.219: the originating control operator is primarily accountable for a message, the first forwarding station's
  control operator must authenticate the sender or accept accountability, and forwarders must stop forwarding once they know a
  message breaks the rules. LinBPQ has a "hold messages for sysop review" setting, which some licensing authorities expect.
- Section 97.113 applies to what a message says.

This is a United States summary. An operator elsewhere follows their own rules. **Amateur only**: no GMRS.

## Not decided

- **Whether to build it at all.** The honest alternative is to leave the gateway as a conduit. It is simpler, holds no text, and
  has no store to run out of room or to explain to a licensing authority. The cost is that an internet outage means no Winlink
  delivery through this station.
- **Hardware and on-air behavior** are unverified. No store, forwarding to the CMS, or recovery after the internet returns has
  been tried on a radio or against Winlink.
- **The research notes' gaps.** The RMS Relay license, and whether LinBPQ's `linmail.cfg` key names match the ones in its
  Windows documentation, were not verified.

## Considered options

- **RMS Relay in a container with Wine**: rejected, see above.
- **A second program for mail (Pat, or a BBS on its own)**: rejected for now. Pat is a client, and a second service adds a
  container and a second store for no gain over LinBPQ's own.
- **Do not build**: the live alternative. Recommended until the operator wants the cost above.
- **LinBPQ mail mode, off by default**: the proposed design if the answer is yes. First step would be a small PR for a store
  that exists only when the setting is on, with the limits above and counts-only status.
