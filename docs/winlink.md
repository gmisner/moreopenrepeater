# Winlink RMS gateway (optional, experimental)

An amateur-radio **Winlink RMS gateway**: stations with a packet radio connect to it on VHF/UHF, and it passes their messages to and from
Winlink over the Internet. It runs as two optional containers, `direwolf` (the packet modem) and `winlink` (the gateway, LinBPQ). See
`docs/adr/0006-winlink-rms-gateway.md` for why. It is off by default; an existing install changes nothing.

**Status.** The containers build and start on a Raspberry Pi 3B+ (arm64): the gateway attaches to the modem's KISS port and sets its timing,
with the modem fed test audio instead of a radio. Nothing here has been verified on the air or against Winlink's CMS: that needs a packet
radio and an authorized station. The modem's sound card and PTT path are tested only as configuration. The session list and counters
(below) were checked on a bench against a stand-in packet client and a stand-in Winlink server talking to the real LinBPQ, and against
real Dire Wolf output from test audio. They have not seen a real station or the real CMS. Treat it as a bench build.

## What you need

- **A packet radio of its own.** Not the repeater's radio or its sound card: they stay with the core. A VHF/UHF radio with a USB sound
  interface (a CM108-type one is easiest; the PTT can come from it) on a packet channel. A repeater and a packet gateway on one radio
  is not supported.
- **A Winlink gateway password**, from Winlink's sysop team. The operator, the licensee or their trustee, does this. Installing MOR does not
  grant access to the network, and the dashboard never says "authorized" on the strength of a connection.
- **An amateur callsign.** The gateway is for amateur stations. It refuses a GMRS callsign, and is not offered on GMRS: GMRS's limited
  digital uses are not a licence for a general email gateway. (This is a guard against a mistake, not a licence check.) Check the rules for your
  station: in the US that is Part 97 (identification, station control and automatic operation, permitted emissions and frequencies, third-party
  traffic, and what may not be sent). Amateur-radio email is not private: do not expect encryption over the air.

## Set it up

```
cd docker
cp winlink.env.example winlink.env && chmod 600 winlink.env     # set the callsign, locator, an admin password
sh ./split-winlink-env.sh                                        # the modem's own settings file, direwolf.env, made from it
```

There are two settings files on purpose. `winlink.env` is for the gateway and holds its two passwords. The modem (Dire Wolf) decodes
whatever it hears on the air and holds an audio device, so it is given `direwolf.env` instead: the callsign, SSID and PTT settings
and nothing secret (`direwolf.env.example` lists them). `split-winlink-env.sh` makes `direwolf.env` from the callsign, SSID and PTT
lines of `winlink.env`, only if there is no `direwolf.env` yet, and `./setup.sh` and `./update.sh` run it too, so an install that
already has a `winlink.env` keeps working after an update. It is a copy: if you later change the callsign, SSID or PTT lines in
`winlink.env`, change them in `direwolf.env` as well. Never put a password in `direwolf.env`.

In `.env` give the packet radio's sound card (its number from `cat /proc/asound/cards`, which must not be the repeater's) and, if it has one,
the PTT device:

```
MOR_PACKET_CARD=1
MOR_PACKET_PTT_HIDRAW=/dev/moreopenrepeater-packet     # then MOR_PACKET_PTT=cm108 in direwolf.env
```

**If the packet radio's interface is a CM108 or an AIOC, name it, even if you only use it for sound.** The repeater's PTT rule gives
the name `/dev/moreopenrepeater-ptt` to every CM108 and AIOC interface it is not told to leave out, and two cannot share it. After a
reboot or a replug the packet radio's interface can win that name, and the core would key the packet radio instead of the repeater.
Plug both interfaces in and run:

```
./setup.sh --list-interfaces                 # shows each one's ID
./setup.sh --packet-radio D4E5F6             # the packet radio's ID (or port:1-1.4 for one with no serial number)
```

That installs a udev rule (with sudo) that names the packet radio's interface `/dev/moreopenrepeater-packet`, which does not move when
`/dev/hidrawN` does, and leaves it out of the repeater's name; the repeater's own interface stays `/dev/moreopenrepeater-ptt`
(`docs/docker.md`, GPIO, serial ports and a link radio). An install with no packet radio, or one that uses a serial or GPIO PTT for
it, does not need this. If `MOR_PACKET_PTT=cm108` is set and `MOR_PACKET_PTT_HIDRAW` is not (or names a device that is missing), the
modem refuses to start and says so, because the container would otherwise be given an empty device and never key.

Give the card a fixed number so it is the same after a reboot (for a USB card, `options snd-usb-audio index=...` in the host's modprobe
configuration), or the next start would find the repeater's card where the packet radio was. Only that one card's device files are put in the
container, so the modem cannot open another, and if the number is wrong it fails to start.

```
docker compose --profile winlink up -d --build
docker compose ps direwolf winlink
docker compose logs winlink          # says whether Winlink is reachable: "CMS OFF" until MOR_WINLINK_CMS_PASSWORD is set
```

