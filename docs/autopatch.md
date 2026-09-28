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
for AllStarLink. Any Asterisk with the AudioSocket modules works (tested
with ASL3's Asterisk 22); app_rpt isn't involved:

1. The controller asks Asterisk over AMI to call `PJSIP/<number>@<trunk>`.
2. When the far end answers, Asterisk runs `AudioSocket()`, which connects
   back to the controller (port 9092 by default) and carries the call audio.
3. The controller bridges that audio to the radio.

## Setup

### 1. A SIP trunk

Sign up with a SIP trunk provider (VoIP.ms, Twilio Elastic SIP Trunking,
Telnyx, and so on) and get the server name, username and password. Add the
trunk to `/etc/asterisk/pjsip.conf`. A registration-based example, named
`trunk`:

```ini
[trunk-reg]
type = registration
outbound_auth = trunk-auth
server_uri = sip:chicago.voip.ms
client_uri = sip:123456@chicago.voip.ms

[trunk-auth]
type = auth
auth_type = userpass
username = 123456
password = your-sip-password

[trunk]
type = aor
contact = sip:chicago.voip.ms

[trunk]
type = endpoint
context = from-trunk
disallow = all
allow = ulaw
outbound_auth = trunk-auth
aors = trunk
from_user = 123456
```

Your provider's own Asterisk guide has the exact values. The controller only
places outgoing calls, so `from-trunk` can be an empty context.

Reload with `asterisk -rx "pjsip reload"`, and check it registered with
`asterisk -rx "pjsip show registrations"`.

### 2. Asterisk modules

Load AudioSocket at startup by adding these to `/etc/asterisk/modules.conf`:

```ini
load = res_audiosocket.so
load = app_audiosocket.so
```

Then restart Asterisk, or load them now:

```sh
sudo asterisk -rx "module load res_audiosocket.so"
sudo asterisk -rx "module load app_audiosocket.so"
```

### 3. An AMI user

The controller needs an AMI login with `originate` and `call` permission,
in `/etc/asterisk/manager.conf` (the ASL3 `admin` user already has them):

```ini
[moreopenrepeater]
secret = a-long-random-secret
read = call,originate
write = call,originate,system
```

### 4. Point the controller at Asterisk

In `/etc/moreopenrepeater/env`:

```sh
MOREOPENREPEATER_AMI_HOST=127.0.0.1
MOREOPENREPEATER_AMI_USER=moreopenrepeater
MOREOPENREPEATER_AMI_SECRET=a-long-random-secret
```

Restart the service. The Autopatch page should no longer say Asterisk isn't
connected. If Asterisk runs somewhere else (another machine or a container),
also set `MOREOPENREPEATER_AUDIOSOCKET_LISTEN=0.0.0.0:9092` and
`MOREOPENREPEATER_AUDIOSOCKET_ADDRESS=<controller's address>:9092`, and
firewall port 9092 to only that Asterisk. The audio isn't encrypted or
authenticated beyond an unguessable per-call ID.

### 5. Dashboard settings

On the Autopatch page, set the **Dial string** to your trunk
(`PJSIP/{number}@trunk` for the example above; some providers want
`PJSIP/+1{number}@trunk`), set a **Caller ID** your provider allows, and turn
on **Allow autopatch calls**. Use **Test call** to try it.

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
555-0000 is busy. `spikes/autopatch_asl3_spike.py` runs the same checks from a
script.
