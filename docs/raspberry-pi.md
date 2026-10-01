# Raspberry Pi deployment

Runs as a single systemd service (`api.app:main`, which is the FastAPI
backend + dashboard + background tick/APRS/link loops, all in-process --
see the README's architecture section). Assumes Raspberry Pi OS Bookworm
(64-bit, Debian 12-based), which ships Python 3.11.2, satisfying this
project's `>=3.11` requirement.

## Quick install

```
curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash
```

`scripts/install-pi.sh` does sections 1-5 below (and generates an admin
password), and running it again updates an existing install. Options go
after `-s --`, following `bash`:

- `--channel stable|beta|dev` picks which releases to follow (default
  stable, or whatever was chosen before); see [Updates](#6-updates).
- `--lan` listens on the local network instead of only on the Pi.
- `--allstar` also installs AllStarLink (ASL3) from its apt repository, for
  linking and autopatch. It gives the controller an AMI login of its own
  (usable only from the Pi) and lets it edit the node's settings; see
  [allstar.md](allstar.md). Then set up the node with `sudo asl-menu` and
  choose it on the dashboard's **AllStarLink** page. EchoLink is turned on
  from the same page.
- `--protect-sd` keeps the logs in memory to spare the SD card, and
  `--no-protect-sd` stops doing so; see [SD card](#sd-card).

```
curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash -s -- --allstar
```

The rest of this page is the same procedure by hand.

## 1. System packages

```
sudo apt update
sudo apt install -y python3-venv python3-dev libportaudio2 espeak-ng
```

`libportaudio2` is PortAudio's runtime library, needed by `sounddevice`
(`audio_io`'s stream layer) even before any real audio hardware is
attached. `espeak-ng` gives the voice ID, announcements and weather alerts
a text-to-speech voice (`pico2wave` from `libttspico-utils` also works and
sounds better, if your distribution has it). Without one, IDs fall back to
CW and spoken announcements can't render.

## 2. Dedicated user + install location

Runs as its own unprivileged system user rather than `pi`/root -- the
`audio_io.cm108` GPIO driver and `sounddevice` need real device access
(handled by the udev rule + group membership below), but nothing here
needs to run as root. The code itself belongs to root and only `data/` to
the service, so a compromised dashboard can't change the code (which the
updater runs as root).

```
sudo useradd --system --no-create-home --home-dir /opt/moreopenrepeater --shell /usr/sbin/nologin moreopenrepeater
sudo usermod -aG audio,dialout,gpio moreopenrepeater   # /dev/hidrawN (below), USB-serial, header pins

sudo git clone --branch stable <this repo's URL> /opt/moreopenrepeater
cd /opt/moreopenrepeater
sudo python3 -m venv .venv
sudo .venv/bin/pip install -e .
sudo install -d -o moreopenrepeater -g moreopenrepeater data
```

An editable install (`-e .`) is deliberate, not just a dev convenience:
`api.app`'s default data/log paths resolve relative to the installed
package's location on disk (`DEFAULT_DATA_DIR`/`DEFAULT_LOG_PATH` in
`src/api/app.py`), which only lands somewhere sensible -- next to the repo
checkout, at `/opt/moreopenrepeater/data` -- when installed this way. Set
`MOREOPENREPEATER_DATA_DIR`/`MOREOPENREPEATER_LOG_PATH` (see the env file
below) if you'd rather use e.g. `/var/lib/moreopenrepeater`.

## 3. CM108 USB sound-card interface permissions

`/dev/hidrawN` device nodes are root-only by default, and the process runs
as `moreopenrepeater`, not root:

```
sudo cp packaging/99-cm108.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

(Membership in the `audio` group, done above, is what the rule grants
access to.) Plug in the interface and confirm with
`ls -l /dev/hidraw*` -- it should show `root audio` ownership, mode
`crw-rw----`.

The service finds the interface itself when it starts, so after plugging
one in, restart it (`sudo systemctl restart moreopenrepeater`). The N in
`/dev/hidrawN` depends on what else is plugged in, so it's looked up by
USB ID rather than configured. With more than one CM108, pick one with
`MOREOPENREPEATER_CM108_HIDRAW=/dev/hidrawN` in the env file (next
section), or set it to `off` to ignore the interface.

With an interface found, the live audio engine keys the radio through the CM108's
GPIO3 (PTT) whenever it transmits, and "CM108 COS input" becomes available
as the carrier-detect source. That's the chip's volume-down input, which is
where URI/RIM/DMK-style interfaces wire the receiver's COS (the same as
AllStarLink's SimpleUSB driver). **COS polarity** on the Radio interface
card says which level means carrier: "Active low" (the default, the pin
pulled to ground) matches SimpleUSB's `carrierfrom=usbinvert`, and "Active
high" matches `carrierfrom=usb`. Reading it needs Linux 5.11 or later,
which every current Raspberry Pi OS has.

A plain USB sound dongle can be turned into an interface with a transistor
and a little soldering: see [cm108-wiring.md](cm108-wiring.md).

Before going on the air, check the interface with the repeater stopped and
a dummy load on the transmitter:

```
sudo systemctl stop moreopenrepeater
sudo -u moreopenrepeater /opt/moreopenrepeater/.venv/bin/python /opt/moreopenrepeater/scripts/cm108_check.py
```

It names the chip, keys PTT for a second, then shows COS and the GPIO
inputs live for 15 seconds while you key a radio on the receive frequency.
Add `--outputs 1,4` to switch spare pins on and off too. PTT is left off
when it exits, even on Ctrl-C.

### GPIO pins

The interface's spare pins are set up under **Audio & tones → CM108 GPIO
pins**: GPIO1, GPIO2 and GPIO4, and GPIO5-8 on CM119-family chips (GPIO3 is
PTT, and on a CM108AH GPIO2 isn't a real pin). Check which ones your
interface brings out to its connector.

- An **output** drives the pin high when on, for a relay driver, a fan or a
  remote reset. Outputs are switched on the Audio page or the dashboard, and
  by a DTMF macro with the "Switch a GPIO output" action (on, off, toggle,
  or on for up to 60 seconds). The macro says what it did on the air. Outputs
  start off whenever the repeater starts.
  **Output schedules**, below the pins, switch an output on at a set time on
  chosen weekdays and off after a number of minutes (0 leaves it on): a
  light from 18:00 for 300 minutes, say. Times are the station's clock.
  Switching a scheduled output off by hand keeps it off until the next
  start, and after a restart an output that should be on comes back on. The
  dashboard shows when a scheduled output goes off.
- An **input** shows on or off on the dashboard, for a door switch or a
  power-fail alarm. Changes are written to the log. Tick **On when the pin
  reads low** for a switch or relay contact that pulls the pin to ground.
  An input can say something on the air when it turns on and when it turns
  off ("Commercial power has failed."), and run a saved DTMF macro, for
  example one that switches an output or plays an announcement. It waits
  for a clear channel like any announcement. A change only counts once it
  has held for a second, so a bouncing contact acts once. The state at
  startup isn't treated as a change.

The pins come straight from the chip and can't power a relay coil: drive
one through a transistor.

### PTT and COS on the Pi's own pins

Without a CM108 (a sound card with no GPIO, or a HAT), PTT and COS can use
the Pi's 40-pin header instead. On the Radio interface card, set **PTT**
and/or **Carrier detect** to "Raspberry Pi GPIO pin". Pins are BCM GPIO
numbers; the defaults are GPIO17 (header pin 11) for PTT and GPIO27 (header
pin 13) for COS, with ground on header pin 9 or 14. The pins are held only
while live audio runs. The service user needs the `gpio` group, which the
installer adds.

The Pi's pins are 3.3 V only and can't drive a radio's PTT line directly:

```
GPIO17 (pin 11) ──[ 2.2k-4.7k ]── base
                                    2N3904   collector ── radio PTT
                                             emitter ──── ground (pin 9)
```

With this circuit, set **PTT polarity** to "Active high". Prefer it to
"Active low" wiring: at power-up, and whenever the repeater isn't running,
the pin is low, so an active-low PTT would key the radio while the Pi
boots.

For COS, a receiver output that pulls to ground on carrier (open collector)
goes straight to GPIO27, with **COS polarity** "Active low"; the repeater
turns on the Pi's internal pull-up. A COS output that goes to 5 V or more on
carrier must go through a transistor, as in
[cm108-wiring.md](cm108-wiring.md#cos) (collector to GPIO27, still "Active
low"). "Active high" turns on the pull-down instead, for a 3.3 V signal that
goes high on carrier.

### Audio devices

In the dashboard, open **Audio & tones → Radio interface**: pick the CM108
as both input and output (it shows up as something like `USB Audio Device`),
choose the carrier-detect source, and enable live audio. Without a COS wire,
"Audio level (VOX)" works with a receiver whose squelch mutes its audio
output; set the VOX threshold a few dB above the idle level shown on the
meter. "CTCSS tone present" opens only when a sub-audible tone is decoded,
which requires the receiver's audio to be unfiltered (discriminator or
flat audio) so the tone reaches the sound card.

## 4. Configuration (systemd EnvironmentFile)

```
sudo mkdir -p /etc/moreopenrepeater
sudo cp packaging/moreopenrepeater.env.example /etc/moreopenrepeater/env
sudo $EDITOR /etc/moreopenrepeater/env   # at minimum, set the AMI secret if using link.node_link
sudo chown root:moreopenrepeater /etc/moreopenrepeater/env
sudo chmod 640 /etc/moreopenrepeater/env
```

## Security: sign-in, users and HTTPS

Anyone who can reach the HTTP port and get past sign-in can reconfigure
the repeater (timers, audio, DTMF macros that key the AllStar link, and
so on), so turn sign-in on before the dashboard is reachable from anywhere
but the Pi itself.

**Sign-in.** Set `MOREOPENREPEATER_AUTH_USER`/`MOREOPENREPEATER_AUTH_PASSWORD`
in the env file. That account is always an admin and can't be edited from
the dashboard, so a lost users file never locks you out. Setting only one
of the two is treated as a typo and the service refuses to start. The
dashboard uses an HttpOnly session cookie (12 hours, revoked on sign-out,
cleared when the service restarts); scripts can use HTTP Basic
(`curl -u admin:... http://127.0.0.1:8000/api/status`).

**More users.** Admins can add accounts under **Tools → Users**, each with a
role:

| Role | Can |
| --- | --- |
| Admin | Everything, including managing users and reading the audit log |
| Operator | Change settings, macros, announcements, audio and recordings |
| Viewer | Look and listen only |

Passwords are stored as scrypt hashes in `data/users.json`. Role changes
and deletions apply to signed-in sessions immediately. Adding a user also
turns sign-in on if the env account isn't set; the first one must then be
an admin.

**Audit log.** Every change made through the dashboard or API is recorded
with who made it (failed sign-ins included), along with DTMF commands
dialed over the air, under **Tools → Audit log**. Settings changes show the
old and new values. Request bodies aren't stored, so passwords never end up
in it. Entries are kept for a year in `data/audit.db`.

**HTTPS.** The service speaks plain HTTP, so on its own the password
crosses the network unencrypted. The env file's default
(`MOREOPENREPEATER_HOST=127.0.0.1`) only listens on the Pi itself. To reach
it from elsewhere, pick one:

- **Tailscale** (simplest): install it on the Pi and your devices, then
  `sudo tailscale serve --bg 8000`. The dashboard is at
  `https://<pi-name>.<tailnet>.ts.net`, with a real certificate, and only
  your tailnet can reach it.
- **Caddy reverse proxy** (for a public hostname pointing at the Pi, ports
  80/443 open): `sudo apt install caddy`, then put this in
  `/etc/caddy/Caddyfile` and `sudo systemctl reload caddy`:

  ```
  repeater.example.org {
      reverse_proxy 127.0.0.1:8000
  }
  ```

  Caddy gets and renews a Let's Encrypt certificate itself, and passes
  WebSockets (live status, Listen live) through without extra config.
- **SSH port forwarding** for occasional access:
  `ssh -L 8000:127.0.0.1:8000 pi@<pi>`, then open `http://localhost:8000`.

Behind a proxy on the same Pi, the session cookie is automatically marked
`Secure`: uvicorn trusts `X-Forwarded-Proto` from localhost by default.
If the proxy runs on another machine, set `FORWARDED_ALLOW_IPS` to its
address in the env file.

## 5. Install and start the service

```
sudo cp packaging/moreopenrepeater.service packaging/moreopenrepeater-update.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moreopenrepeater moreopenrepeater-update.path
sudo systemctl status moreopenrepeater
```

Verify:

```
curl http://127.0.0.1:8000/api/status
journalctl -u moreopenrepeater -f      # systemd/stdout view
curl http://127.0.0.1:8000/api/logs?lines=50   # the app's own rotating log, same as the dashboard's Logs panel
```

## 6. Updates

Releases come in three channels, so a change can be tried before it
reaches repeaters on the air:

| Channel | Branch | Gets |
| --- | --- | --- |
| Stable | `stable` | Releases that went through beta. The default; keep production repeaters here. |
| Beta | `beta` | The next stable release while it's tested, on a test repeater. |
| Dev | `main` | Every change as soon as it's pushed, before any testing. |

A change lands on `main` (dev) first. Once its tests pass, the **promote**
GitHub Actions workflow (Actions tab, "Run workflow") moves `beta` up to it;
after it has run well on a test repeater, the same workflow moves `stable`
up to `beta`. It only promotes commits whose tests passed, and only forward.
From a terminal: `gh workflow run promote.yml -f channel=beta` (or
`channel=stable`).

To update, open the dashboard's **Updates** page (admins only). It shows the
installed version and what the chosen channel has that's new; pick a channel
and press **Update**. The repeater goes off the air for a minute or two, and
you may have to sign in again afterwards. If the new version doesn't start,
the previous one is put back and the page says so; the log of the last
update is on the same page and in `/var/lib/moreopenrepeater-update/update.log`.

How it works: the service can't update itself (it can't write the code), so
the page writes the chosen channel to `data/update-request`.
`moreopenrepeater-update.path` notices and starts
`moreopenrepeater-update.service`, which runs `scripts/update.sh` as root:
it runs the installer for that channel, reports progress in
`/var/lib/moreopenrepeater-update/status.json`, and goes back to the previous
commit if the service doesn't come up.

From a terminal, run the installer again (add `--channel beta` to switch):

```
curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash
```

Or by hand:

```
cd /opt/moreopenrepeater
sudo git pull
sudo .venv/bin/pip install -e .   # picks up any new/changed dependencies
sudo systemctl restart moreopenrepeater
```

## Backups

The dashboard's **Backup & restore** page (admins only) downloads a full
backup as a `.zip`. It holds the settings, DTMF macros, announcements, audio
clips, dashboard users, airtime statistics and the audit log, and optionally
the recordings. Restoring one on a fresh install brings all of that back;
recordings are added to any already there. The file includes the users'
password hashes, so keep it private.

The same page can save backups on the Pi itself, on demand or on a schedule
(every so many hours, keeping the newest few). They go in `backups/` in the
data directory unless `MOREOPENREPEATER_BACKUP_DIR` points elsewhere. An SD
card is the part of a Pi most likely to fail, so a USB drive or network share
is a better home:

```
sudo mkdir -p /mnt/usb/moreopenrepeater-backups
sudo chown moreopenrepeater: /mnt/usb/moreopenrepeater-backups
echo 'MOREOPENREPEATER_BACKUP_DIR=/mnt/usb/moreopenrepeater-backups' | sudo tee -a /etc/moreopenrepeater/env
sudo systemctl restart moreopenrepeater
```

If a scheduled backup fails (a full or unplugged drive), the page says so and
it tries again a minute later.

A backup doesn't include the env file (the built-in admin and the Asterisk
login) or Asterisk's own configuration, which holds the autopatch phone line.
Keep a copy of `/etc/moreopenrepeater/env` and `/etc/asterisk/` too.