Set `MOR_WINLINK_CMS_PASSWORD` when you have the gateway password. Setting `MOR_WINLINK_REPORT=1` (with the channel's frequency) lists the
gateway on Winlink, which publishes your station: leave it off until the gateway has been tested with a client you control.

## What it does and does not do

- The gateway answers as `CALL-10` (`MOR_WINLINK_SSID`) on the modem's 1200 baud AX.25 port and hands connections to Winlink's CMS. LinBPQ's
  node answers as the plain `CALL`, a separate call on purpose: with both on one call a station that connects gets the node and never
  reaches Winlink. Its admin page (user `sysop`) and telnet port are on the private network only: nothing is published to the host or the
  LAN. To open the page, give the `winlink` service a published port bound to the loopback, in a file of its own that you list in
  `COMPOSE_FILE` in `.env` for as long as you need it, for example `compose.winlink-admin.yaml`:

  ```
  services:
    winlink:
      ports:
        - "127.0.0.1:8080:8080"
  ```

  and `COMPOSE_FILE=compose.yaml:compose.winlink-admin.yaml` in `.env` (append `:compose.winlink-admin.yaml` to the line that is
  there if there is one), then `docker compose --profile winlink up -d`. Take it out of the line again when you are done. A
  `compose.override.yaml` is not a way to do this on a machine where `setup.sh` has set `COMPOSE_FILE` (for `--gpio` and the others):
  Compose reads that file on its own only while `COMPOSE_FILE` is not set. Do not use `-f` either; it also ignores `COMPOSE_FILE`
  (`docs/docker.md`, Compose files and the `-f` option).
- It keeps no message store, and its own logs and running state are in memory (a tmpfs), so there is nothing to back up. Dire Wolf
  prints a block of text for every frame it hears (every station it can hear, not only ones for the gateway: the sender's call, the
  frame's text, and what it decoded from it). Docker would keep that on the SD card in the `direwolf` container's log, so the reporter
  does not pass it on. In its place the log has one line per frame, `frame heard, audio level 80(23/22)` or `frame sent`, with no call
  and no text. The audio level is what to check when setting up (`docs/winlink-on-air-checklist.md`). Dire Wolf's start-up text, its
  errors and its KISS client messages are passed on as they are. The decoded text is never stored anywhere.
- The **gateway** container runs a small reporter (`docker/winlink-reporter.py`, standard library only) beside LinBPQ. LinBPQ keeps two
  logs of its own for this, in its in-memory log folder: one record per session that reached Winlink (`ENABLEADIFLOG=1`), written
  when the session ends, and one line per connection accepted (`LogAllConnects=1`). The reporter reads those two files and nothing else
  in that folder, and writes `gateway.json`.
- The **modem** container runs Dire Wolf through the same reporter. It passes Dire Wolf's output on, except the block it prints for each frame
  (see above), which it replaces with one line, and counts two kinds of line by their first characters: a frame heard and a frame
  sent. It writes `modem.json`.

**What it never reads or shows.** No message text and no password. The reporter writes a short list of facts (counts, times, an
outcome word, and a callsign that looks like one) and nothing a station typed, such as its software's name or its locator; the API
checks the files again before it shows anything. Two LinBPQ files are left alone on purpose: its telnet log, which records what a user
types at its login (a password included, in the container's memory only), and its Winlink access log, which is a trace of the protocol.
Dire Wolf's own output does contain the text of frames it decodes, and `docker compose logs direwolf` always has: the reporter only
looks at how each line starts and keeps none of it. Message text, a station's identification and passwords never reach the dashboard's
log either.

**The log.** When a session ends the API logs one `winlink.session_ended` event with its facts: `duration_s`, `messages_in`,
`messages_out`, the byte counts and the `outcome`. The station's callsign is not in the event; it is shown to the signed-in dashboard
user and nowhere else. The event is written when the page (or anything else that asks for `/api/winlink`) next reads the status, so
with nobody looking it appears at the next look. Sessions that were already there when the API started are not announced.

**Updating an existing install.** The two images now carry python3 and the reporter, and the API gets the new read-only volume, so run
`docker compose --profile winlink up -d --build` from the docker folder (or `./update.sh`). The gateway's configuration also changed: the
node and the Winlink application are on separate calls now (see above), which needs the rebuilt container to take effect.

The API finds the services at `winlink` (the gateway) and `MOR_DIREWOLF_IP` (the modem, 172.28.0.40 by default), set in `docker/compose.yaml`
as `MOREOPENREPEATER_WINLINK_HOST` and `MOREOPENREPEATER_WINLINK_MODEM_HOST`. The ports (8080, 8010, 8001) can be changed with
`MOREOPENREPEATER_WINLINK_ADMIN_PORT`, `_TELNET_PORT` and `_KISS_PORT` in `api.env`, for a hardware TNC or another gateway.

## The images

Both are built on your machine from a pinned commit of the upstream source: LinBPQ (G8BPQ, `g8bpq/linbpq`, master of 2026-11-19, version 6.0.26.1) and
Dire Wolf (`wb2osz/direwolf`, 1.8.1, GPL-2.0). LinBPQ's files carry GPLv3 notices, but upstream publishes no LICENSE file or redistribution
terms, so none of it is in this repository or a published image. Changing the pinned commit is a deliberate edit of `Dockerfile.winlink` or
`Dockerfile.direwolf`.

## Bringing it up on the air

The order to check things in, one proof per step, is in `docs/winlink-on-air-checklist.md`.
