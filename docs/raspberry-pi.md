# Raspberry Pi deployment

Runs as a single systemd service (`api.app:main`, which is the FastAPI
backend + dashboard + background tick/APRS/link loops, all in-process --
see the README's architecture section). Assumes Raspberry Pi OS Bookworm
(64-bit, Debian 12-based), which ships Python 3.11.2, satisfying this
project's `>=3.11` requirement.

## 1. System packages

```
sudo apt update
sudo apt install -y python3-venv python3-dev libportaudio2
```

`libportaudio2` is PortAudio's runtime library, needed by `sounddevice`
(`audio_io`'s stream layer) even before any real audio hardware is
attached.

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

## 4. Configuration (systemd EnvironmentFile)

```
sudo mkdir -p /etc/moreopenrepeater
sudo cp packaging/moreopenrepeater.env.example /etc/moreopenrepeater/env
sudo $EDITOR /etc/moreopenrepeater/env   # at minimum, set the AMI secret if using link.node_link
sudo chown root:moreopenrepeater /etc/moreopenrepeater/env
sudo chmod 640 /etc/moreopenrepeater/env
```

**Security note**: anyone who can reach the HTTP port and pass auth (if
enabled) can fully reconfigure the repeater (change timers, upload audio,
add DTMF macros that key the AllStarLink link, etc). Set
`MOREOPENREPEATER_AUTH_USER`/`MOREOPENREPEATER_AUTH_PASSWORD` in the env
file to require signing in: the dashboard then shows a login page and uses
an HttpOnly session cookie (12-hour lifetime, revoked on sign-out, and
cleared on service restart), and scripts can still use HTTP Basic
(`curl -u admin:... http://127.0.0.1:8000/api/status`). Every API route
and the WebSocket require one or the other; only the login page and the
static JS/CSS (no secrets in those) are public. Setting only one of the two
variables is treated as a misconfiguration and refuses to start rather than
silently running open. There's still no TLS, so the password travels in
the clear -- that's fine over loopback/SSH-tunneled/Tailscale access, not
fine if you expose the port directly to an untrusted network. The env
file's default (`MOREOPENREPEATER_HOST=127.0.0.1`) only listens on
loopback either way; reach it remotely over SSH port-forwarding,
Tailscale, or a VPN rather than exposing the port directly, unless you put
a reverse proxy with real TLS in front of it yourself.

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

## Still not covered here

- `audio_io`/`link` aren't both exercised on real repeater hardware end to
  end yet -- see the README's architecture section and "Status" for what's
  confirmed against real hardware/a real AllStarLink instance so far vs.
  what's still simulated via the dashboard's `simulate_*` controls.
- No backup/restore automation beyond the dashboard's manual "Download
  configuration" snapshot -- back that up yourself before major changes.
