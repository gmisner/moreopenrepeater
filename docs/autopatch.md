# Autopatch

Autopatch lets repeater users place phone calls over the air. A user keys
up, sends the access code and the number, and unkeys (or ends with `#`):

    *6 8605551234 #

The repeater reads the number back and plays ringback while the call is
placed. Once it's answered, the caller's audio goes out over the repeater,
and whoever is transmitting is heard on the phone. The hang-up code (`#` by
default) ends the call, and so does the far end hanging up or the time
limit (users hear a warning 30 seconds before). The transmitter stays keyed
for the whole call, and the station ID still goes out on schedule.

Settings are on the dashboard's **Autopatch** page, which also shows the call
in progress, has a **Hang up** button, and can place a test call.

## How it works

Calls go out through the Asterisk that already runs beside the controller
for AllStarLink. Any Asterisk with the PJSIP and AudioSocket modules works
(tested with ASL3's Asterisk 22); app_rpt isn't involved:

1. The controller asks Asterisk over AMI to call the number on the phone
   line (a SIP trunk from a provider such as VoIP.ms).
2. When the far end answers, Asterisk runs `AudioSocket()`, which connects
   back to the controller (port 9092 by default) and carries the call audio.
3. The controller bridges that audio to the radio.

The phone line itself is set up from the dashboard too, over the same AMI
connection, so nothing in `/etc/asterisk` needs editing by hand.

## Setup

### 1. Connect the controller to Asterisk

In `/etc/moreopenrepeater/env`, set the AMI login (on ASL3, the `admin`
user and the secret from `/etc/asterisk/manager.conf`):

```sh
MOREOPENREPEATER_AMI_HOST=127.0.0.1
MOREOPENREPEATER_AMI_USER=admin
MOREOPENREPEATER_AMI_SECRET=the-secret-from-manager.conf
```

and restart the service. ASL3's `admin` login can do everything autopatch
needs. A login of your own needs at least:

```ini
[moreopenrepeater]
secret = a-long-random-secret
read = call,config,system
write = call,config,system,originate
```

### 2. Turn on SIP

Open the **Autopatch** page. On ASL3, the **Phone line** card says SIP is
turned off, because ASL3 ships with Asterisk's SIP (PJSIP) and RTP modules
not loaded. **Turn on SIP in Asterisk** loads them, along with AudioSocket,
and adds them to `modules.conf` so they load whenever Asterisk starts.

### 3. Set up the phone line

Sign up with a SIP trunk provider, then pick it under **Provider** and fill
in the details from your account:

| Provider | Server | Username | Number format | Register |
| --- | --- | --- | --- | --- |
| VoIP.ms | the server (point of presence) your account uses, e.g. `atlanta.voip.ms` | 6-digit account ID, or a sub-account like `123456_repeater` | 1 + 10 digits | on |
| Telnyx | `sip.telnyx.com` | a Credentials connection's username | +1 + 10 digits | on |
| Twilio Elastic SIP Trunking | the trunk's termination URI, `yourtrunk.pstn.twilio.com` | a user from the trunk's credential list | +1 + 10 digits | off |

For another provider, choose **Other** and use its Asterisk (PJSIP) guide.
**Number format** only changes 10-digit numbers (and 11-digit 1+ numbers);
911 and other short codes always go out as dialed.

**Save phone line** writes it to Asterisk and points autopatch at it (the
dial string becomes `PJSIP/{number}@mor-trunk`). The card then shows
whether the provider accepted the registration:

- **Registered**: ready for calls.
- **Rejected**: the provider turned down the username or password. Fix them
  and save again.
- **Not registered**: no answer yet. Asterisk retries every minute; check the
  server name, and that the network allows outgoing SIP (UDP port 5060).

For a provider without registration (Twilio), the card shows whether
Asterisk can reach the server instead.

The line is kept in `/etc/asterisk/pjsip.conf`, in sections named
`mor-trunk`, `mor-trunk-auth`, `mor-trunk-aor` and `mor-trunk-reg`, plus a
`mor-transport-udp` (or `-tcp`) transport if the file had none. Other
sections in the file are left alone, but Asterisk removes the file's
comments when it saves it -- on ASL3, that's all of the stock file, which is
only commented-out samples. The SIP password is stored there and never shown
on the dashboard. ASL3 leaves the file readable by every user on the
machine; if others can log in, `sudo chmod 640 /etc/asterisk/pjsip.conf`
(Asterisk keeps that when it saves). Only admins can change the phone line,
and not during a call.

### 4. Turn autopatch on

In the settings below the phone line, set a **Caller ID** your provider
allows (usually a number on your account; Twilio wants `+1` and 10 digits),
turn on **Allow autopatch calls**, and save. Use **Test call** to try it.

### Asterisk on another machine

Everything above works over the network, except that Asterisk has to reach
the controller for the call audio. Set
`MOREOPENREPEATER_AUDIOSOCKET_LISTEN=0.0.0.0:9092` and
`MOREOPENREPEATER_AUDIOSOCKET_ADDRESS=<controller's address>:9092`, and
firewall port 9092 to only that Asterisk. The audio isn't encrypted or
authenticated beyond an unguessable per-call ID.

### Calls connect but there's no audio

That's usually the router between Asterisk and the provider. The phone line
already uses the common settings for Asterisk behind NAT. If audio is still
one-way or missing, add your public address and local network to the
transport section in `pjsip.conf` (the controller never rewrites a transport
once it exists):

```ini
[mor-transport-udp]
type = transport
protocol = udp
bind = 0.0.0.0
external_media_address = 203.0.113.10
external_signaling_address = 203.0.113.10
local_net = 192.168.1.0/24
```

and restart Asterisk. `asterisk -rx "pjsip set logger on"` shows the SIP
messages.

### Using a trunk set up by hand

To use a PJSIP endpoint you configured yourself, set the **Dial string** to
`PJSIP/{number}@<your endpoint>` and leave the phone line empty. The modules
still need to be on (step 2).

## Which numbers can be dialed

**Allowed numbers** and **Blocked numbers** are patterns in Asterisk's
dialplan notation: `X` is any digit, `N` is 2-9, `Z` is 1-9, and digits match
themselves. A number has to match an allowed pattern and no blocked one.

The default allows `911` and ten-digit numbers (`NXXNXXXXXX`), which rules out
1+ long distance, international calls and short codes like 411. It blocks
900 numbers and 976 exchanges. To allow seven-digit local dialing, add
`NXXXXXX`; for 1+ calls, add `1NXXNXXXXXX`.

**About 911:** calls reach the dispatch center your provider has on file for
your account, not the repeater's location. Register the repeater site's
address as your E911 address with the provider, or remove `911` from the
allowed list.

## Operating notes

- US amateur rules (Part 97) prohibit business communications over the air,
  including on autopatch. Many clubs limit autopatch to members or to
  emergencies; the access code is the usual gate.
- Calls, refusals and outcomes are in the audit log, with the number dialed.
- Turning the transmitter off (over DTMF or the dashboard) or turning
  autopatch off hangs up any call in progress.

## Testing without a trunk

Any Asterisk channel works as the dial string, so a test context stands in
for the phone network. In `/etc/asterisk/extensions.conf` (or ASL3's
`custom/extensions.conf`):

```ini
[patchtest]
exten => 5550000,1,Busy(3)
exten => _X.,1,Answer()
 same => n,Playback(hello-world)
 same => n,Echo()
```

Load `app_echo.so` if needed, set the dial string to
`Local/{number}@patchtest`, add `NXXXXXX` to the allowed numbers, and call
555-1234: you hear "hello world", then your own transmissions echoed back.
555-0000 is busy. Saving a phone line sets the dial string back to the trunk.

`spikes/autopatch_asl3_spike.py` runs the same checks from a script, and
`spikes/sip_trunk_asl3_spike.py` sets up a fake SIP provider in the same
Asterisk, so the phone line, registration and calls can be tried over real
SIP.
