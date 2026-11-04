# moreopenrepeater

Domain language for the repeater controller: on-air behavior, linking, and the
operator dashboard. Use these terms in issues, ADRs, and code discussion.

## Language

### Controller core

**RepeaterController**:
The in-process state machine that turns radio and link events into transmit
commands (idle → receiving → courtesy tone → hang time → ID / timeout).
_Avoid_: state machine alone, engine, app_rpt

**RepeaterConfig**:
The flat settings object the controller and API share (hang time, TOT, ID,
tones, linking, mailbox, nets, and the rest). Loaded and saved via
`GET`/`PUT /api/config`.
_Avoid_: settings blob, options, preferences

**RepeaterService**:
The FastAPI-facing facade that owns a `RepeaterController`, applies its
commands, and wires live audio, AllStar, autopatch, and related hooks.
_Avoid_: app runtime, backend controller

### Radio signalling

**COS**:
Carrier Operated Squelch — the receiver's "someone is transmitting" line or
software detect (VOX / CTCSS). Opens the repeater into receiving.
_Avoid_: squelch alone, carrier detect as a synonym in new prose (say COS)

**PTT**:
Push-to-Talk — the line or command that keys the transmitter. The controller
asserts PTT while repeating, IDing, or holding for hang time / a patch.
_Avoid_: TX key, key-up (except when describing the operator action)

**CTCSS**:
Continuous Tone-Coded Squelch System — a sub-audible access tone decoded or
required on the input. Distinct from DTMF.
_Avoid_: PL tone, subtone, CTCSS/DCS as one thing

### Timing and ID

**Courtesy tone**:
The short tone or clip after a user (or link / patch) unkeys, before hang
time. Variants can differ for local, linked, and phone-patch traffic.
_Avoid_: roger beep, tail beep

**Hang time**:
How long the transmitter stays keyed after the courtesy tone so the next
station can reply without a full drop.
_Avoid_: tail timer, hangtimer

**TOT**:
Time-out timer — maximum continuous transmit time (`tot_duration`) before the
controller drops PTT and plays the timeout tone.
_Avoid_: timeout alone when meaning this limit; TX limit

**ID**:
Station identification — voice and/or CW of the callsign on the configured
interval (and long-ID variants). Not a user account.
_Avoid_: ident, beacon (unless idle_id beaconing is meant)

### Linking and phone

**AllStar**:
AllStarLink via a local Asterisk/`app_rpt` sidecar: node linking over IAX2,
driven by AMI and USRP audio. This project is not a wrapper around `app_rpt`
as the repeater logic.
_Avoid_: Asterisk linking, ASL node as the controller itself

**Autopatch**:
Phone patch over SIP: on-air dial-out (and optional dial-in) through the
Asterisk sidecar. Holds PTT for the call.
_Avoid_: phone patch alone in new prose; PBX feature

**Link radio**:
A second radio interface for RF linking or a remote base (its own audio
devices and PTT/COS), separate from the AllStar IP link path.
_Avoid_: secondary radio, remote radio (say remote base when that mode is meant)

### Operator features

**Mailbox**:
On-air voice mailboxes: leave / play / delete short messages by DTMF, with
dashboard listen-back.
_Avoid_: voicemail (phone), inbox

**Net control**:
The station running a net while **net mode** is active (longer TOT, net
courtesy tone, held announcements/patch, check-in log).
_Avoid_: net operator, NCS in code/docs unless quoting operators

**Site sensor**:
A named analog reading at the repeater site (temperature, battery voltage,
fuel, supply) from a DS18B20 1-wire probe or an ADS1115 I2C ADC channel,
with scale/offset, high/low thresholds (hysteresis), dashboard display, and
optional phone / on-air alerts. Configured on the `sensors` facet of
`RepeaterConfig` (`site_sensors`).
_Avoid_: SiteStatus (OpenRepeater module name); GPIO input for analog values

### Dashboard cutover

**Mantine dashboard**:
The React + Mantine operator UI whose source lives in `frontend/` (Vite +
TypeScript). Built assets are written to `web/dist/` and served by FastAPI.
_Avoid_: plain HTML dashboard, SPA without naming Mantine when meaning this UI

**web/dist cutover**:
Serving prefers `web/dist/` when both `web/dist/index.html` and the marker
`web/dist/.ready` exist (ADR 0001). Developers and CI run `npm run build`;
the Pi never runs `npm`. Removing `.ready` falls back to the vanilla files
under `web/` until that fallback is deleted.
_Avoid_: no-build frontend, dashboard in `web/` as the editable source

### Multi-site

**Hub**:
A moreopenrepeater instance that lists other remotes on the **Sites** view by
polling each site's API with a per-site token (`data/hub-sites.json`). Remotes
do not call home.
_Avoid_: multi-site controller, central command server

**Site** (hub):
One configured remote repeater entry (name, base URL, API token) shown on Sites.
_Avoid_: node (AllStar), link (unless meaning AllStar/RF link)
