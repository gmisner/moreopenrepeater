# Running the core alone (experimental)

Stage 3 of the modular migration ([#229](https://github.com/gmisner/moreopenrepeater/issues/229)).
`moreopenrepeater-core` runs the repeater with **no dashboard, no API and no
accounts**. It is an option for testing the split. It is **off by default** and
`install-pi.sh` does not install it. The normal service is unchanged.

## What it runs

The controller tick, IDs and time-outs, announcements, text-to-speech
(Piper), the audio engine and PTT, the link radio, AllStar and DMR audio,
autopatch, GPIO and the TX fan, and the systemd watchdog. It starts from the
saved configuration file (`data/state.json`) and needs no network and no other
service.

## What it does not run

The dashboard and API, sign-in, backups, updates, alerts, weather fetching,
APRS, the hub, mailbox, transcripts, streaming, Home Assistant, and the
AllStar AMI link-control loop (link audio still works; link state shown in the
dashboard and macro-driven link commands over AMI do not). Scheduled weather
announcements need the weather module and do not run.

## Before you use it

1. Set the repeater up with the normal service first. The core alone cannot be
   set up, and it **refuses to start** if the saved configuration is missing,
   unreadable, not valid JSON, or has no callsign. It never falls back to
   defaults, because that would put an unidentified repeater on the air.
2. A time-out, ID interval, hang time or link or autopatch limit that is not a
   usable number is replaced by its default (logged as a warning), so a bad
   value can't key the transmitter and drop it at once.
3. Stop the normal service. The unit declares `Conflicts=moreopenrepeater.service`,
   so systemd does this for you, but check:
   `sudo systemctl stop moreopenrepeater`.

## Try it

```
sudo cp /opt/moreopenrepeater/packaging/moreopenrepeater-core.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl stop moreopenrepeater
sudo systemctl start moreopenrepeater-core
journalctl -u moreopenrepeater-core -f
```

To go back: `sudo systemctl stop moreopenrepeater-core && sudo systemctl start moreopenrepeater`.
Do not `enable` it until you have tested it on your hardware.

Exit code 2 means the saved configuration is unusable. systemd will not restart
it (`RestartPreventExitStatus=2`); the journal says what to fix.

## Status

Verified by tests only (simulation). Not yet run on a Raspberry Pi. See the
[design](research/core-extraction-design.md) for what must be shown on real
hardware: stopping the API during a transmission does not interrupt PTT, the
time-out or the ID, and killing the core drops PTT.