## Alerts

The dashboard's **Alerts & health** page (admins only) sends a message when
the repeater needs attention:

- it started again after a crash or a power cut, with roughly how long it was
  off the air (it tells the two apart by whether the Pi rebooted);
- the stuck-carrier lockout engaged, and when it cleared;
- an update failed or was rolled back (and when one succeeded);
- the CPU passed the temperature limit (80 °C unless you change it), or the Pi
  reported under-voltage or throttling;
- live audio stopped for more than a minute, or the CM108 interface was unplugged.

It sends by [ntfy](https://ntfy.sh) (free push notifications to a phone app),
a Telegram bot, email, or a webhook (Slack and Discord webhook URLs work as
they are). "Send a test alert" tries every one that's set up. Each problem is
sent once when it starts and once when it's over, the same alert isn't sent
again within half an hour, and no more than a dozen go out an hour; a crash
loop sends one alert, then a count. Every alert is listed on the page and in
the audit log.

The settings, tokens and passwords included, are kept in `data/alerts.json`,
readable only by the service. They aren't part of the settings the dashboard
reads back, or of backups, so set them up again after restoring onto a new
card. Alerts need the internet; a repeater without it can still show its
health on the page.

The page also shows the CPU temperature, power and throttling flags, and how
much has been written to the SD card.

## SD card

Dead SD cards are the usual way an unattended Pi dies: constant small writes
wear them out. Out of the box the controller keeps its own writes small. Its
databases use a write-ahead log without a sync per change, airtime
statistics are written once a minute, and the settings file is only written
when you save. Logs are the biggest remaining writer, and reinstalling with
`--protect-sd` keeps them in memory:

```
curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash -s -- --protect-sd
```

That keeps the system journal in memory (`/etc/systemd/journald.conf.d/60-moreopenrepeater.conf`),
and the controller's own log in `/run/moreopenrepeater`. Both start afresh at
every reboot, so after a power cut the reason for it is gone. The crash and
power-cut alerts above are what's left. The choice is remembered by later
installs and updates; `--no-protect-sd` undoes it.

A read-only root filesystem (Raspberry Pi OS's overlay file system) would
protect the card further, but updates install into `/opt`, so they'd vanish
at the next reboot; it isn't offered. A swap file on the card
(`dphys-swapfile`) also writes to it; newer Raspberry Pi OS releases swap to
compressed memory instead. Use a good card (an "endurance" or A1/A2 one),
keep backups on a USB drive (see [Backups](#backups)), and the **Alerts &
health** page shows how much has been written to the card since boot and
over its life.

## Stuck-carrier lockout

Interference, a stuck microphone or a desensed receiver makes a repeater
time out again and again, sending the timeout tone over and over. After 3
timeouts within 15 minutes (a carrier that never drops counts again every
timeout period) the controller stops repeating. IDs still go out, as the
rules require. The dashboard shows a "Locked out" banner. It clears by itself
once the channel has been quiet for a minute, or with the banner's **Clear
lockout** button, or with a DTMF macro (the "Clear a stuck-carrier lockout"
action). The numbers are on the **Timing** page; 0 timeouts turns the lockout off.
Lockouts are counted on the Activity page and send an alert.

## Repeaters without internet

The repeater itself never needs the internet. These optional features do, and each
one is off until you turn it on:

- **APRS beaconing** and the **APRS map** connect to an APRS-IS server
  (`rotate.aprs2.net` by default). The map only receives. If the connection
  drops, it retries (backing off to every two minutes), and the APRS map page says it isn't
  connected. With no internet at all, you can point the APRS server setting at a
  local APRS-IS server (for example `aprsc` fed by your own RF iGate).
- **Map tiles** come from OpenStreetMap, loaded by the browser, not the Pi. If the
  browser can't reach them, the map shows range rings on a plain background.
  Clear the "Map tiles" setting to always use that, or point it at a tile server
  on your own network.
- **Weather alerts** come from `api.weather.gov` (US only). Alert areas on the
  map need one extra request per affected NWS zone, and only when the map is on.

`data/aprs.db` holds the stations the map has heard. It's safe to delete; it
refills as new reports arrive.

## Still not covered here

- The live audio engine is tested with synthetic signals and against Mac
  audio devices, but not yet with a CM108 and a real radio. The CM108 PTT,
  COS and GPIO code follows AllStarLink's SimpleUSB driver but hasn't run on
  real hardware yet; the Pi header pins have been checked with `pinctrl`,
  but not with a radio. Expect to tune the VOX threshold and TX gain on
  first key-up, and check the "audio glitches" count on the Radio interface
  card (dropped or late audio blocks).
- Audio processing fits on a Raspberry Pi 3B+: it averages about 4 ms of
  each 20 ms block, and 99% of blocks take under 6 ms
  (`scripts/benchmark_audio.py`). A desktop session with a
  web browser open on the Pi itself competes for the CPU and causes the
  occasional late block, so run the Pi without a desktop (Raspberry Pi OS
  Lite), or at least don't browse on it, and use the dashboard from another
  computer.
- AllStarLink audio (the node's `rxchannel` over USRP) is set up separately;
  see [allstar.md](allstar.md).
