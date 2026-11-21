# Winlink RMS gateway (optional, experimental)

An amateur-radio **Winlink RMS gateway**: stations with a packet radio connect to it on VHF/UHF, and it passes their messages to and from
Winlink over the Internet. It runs as two optional containers, `direwolf` (the packet modem) and `winlink` (the gateway, LinBPQ). See
`docs/adr/0006-winlink-rms-gateway.md` for why. It is off by default; an existing install changes nothing.

**Status.** The containers build and start on a Raspberry Pi 3B+ (arm64): the gateway attaches to the modem's KISS port and sets its timing,
with the modem fed test audio instead of a radio. Nothing here has been verified on the air or against Winlink's CMS: that needs a packet
radio and an authorized station. The modem's sound card and PTT path are tested only as configuration. Treat it as a bench build.

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
```

In `.env` give the packet radio's sound card (its number from `cat /proc/asound/cards`, which must not be the repeater's) and, if it has one,
the PTT device:

```
MOR_PACKET_CARD=1
MOR_PACKET_PTT_HIDRAW=/dev/hidraw1     # then MOR_PACKET_PTT=cm108 in winlink.env
```

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

- The gateway answers as `CALL-10` (`MOR_WINLINK_SSID`) on the modem's 1200 baud AX.25 port and hands connections to Winlink's CMS. Its admin
  page (user `sysop`) and telnet port are on the private network only: nothing is published to the host or the LAN. To open the page, give
  the `winlink` service a published port in a `compose.override.yaml` bound to the loopback, for as long as you need it.
- It keeps nothing but its running state and logs in memory (a tmpfs), so there is no message store to back up or to wear the SD card.
  Message text is not logged by MOR.
- The core and the repeater do not know it exists. If it stops, the repeater is unaffected; a restart policy brings it back.
- Not built yet: sessions and counters on the dashboard, start and stop from the dashboard, VARA FM, 9600 baud, several ports, a shared
  radio (designed in `docs/adr/0007-shared-radio-lease.md`, not built), and off-grid delivery. The modem's PTT and audio path and a full message exchange have not been tried on a radio.

## The dashboard page

**Integrations, then Winlink** is a read-only status page. The API makes three quick connections on the private network and reports
which answered: the gateway's admin page (any web answer counts, a login prompt included), the gateway's telnet port, and the modem's KISS
port. It says the same thing in words ("Answering" or "Not answering", with a short reason) and lists what to look at when one is down.

- **It does not start or stop anything.** The API has no access to Docker and is not given any. Run the containers from the repeater's
  computer, as above. With the profile off, nothing answers, and the page says so and shows the commands to turn it on. That is not an error.
- **It never slows anything down.** Each check gives up after 2 seconds, the three run together, and the answer is kept for 15 seconds, so a
  page that polls (this one asks every 30 seconds, and not while the tab is hidden) cannot hammer the gateway. A dead gateway does not
  delay the dashboard or the repeater. A change (it came up, one part went down) is logged once as `winlink.status_changed`.
- **It reads no message text and no password**, and returns none. "Answering" means the port accepts a connection, not that Winlink's CMS
  is reachable or that a station is authorized.
- **The callsign** is shown only if you also put `MOR_WINLINK_CALL` in `docker/.env` (the file compose reads for the API;
  `winlink.env` goes to the gateway only, and the API never sees the passwords in it). Without it the page says the callsign is not
  shown, and the gateway is unaffected.

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
