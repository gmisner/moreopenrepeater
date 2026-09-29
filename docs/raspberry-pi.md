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

- `--lan` listens on the local network instead of only on the Pi.
- `--allstar` also installs AllStarLink (ASL3) from its apt repository, for
  linking and autopatch. It gives the controller an AMI login of its own
  (usable only from the Pi) and lets it edit the node's settings; see
  [allstar.md](allstar.md). Then set up the node with `sudo asl-menu` and
  choose it on the dashboard's **AllStarLink** page. EchoLink is turned on
  from the same page.

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
needs to run as root.

```
sudo useradd --system --create-home --home-dir /opt/moreopenrepeater --shell /usr/sbin/nologin moreopenrepeater
sudo usermod -aG audio,dialout moreopenrepeater   # /dev/hidrawN (below) and USB-serial, if used

sudo -u moreopenrepeater git clone <this repo's URL> /opt/moreopenrepeater
cd /opt/moreopenrepeater
sudo -u moreopenrepeater python3 -m venv .venv
sudo -u moreopenrepeater .venv/bin/pip install -e .
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

Then point the service at it in the env file (next section):

```
MOREOPENREPEATER_CM108_HIDRAW=/dev/hidraw0
```

With that set, the live audio engine keys the radio through the CM108's
GPIO3 (PTT) whenever it transmits, and "CM108 COS input" becomes available
as the carrier-detect source. That's the chip's volume-down input, active
low, which is where URI/RIM/DMK-style interfaces wire the receiver's COS
(the same as AllStarLink's SimpleUSB driver). Reading it needs Linux 5.11 or
later, which every current Raspberry Pi OS has.

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
- An **input** shows high or low on the dashboard, for a door switch or a
  power-fail alarm. Changes are written to the log.

The pins come straight from the chip and can't power a relay coil: drive
one through a transistor.

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
sudo cp packaging/moreopenrepeater.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moreopenrepeater
sudo systemctl status moreopenrepeater
```

Verify:

```
curl http://127.0.0.1:8000/api/status
journalctl -u moreopenrepeater -f      # systemd/stdout view
curl http://127.0.0.1:8000/api/logs?lines=50   # the app's own rotating log, same as the dashboard's Logs panel
```

## 6. Updating

```
cd /opt/moreopenrepeater
sudo -u moreopenrepeater git pull
sudo -u moreopenrepeater .venv/bin/pip install -e .   # picks up any new/changed dependencies
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
  real hardware yet. Expect to tune
  the VOX threshold and TX gain on first key-up, and check the "audio
  glitches" count on the Radio interface card (dropped or late audio blocks)
  under load on a Pi.
- AllStarLink audio (the node's `rxchannel` over USRP) is set up separately;
  see [allstar.md](allstar.md).
