# ADR 0006: Winlink RMS gateway as LinBPQ behind Dire Wolf, on a radio of its own

Accepted for the first slice (issue #219, with #240 and the modular framework #221). The Winlink RMS gateway is **LinBPQ**
(G8BPQ's packet switch) in its own container, speaking KISS over TCP to **Dire Wolf**, the soundcard packet modem, in a second
container. The first slice is an **amateur-only, 1200 baud AX.25, dedicated-radio** gateway. The repeater's core is not involved:
it neither owns nor leases anything for packet, and nothing about its radio, sound card or PTT changes.

## Decisions

- **Backend: LinBPQ.** It is user-space, has its own AX.25 and a TCP KISS port, needs no kernel module and no host
  package, and has an arm64 build (checked: version 6.0.26.1 (upstream master of 2026-11-19), built from source in a Debian 13 container on a Raspberry Pi 3B+, where it
  starts and opens its KISS-over-TCP port and its admin page).
- **Rejected: Linux RMS Gateway.** It sits on the kernel's AX.25 stack (an AX.25-enabled kernel, `ax25-tools`, `ax25-libs`), and a
  container cannot load kernel modules. That is host reliance, which every service container here rules out. (Issue #219
  also notes a binary-only component; not checked, since the kernel dependency already decides it.)
- **Modem: Dire Wolf** (GPL-2.0, 1.8.1), in its own container. It is the only part with devices: one sound card and, optionally,
  one CM108-type PTT device. A hardware TNC with a KISS TCP port can take its place (set the gateway's KISS host).
- **Two containers, not one.** The modem owns devices and is replaceable by a hardware TNC; the gateway owns a network connection
  to Winlink and a password. They fail, restart and upgrade separately, and the gateway has no device at all.
- **Radio access (#240): a dedicated radio per module.** The packet radio has its own sound card and PTT. Compose maps that
  one card's device files (never `/dev/snd` as a whole) and the one PTT device, so the repeater's card and PTT are not in the
  container and cannot be opened or keyed by a wrong setting. A **shared radio is deferred** (designed in ADR 0007, not built): it needs safe arbitration with the
  core (exclusive ownership, voice priority, PTT release on failure) and the maintainer's approval for any core change, and
  is not promised here.
- **Amateur only.** The gateway refuses a GMRS-looking callsign (four letters and three or four digits) and a service other than
  `amateur`, at start. This guards against a mistake and is not a licence check. It has no path to the GMRS audio or the links.
- **No redistribution of upstream's source.** LinBPQ's files carry GPLv3 notices but upstream publishes no LICENSE file and no
  statement about redistribution, so nothing of it is committed here. The image builds it from a pinned upstream commit, checked after
  the fetch, on the owner's machine. Dire Wolf is built the same way, from a pinned commit.
- **Nothing is published.** The admin page (8080), the telnet port (8010) and the KISS port (8001) are on the private services network
  only. The gateway reaches Winlink by an outbound connection.
- **Credentials.** The callsign, locator, the admin login and the Winlink gateway password are in `winlink.env` (readable by the
  owner only, ignored by git), never defaults, never printed, and written only into the gateway's in-memory configuration
  (a tmpfs). The configuration is made from the environment at every start and checked first; a value that could add lines to it
  or break it stops the start. Only the gateway reads `winlink.env`. The packet modem decodes untrusted radio traffic and holds an
  audio device, so it gets `direwolf.env` (call, SSID and PTT settings, no passwords).
- **Sessions and counters come from LinBPQ's own session record and Dire Wolf's output, through a small reporter in each container
  and an in-memory volume.** LinBPQ writes one record per session that reached Winlink (its ADIF log, `ENABLEADIFLOG=1`: callsign, end
  time, length, messages and message sizes each way, how it ended) and one line per accepted connection (`LogAllConnects=1`), in its
  tmpfs log folder. The reporter (`docker/winlink-reporter.py`, standard library only) reads those two files, nothing else in the folder,
  and writes a short whitelist of facts to `/winlink-status/gateway.json`; in the modem container it runs Dire Wolf as a child, passes its
  output on, replacing the block it prints for each frame (the sender's call and the frame's text) with one line that has the audio level and
  no text, and counts lines that start like a frame heard or a frame sent. The API mounts the volume read-only,
  checks every field again, caches for 15 seconds like the status probes, and logs `winlink.session_ended` with facts and no callsign.
  Nothing is written to disk: the volume is a tmpfs and the counts start again with the container. Dire Wolf does not log each time
  it keys the transmitter, so frames sent stands in for it, and the page says so.
  Rejected: an HTTP read of LinBPQ's own pages (the Links and Users pages show only what is connected right now, so a poll misses short
  sessions and cannot know counts, and each request adds a line to LinBPQ's telnet log); its telnet monitor commands (they need the
  sysop password in the API); reading container logs from the API (that needs Docker access, which it must not get); LinBPQ's Winlink
  access log (a protocol trace, not a record of sessions); and a second KISS client in the API (it would put every frame's text in
  the API's memory). LinBPQ's telnet log is never read: it records what a user types at its login, a password included.
- **Two calls.** The node answers as the plain call and the Winlink application as CALL-SSID, and the application connects through the
  telnet port (`C 2 CMS`, the port number of the telnet port in the generated file). Found while building the above, on a bench with a
  stand-in modem and CMS: with the node and the application on one call, or with the command pointing at the radio's port, a station
  that connected never reached Winlink. The first slice's tests only checked the file's text.
- **Honest readiness.** Without the Winlink password the gateway starts but says that nothing can reach Winlink. Listing on Winlink's
  channel list (which publishes the station) is a separate, off-by-default setting. Installing this grants no Winlink authorization:
  that is the operator's step.

## What is not decided or not done

- **Requester answers** (#219): same computer or radio, which modes, a public CMS gateway or local messaging. Until they are in, this is
  the proposed topology, not a confirmed one.
- **Hardware is unverified.** The entrypoints and compose are tested without radios; the audio path, PTT, an AX.25 connection and a
  message exchange through Winlink's CMS need a real radio and an authorized station, and are not claimed.
- A dashboard status page is built (read-only: three network probes with a 2 second timeout and a 15 second cache, no Docker access, so
  no start or stop; `docs/winlink.md`), and it shows sessions and counters (above). Not built: start and stop, VARA FM, 9600 baud,
  several ports, off-grid delivery (ADR 0008: no RMS Relay, and a LinBPQ mail mode only if wanted).
- **Session records and counters are checked on a bench, not on the air.** The parsing was written against output captured from the pinned
  LinBPQ build run against a stand-in packet client and a stand-in Winlink CMS, and from Dire Wolf fed test audio. A real station and the
  real CMS may write something the samples do not show. LinBPQ's record is written only for sessions that reached the CMS.
- Whether to keep building LinBPQ ourselves or ask upstream for a licence file.

## Considered options

- **Linux RMS Gateway on the host**: rejected, see above.
- **Pat (a Winlink client) as the gateway**: it is a client, not an RMS.
- **One container for both**: rejected, see above.
- **The core owning the radio for packet (a lease)**: deferred, needs design and approval (#240).
