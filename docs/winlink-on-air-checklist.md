# Winlink gateway: bring-up and on-air checklist

Nothing in this project has been verified on the air or against Winlink's CMS (see `docs/winlink.md` and ADR 0006). This is the
order to verify it in, so that each step proves one thing and a failure points at one place. Do the steps in order, and stop at the
first one that fails. The owner of the station does this, with an authorized callsign. Record what you saw: the dashboard never
says "authorized" or "working on air" for you.

## Before anything transmits

- [ ] You hold the amateur license for the callsign you put in `winlink.env`. It is not a GMRS callsign (the gateway refuses those).
- [ ] You have the Winlink gateway password from Winlink's sysop team, or you are testing locally and leave `MOR_WINLINK_CMS_PASSWORD` unset.
- [ ] `MOR_WINLINK_REPORT` is **off**. Do not list the gateway on Winlink's channel list until the last step.
- [ ] The packet radio has its **own** sound card and PTT, a fixed card number, and is not the repeater's radio.
- [ ] A **dummy load** on the packet radio for steps 1 to 4.

## 1. Containers and plumbing (no radio needed)

```
cd docker && docker compose --profile winlink up -d --build
docker compose ps direwolf winlink
```

- [ ] Both containers healthy. Dashboard **Integrations, Winlink** shows the gateway and the modem answering.
- [ ] `docker compose logs winlink` says "CMS OFF" (no password) or that Winlink is reachable (password set). It does not print the password.

Proves: the images, the private network, and that LinBPQ attached to Dire Wolf's KISS port. Does not prove audio or RF.

## 2. Receive audio (dummy load off, antenna on, or a signal generator)

- [ ] Dire Wolf's log shows audio level lines when a packet station transmits nearby (a handheld sending an APRS beacon is fine, receive only).
- [ ] The level is not clipping (Dire Wolf's documentation gives the level to aim for) and the dashboard's modem counter shows frames heard.

Proves: sound card selection, receive levels, decode. Does not prove transmit.

## 3. PTT and transmit audio (dummy load on)

- [ ] Make the gateway transmit once, briefly: a short test beacon (a `PBEACON` line in Dire Wolf's config, removed afterward) or a connect attempt from a second radio. The radio keys, then unkeys.
- [ ] PTT releases every time. If it ever sticks on, stop and fix PTT first (this is the same class of problem as a USB drop on keying: look at power, RF getting back into the cable, and the cable's ground).
- [ ] A second receiver (or the HT) hears a clean packet burst, and Dire Wolf's transmit level is not overdriving the radio.

Proves: PTT device, transmit audio level, TXDELAY. Keep transmissions short, and identify as the rules require.

## 4. A local connection (still on the dummy load or a low-power local path)

- [ ] A second station (a radio with a TNC or soundcard modem, or Pat on a second Pi) connects to `CALL-10`. The dashboard shows a session.
- [ ] The connection stays up for a few minutes and ends cleanly. Counters match what you did.

Proves: AX.25 connect and disconnect through LinBPQ. Does not prove Winlink.

## 5. Winlink (needs the gateway password)

- [ ] Set `MOR_WINLINK_CMS_PASSWORD`, restart the `winlink` container, and confirm the log no longer says "CMS OFF".
- [ ] From a client you control, connect to `CALL-10` and exchange a **test message to yourself**. It arrives. Check the Winlink web mailbox.
- [ ] Nothing sensitive or prohibited goes in the test message. Amateur radio email is not private or encrypted.

Proves: the gateway's path to the CMS and back, with your password. This is the step that needs authorization from Winlink.

## 6. Only then, public

- [ ] Run for a day on a quiet channel and watch the dashboard counters, the temperature, and that the repeater (separate radio) is untouched.
- [ ] If you want it listed, set `MOR_WINLINK_REPORT=1` and the channel frequency. This publishes your station. It is the operator's choice.

## What to send back if something fails

The step number, the output of `docker compose logs --tail 100 direwolf winlink`, the dashboard Winlink page, and (if PTT or USB is involved)
the kernel log lines from the Support bundle. Never paste `winlink.env` or a password.
