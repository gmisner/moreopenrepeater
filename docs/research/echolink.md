# EchoLink linking: protocol research

Research date: 2026-09-28. Question: what would it take for moreopenrepeater's
Python controller to link to the EchoLink network, and what's the recommended
approach?

Two independent open-source implementations are the load-bearing sources:
SvxLink's `echolib` (C++, GPLv2+) at commit
[`fcd1301`](https://github.com/sm0svx/svxlink/tree/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib),
and AllStarLink's `chan_echolink.c` (C, GPLv2) at commit
[`fa57a08`](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c).
Where both agree, the claim below is treated as verified. Where they disagree,
both behaviors are listed. EchoLink (Synergenics, LLC) publishes **no protocol
specification**. The official site documents ports, policies and
operations only, so every wire-format claim below comes from these two
codebases, not from EchoLink itself.

Link shorthand used below:

- `SV/` = `https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/`
- `AR/` = `https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/`

## TL;DR recommendation

**Enable `chan_echolink` in the Asterisk + app_rpt sidecar the project already
runs for AllStar; don't write a native Python EchoLink stack yet.** In app_rpt,
an EchoLink station is just another link, named `3` + the zero-padded 6-digit
EchoLink node number (e.g. `3009999`) ([ASL3 manual](https://github.com/AllStarLink/ASL3-Manual/blob/6b2898c50f121cb05a668743c8ac83e97a7c3d15/docs/adv-topics/echolink.md);
[AR/channels/chan_echolink.c `el_new`, L2618](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L2618)).
That should let the existing `NodeLinkClient` (which reads `RPT_ALINKS` and
sends `rpt fun`) handle EchoLink connects, disconnects and keying with little
or no code change. It's mostly configuration: `modules.conf`, `echolink.conf`,
forwarding UDP 5198–5199, and getting a validated `-R` callsign. The catch is
that EchoLink *audio* would then travel the same app_rpt audio path as
AllStar, so it's stuck behind the same open `rxchannel=audiosocket` problem.
A native Python client is clearly doable (the protocol is small: a
line-oriented TCP login/list exchange, RTCP-style SDES/BYE packets, and RTP
carrying 4 GSM 06.10 frames every 80 ms). My rough estimate is 3–5 weeks. It's
worth building only if EchoLink has to work without Asterisk, or if the
audiosocket blocker never gets fixed. Don't use SvxLink as a sidecar: it's a
second full repeater controller, which duplicates this project's whole
purpose.

## 1. Network architecture: directory ("addressing") servers

**Servers and port.** Clients reach the directory servers over **TCP port 5200**
([SV/echolib/EchoLinkDirectoryCon.h L215 `DIRECTORY_SERVER_PORT = 5200`](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectoryCon.h#L215);
[AR/channels/chan_echolink.c L227 `EL_DIRECTORY_PORT 5200`](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L227);
[echolink.org firewall_solutions](https://www.echolink.org/firewall_solutions.htm)).
SvxLink's default config uses `SERVERS=servers.echolink.org`, and its man page
says hostnames that resolve to several addresses are tried in turn
([SV/svxlink/modules/echolink/ModuleEchoLink.conf.in](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.conf.in);
[SV/doc/man/ModuleEchoLink.conf.5 `SERVERS`](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/doc/man/ModuleEchoLink.conf.5)).
ASL's sample config lists `nasouth`, `naeast`, `servers` and `backup` `.echolink.org`
([AR/configs/rpt/echolink.conf L35-38](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/configs/rpt/echolink.conf#L35-L38)).
All four names resolved on 2026-09-28, and `servers.echolink.org:5200`
accepted a TCP connection. EchoLink says about 4 servers are online at a time,
replicating every 20 s, on Oracle/AWS cloud hosts. The servers only provide the
station list and are "not involved" in QSO audio "except for certain signals
used to assist with firewall pass-through"
([echolink.org faq_servers](https://www.echolink.org/faq_servers.htm)). Those
firewall-assist signals aren't documented anywhere, and neither open-source
implementation sends them (**unverified**).

**Login/registration.** Each command opens a fresh TCP connection, sends one
request, reads the reply, and closes
([SV/echolib/EchoLinkDirectory.cpp `ctrlSockConnected` L677-729, `ctrlSockDataReceived` L732-818](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.cpp#L677-L818)).
The login string is:

```
'l' CALLSIGN 0xAC 0xAC PASSWORD '\r' STATUS_AND_VERSION '\r' DESCRIPTION '\r'  [ EMAIL '\r' ]
```

- SvxLink sends `ONLINE3.38(HH:MM)`, `BUSY3.40(HH:MM)` or `OFF-V3.40` as the
  status/version field, uppercases the callsign, and truncates the description
  to 27 characters
  ([EchoLinkDirectory.cpp L686-722](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.cpp#L686-L722);
  [EchoLinkDirectory.h L133 `MAX_DESCRIPTION_SIZE = 27`](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.h#L133)).
- chan_echolink sends `ONLINE1.00R(H:DD)` (or `1.00B` for a `*CONF*` call) and
  adds an email field
  ([AR/channels/chan_echolink.c `sendcmd` L2972-3059, format at L3028](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L2972-L3059)).
- Both treat a reply starting with `OK` as success
  ([SV L745-783](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.cpp#L745-L783);
  [AR L3053](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3053)).
  chan_echolink's header comment says `ok`, but its code checks `OK`.
- The server accepts two quite different version strings, so it seems lenient
  about that field. Which version strings or fields it actually requires is
  **unverified**.
- **Plaintext password:** these third-party clients send the password in
  cleartext over TCP. EchoLink's own proxy FAQ says the official client (1.8
  and later) "uses public-key cryptography to encrypt your login information"
  ([echolink.org proxy](https://www.echolink.org/proxy.htm)). The encrypted
  login is undocumented. The servers still accept the plaintext form (SvxLink
  and ASL3 are listed as compatible software and work today), but whether
  EchoLink will keep accepting it is **unverified**.

**Re-login interval.** SvxLink re-sends its status every 5 minutes
([EchoLinkDirectory.h L373 `REGISTRATION_REFRESH_TIME = 5*60*1000`](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.h#L373);
`onRefreshRegistration` L967-980). chan_echolink re-registers every 360 s,
retries every 20 s after a failure, and notes that "Echolink deactivates this
node within 6 minutes" if registration stops
([AR `el_register` L3517-3567](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3517-L3567)).
Re-registering every ≤ 5 min is safe. The exact server-side expiry is
**unverified** beyond that code comment. Going offline is done by explicitly
sending `OFF-V…`; chan_echolink never does this and just lets the entry
expire.

**Callsign suffixes.** `-L` means a simplex link and `-R` a repeater link.
Conference servers use `*NAME*`. SvxLink sorts the station list into links,
repeaters, conferences and stations using exactly those rules
([EchoLinkDirectory.cpp L617-634](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.cpp#L617-L634)).
EchoLink policy says an RF gateway **must** use `-L` or `-R` (`-R` for a
repeater), and each suffix is validated separately
([access_policies #10](https://www.echolink.org/access_policies.htm);
[faq_validation #12](https://www.echolink.org/faq_validation.htm);
[sysop_nodes](https://www.echolink.org/sysop_nodes.htm)). For this project the
right form is `<CALL>-R`.

**Bad password.** The server doesn't send an error code. It returns a station
list whose message block starts with `INCORRECT PASSWORD`
([EchoLinkDirectory.cpp L639-643](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.cpp#L639-L643)).

**Fetching the station list.** There are two variants:

- **SvxLink** sends `s` after it's registered. The reply is `@@@\n`, then a
  count line, then 4 lines per entry (`callsign`, `description [ON|BUSY HH:MM]`,
  `node id`, `ip`), terminated by `+++`. Entries whose callsign is a single space
  carry server message text
  ([`handleCallList` L465-665](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDirectory.cpp#L465-L665);
  status parsing in [SV/echolib/EchoLinkStationData.cpp `setData` L184-227](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkStationData.cpp#L184-L227)).
  SvxLink refreshes every 10 min, and immediately when an unknown station
  calls in
  ([SV/svxlink/modules/echolink/ModuleEchoLink.cpp `getDirectoryList` L1358-1373](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.cpp#L1358-L1373)).
- **chan_echolink** sends `F<snapshot_id>\r`. The reply may be zlib-compressed
  and may be either a full list (`@@@`) or a differential one (`DDD`, whose
  count line is `lines:snapshot_id`)
  ([AR header comment L118-164](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L118-L164);
  [`do_el_directory` L3210-3440, request at L3262](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3210-L3440)).
  It re-fetches every 240 s after a compressed reply, every 1800 s after an
  uncompressed one, and retries every 20 s on failure
  ([`el_directory` L3459-3508](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3459-L3508)).

**Why the station list is mandatory.** It isn't optional UI. It's how EchoLink
authenticates stations. Incoming connections carry no credential: SvxLink
accepts a caller only if its SDES callsign is in the downloaded list *and*
the source IP matches the listed IP
([ModuleEchoLink.cpp `onIncomingConnection` L978-1038](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.cpp#L978-L1038)).
chan_echolink looks the source IP up in its directory database and rejects
unknown callers
([AR `do_new_call` L3581-3605](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3581-L3605)).
So a minimal node still has to download and cache the list.

## 2. Station-to-station protocol

**Ports.** Audio, chat and info travel on **UDP 5198**. Connection control
(SDES/BYE) travels on **UDP 5199**. Each port is used as both source and
destination
([SV/echolib/EchoLinkDispatcher.cpp L86-87, L216-241, L283-284](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDispatcher.cpp#L86-L87);
[AR header comment L76-80](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L76-L80)).
Sessions are keyed **by remote IP alone**, so there can be only one QSO per
peer IP
([EchoLinkDispatcher.cpp `registerConnection` L188-204](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDispatcher.cpp#L188-L204)).
That matches EchoLink's "one login per IP address" rule
([access_policies #11](https://www.echolink.org/access_policies.htm)).

**The RTP/RTCP dialect isn't standard.** The packets look like
[RFC 3550](https://www.rfc-editor.org/rfc/rfc3550) RTCP (RR=201, SDES=202,
BYE=203, and SDES items CNAME=1, NAME=2, EMAIL=3, PHONE=4, PRIV=8), but the
**version field is 3, not 2**
([SV/echolib/rtp.h L13 `RTP_VERSION 3`](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/rtp.h#L13);
[AR `rtcp_make_sdes` L1001-1002](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L983-L1057)).
Both parsers also accept version 1
([SV/echolib/rtpacket.cpp `isRTCPByepacket` L217-244](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/rtpacket.cpp#L217-L244);
[AR `is_rtcp_bye` L1254-1280](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L1254-L1280)).
SvxLink's `rtp.h` says it follows the "RTP draft: November 1994 version". Its
comments about a "Look Who's Listening server" suggest the code descends from
Speak Freely, but that's an inference (**unverified**). Standard RTP
libraries would reject these packets, so the implementation has to be
hand-rolled (it's small).

**Connect (SDES).** A connect request is a composite packet: an empty RR (8
bytes, count=1, SSRC 0) followed by an SDES chunk with SSRC 0, containing:

| Item | SvxLink value | chan_echolink value |
|---|---|---|
| CNAME (1) | `CALLSIGN` (literal) | `CALLSIGN` (literal) |
| NAME (2) | `%-15s%s` callsign, name | `"%s %s"` callsign, name |
| EMAIL (3) | `CALLSIGN` (literal) | omitted |
| PHONE (4) | `08:30` (literal) | omitted |
| TOOL (6) | omitted | `AllStar <node>` |
| PRIV (8) | `SPEEX` if built with Speex | `\x01D1` ("enable DTMF keypad" per comment) |

Sources: [SV/echolib/rtpacket.cpp `rtp_make_sdes` L15-85](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/rtpacket.cpp#L15-L85);
[AR `rtcp_make_sdes` L983-1057](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L983-L1057).
The receiver reads only the NAME item and splits it at the first whitespace
into callsign and name
([SV/echolib/EchoLinkQso.cpp `handleSdesPacket` L626-683](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L626-L683);
[EchoLinkDispatcher.cpp L321-362](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkDispatcher.cpp#L321-L362)).
The rest of the items are effectively fixed filler.

**Session lifecycle.**

- The caller sends SDES and re-sends it every 10 s. Receiving any SDES moves a
  connecting session to connected.
- A connected session treats each SDES as a keep-alive and drops after 50 s
  without one.
- An outbound connect gives up after 5 unanswered SDES
  ([SV/echolib/EchoLinkQso.h L471-473](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.h#L471-L473);
  [EchoLinkQso.cpp `sendKeepAlive` L888-899, `setupConnection` L922-937](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L888-L937)).
- chan_echolink also uses a 10 s heartbeat and drops after `rtcptimeout`
  (default 10) missed heartbeats
  ([AR L193 `KEEPALIVE_TIME`](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L193);
  [echolink.conf L29](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/configs/rpt/echolink.conf#L29)).

**Disconnect (BYE).** A BYE is an empty RR plus a BYE with SSRC 0 and a reason
string. SvxLink sends `jan2002`; chan_echolink sends `bye`, `disconnected`,
`rtcp timeout` or `UN-AUTHORIZED`
([rtpacket.cpp `rtp_make_bye` L89-138](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/rtpacket.cpp#L89-L138);
[AR `rtcp_make_bye` L1135-1186](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L1135-L1186)).
To reject a caller, chan_echolink sends that BYE 20 times in a row
([AR L4060-4068](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L4060-L4068)).
A station that receives SDES from a peer it has no session with answers with
BYE ([EchoLinkQso.cpp L671-673](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L671-L673)).

**Audio.** Each packet is a 12-byte RTP-like header followed by 4 GSM 06.10
full-rate frames:

- Header: first byte `0xC0` (V=3), PT=`0x03` (GSM), sequence number
  incrementing, timestamp 0, SSRC 0 (SvxLink) or the sender's node number
  (chan_echolink).
- Payload: 4 frames × 33 bytes = 132 bytes, i.e. 640 samples at 8 kHz, so
  **80 ms per packet**.
- Sources: [EchoLinkQso.h L149-158, L477-479](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.h#L149-L158);
  [EchoLinkQso.cpp `sendVoicePacket` L958-1011](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L958-L1011);
  [AR L196-198 `BLOCKING_FACTOR 4`, `GSM_FRAME_SIZE 33`, L1863-1871](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L1863-L1871).
- PT 3, 33 bytes per 160-sample (20 ms) frame, and the 4-bit `0xD` frame
  signature all match [RFC 3551 §4.5.8 and Table 4](https://www.rfc-editor.org/rfc/rfc3551#section-4.5.8).
- The official client credits libgsm's authors (Degener and Bormann, TU
  Berlin) for its audio compression
  ([echolink.org help credits](https://www.echolink.org/help_ex/credits.htm)).
- Bandwidth works out to 14.4 kbit/s of UDP payload, about 17.2 kbit/s
  including IPv4 and UDP headers. That matches EchoLink's "divide by 18 [kbps]"
  per-connection sizing rule ([proxy FAQ](https://www.echolink.org/proxy.htm)).
- A packet whose first byte isn't `0xC0` is treated as text
  ([EchoLinkQso.cpp `handleAudioInput` L686-703](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L686-L703)).
- There's no explicit PTT signal. "Remote keyed" is inferred from audio
  arriving, and SvxLink ends it after roughly 100 ms–1 s of silence
  ([EchoLinkQso.h L474-476](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.h#L474-L476);
  `checkRxActivity` L1014-1027).
- SvxLink-to-SvxLink links can switch to Speex (PT `0x96`) when both sides
  advertise `SPEEX` in PRIV
  ([EchoLinkQso.cpp `setRemoteParams` L447-458](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L447-L458)).
  That's a private extension. GSM is the only codec that works with every
  peer.

**Text packets (UDP 5198).** Both kinds start with `oNDATA` (first byte
`0x6F`) and are NUL-terminated:

- **Info** (station description or conference status): `oNDATA\r<text with \r
  line breaks>\0`. SvxLink sends one automatically when a session reaches
  connected
  ([`sendInfoData` L341-370, `setState` L902-913, parse L706-734](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L341-L370)).
- **Chat**: `oNDATA<CALL>><message>\r\n\0`
  ([`sendChatData` L373-391](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L373-L391);
  [AR L2200](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L2200)).

**Connected-stations list.** There's no structured message for this. The
connected-stations display is free text inside the info packet. SvxLink
rebuilds it whenever the talker changes, in the official client's layout: a
header line `<app> - <CALL> (<n>)`, a `> <talker>` line, then one line per
station. A captured official-client example is in
[SV/svxlink/modules/echolink/conf_info_msg.txt](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/conf_info_msg.txt)
and the builder is
[ModuleEchoLink.cpp `broadcastTalkerStatus` L1522-1571](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.cpp#L1522-L1571).
chan_echolink instead sends a welcome message plus a `Systems Linked:` list
([AR `send_info` L2083-2115](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L2083-L2115)).
Nodes also advertise their connection count in the directory description,
e.g. SvxLink's `" (n)"` suffix and chan_echolink's `"[n/max]"`
([ModuleEchoLink.cpp `updateDescription` L1574-1593](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.cpp#L1574-L1593);
[AR L3788](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3788)).

**Multi-station (conference) behavior.** A node hosting several stations sends
separate unicast audio to each one. There's no multicast or server fan-out:
SvxLink caps this with `MAX_QSOS`/`MAX_CONNECTIONS` and chan_echolink with
`maxstns`
([ModuleEchoLink.conf.in](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.conf.in);
[echolink.conf L27](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/configs/rpt/echolink.conf#L27)).

**Optional APRS/status feed.** chan_echolink also sends a standard V=2 SDES
with an APRS-style position string to `aprs.echolink.org:5199`, which feeds
EchoLink's status pages
([AR L218, `rtcp_make_el_sdes` L1070-1127, L3834-3846](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L3834-L3846)).
It isn't required for linking.

## 3. NAT / firewall, proxy and relay

- **Required network access:** inbound and outbound UDP 5198–5199 (forwarded to
  the node), and outbound TCP 5200. TCP 5200 must **not** be forwarded inbound
  ([echolink.org firewall_solutions](https://www.echolink.org/firewall_solutions.htm);
  [help firewalls](https://www.echolink.org/help_ex/firewalls.htm)). Without
  forwarding, the node can make some outbound connections but not reliably
  accept incoming ones
  ([firewall-friendly](https://www.echolink.org/firewall-friendly.htm)). For a
  repeater `-R` node that defeats the purpose, and EchoLink warns that
  unreachable nodes "may lead to … being de-listed"
  ([isps](https://www.echolink.org/isps.htm)).
- **One public IP per logged-in node.** You can't run two nodes behind one NAT,
  e.g. the repeater node plus someone's phone app on the same home network
  ([access_policies #11](https://www.echolink.org/access_policies.htm);
  [firewall-friendly](https://www.echolink.org/firewall-friendly.htm)). The
  ASL3 manual calls this out as a common cause of EchoLink failures
  ([ASL3 manual "Connectivity Issues"](https://github.com/AllStarLink/ASL3-Manual/blob/6b2898c50f121cb05a668743c8ac83e97a7c3d15/docs/adv-topics/echolink.md)).
- **CGNAT** (Starlink, most cellular): EchoLink recommends a VPN that supports
  port forwarding on a dedicated address, or a "public IP" upgrade from the
  ISP. It says public proxies are for temporary use, not 24/7 nodes
  ([isps](https://www.echolink.org/isps.htm)). Since repeater sites often have
  cellular backhaul, this matters for this project.
- **EchoLink Proxy:** a Java program that tunnels all three channels over one
  TCP connection (default port 8100). One client per proxy, and the proxy host
  itself needs the normal port forwarding
  ([proxy](https://www.echolink.org/proxy.htm)). SvxLink implements the client
  side:
  - Message framing: 1-byte type, 4-byte IPv4 address, 4-byte little-endian
    length, then data. Types are `TCP_OPEN`, `TCP_DATA`, `TCP_CLOSE`,
    `TCP_STATUS`, `UDP_DATA`, `UDP_CONTROL` and `SYSTEM`.
  - Authentication: `CALLSIGN\n` followed by MD5(password + 8-byte nonce).
  - Public proxies use the password `PUBLIC`.
  - Sources: [SV/echolib/EchoLinkProxy.h L110-120, L329-336](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkProxy.h#L110-L120);
    [EchoLinkProxy.cpp L136-146, `sendMsgBlock` L256-292, auth L384-426](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkProxy.cpp#L384-L426).
  - chan_echolink has **no** proxy support (grep finds no proxy code). With the
    sidecar approach, the node needs real UDP reachability or a VPN.
- **Relay servers:** these are only for the iOS/Android apps. They can make
  outbound connections but can't accept incoming ones ("No route available")
  ([faq_android](https://www.echolink.org/faq_android.htm);
  [faq_connecting](https://www.echolink.org/faq_connecting.htm)). chan_echolink
  notes that relay users share one IP and get relay-generated node numbers
  ([AR header L158-164](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L158-L164)).
  The relay protocol isn't documented anywhere, and relays aren't an option
  for a repeater node.

## 4. Licensing and policy constraints

- **Callsign validation.** Every callsign variant, including `-L` and `-R`,
  needs proof of license through EchoLink's validation process before it can
  connect to anyone. First you register by logging in once with the software.
  Club or repeater calls are validated for sysop (`-L`/`-R`) mode only
  ([authentication](https://www.echolink.org/authentication.htm);
  [validation](https://www.echolink.org/validation/);
  [faq_validation #12](https://www.echolink.org/faq_validation.htm)). This is
  a human, out-of-band step that no implementation choice avoids, so it's on
  the critical path for any spike. The pages give no turnaround time; the FAQ
  says they process about 100 requests a day (**turnaround unverified**).
- **Policy points that affect this project** ([access_policies](https://www.echolink.org/access_policies.htm)):
  - #3: sysop validation only for license classes allowed to run a gateway.
  - #4: RF interconnection on amateur frequencies only, **and** "it is also not
    permitted to interconnect EchoLink with other VoIP systems that support
    direct access from a computer".
  - #10: no "headless" nodes without a radio.
  - #11: one IP per login.
  - #12: one login per callsign at a time.
  - #15: access can be withdrawn at will.

  #4 conflicts with this project's architecture. AllStarLink allows direct
  computer access, and a node bridging AllStar and EchoLink is exactly what
  ASL3 ships and what EchoLink's own [sysop_nodes](https://www.echolink.org/sysop_nodes.htm)
  and [download](https://www.echolink.org/download.htm) pages describe
  ("communicate with stations on the EchoLink network, in addition to the
  AllStarLink network"). How #4 is actually enforced against AllStar bridges
  is **unverified**. The sysop should ask EchoLink Support before running both
  links cross-connected, or should keep EchoLink and AllStar from being linked
  to each other at the same time.
- **Third-party clients.** EchoLink's download page lists SvxLink, the
  Asterisk/app_rpt channel driver, EchoIRLP and EchoHam as "Compatible
  Software … not officially EchoLink apps … we do not support these programs"
  ([download](https://www.echolink.org/download.htm)). That's tolerance, not a
  license grant. I found no published EchoLink EULA or terms page on the site
  (`license.htm`, `eula.htm` and `terms.htm` all return 404). Whether the
  Windows installer's EULA prohibits reverse engineering, and whether it would
  bind a clean-room implementer, is **unverified**. EchoLink® is a registered
  trademark of Synergenics, LLC ([help credits](https://www.echolink.org/help_ex/credits.htm)),
  so a native client shouldn't use the name in a way that implies endorsement.
- **No protocol documentation.** EchoLink publishes none. The two open-source
  implementations are the de facto spec.
- **Open-source licenses:**
  - SvxLink is GPL, with exceptions: its `echolib/md5.{c,h}` is zlib-licensed
    ([SV/../COPYRIGHT](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/COPYRIGHT)),
    and echolib's file headers say "GPLv2 or later".
  - chan_echolink.c is GPLv2, as is the app_rpt repo
    ([AR/channels/chan_echolink.c L16-18](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/channels/chan_echolink.c#L16-L18);
    [AR/LICENSE.md](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/LICENSE.md)).
  - libgsm has a permissive notice-preservation license
    ([quut.com/berlin/gsm/COPYRIGHT](https://www.quut.com/berlin/gsm/COPYRIGHT)).
  - moreopenrepeater itself has no LICENSE file yet.
- **Porting versus reimplementing.** Per the FSF, translating GPL code into
  another language is a modification, and the result must carry the same GPL
  ([GPL FAQ #TranslateCode](https://www.gnu.org/licenses/gpl-faq.html#TranslateCode)).
  So a line-by-line Python port of echolib or chan_echolink makes that Python
  module GPL, and effectively the whole in-process program. Writing a new
  implementation from protocol *facts* (packet layouts, constants, sequences
  like those in this note), without copying code structure, avoids that.
  Running unmodified GPL Asterisk or SvxLink as a separate process that talks
  over sockets, as the AllStar sidecar already does, is generally separate
  programs rather than one combined work
  ([GPL FAQ #MereAggregation](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)).
  That's the FSF's view, not legal advice.

## 5. Python feasibility

**GSM 06.10 codec options.**

- **`audioop` never had GSM.** Its function list is ulaw/alaw/ADPCM/rms etc.
  ([Python 3.12 audioop docs](https://docs.python.org/3.12/library/audioop.html)),
  and it was removed in 3.13 anyway ([PEP 594](https://peps.python.org/pep-0594/)).
  The removal doesn't matter here.
- **No maintained PyPI package wraps libgsm.** Searching the PyPI simple index
  for "gsm" on 2026-09-28 turned up only SMS/modem and unrelated projects.
  `pyogg` (last release 2020) covers Ogg/Vorbis/Opus/FLAC only
  ([PyPI pyogg](https://pypi.org/project/pyogg/)).
- **`soundfile` works today.** Its wheels bundle libsndfile, which includes a
  GSM 06.10 codec ([PyPI soundfile](https://pypi.org/project/soundfile/),
  0.14.0 released 2026-06). I checked this locally with soundfile 0.14.0 and
  libsndfile 1.2.2: writing `format='RAW', subtype='GSM610'` turns 8000
  samples into exactly 1650 bytes (33 bytes per 160 samples), every frame
  starts with the `0xD` signature, and decoding round-trips. The catch is that
  soundfile's API is file-oriented. GSM encoder and decoder state carries
  across frames: SvxLink creates one `gsm` handle per QSO
  ([EchoLinkQso.cpp L172](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/EchoLinkQso.cpp#L172)).
  Opening a new virtual file for each 80 ms packet would reset that state.
  Whether that causes audible artifacts is **unverified**.
- **Recommended codec path: `ctypes`/`cffi` against the system `libgsm`.**
  Only four C functions are needed: `gsm_create`, `gsm_encode`, `gsm_decode`
  and `gsm_destroy` ([quut.com/gsm](https://www.quut.com/gsm/)). Debian
  bookworm, and so Raspberry Pi OS, packages it as `libgsm1` 1.0.22
  ([packages.debian.org](https://packages.debian.org/bookworm/libgsm1)).
  That's about 50 lines of Python with proper per-session state. A pure-Python
  or numpy GSM encoder is possible, but its per-frame CPU cost on a Pi hasn't
  been measured (**unverified**, not recommended).
- **PyAV (FFmpeg bindings):** could also work, but whether its wheels include a
  GSM *encoder* (FFmpeg's encoder is the optional `libgsm` wrapper) is
  **unverified**.

**What a minimal native client would need** (asyncio, fitting the existing
`link`/`RepeaterService` event-loop model):

1. A directory client: an async TCP request/response per command. It sends
   login/`ONLINE` every ≤ 5 min and `OFF-V` on shutdown, fetches the station
   list (`s` to start with, `F<snapshot>` with zlib and differential updates
   later), and caches callsign ↔ IP ↔ node id.
2. Two `asyncio.DatagramProtocol` sockets on 5198 and 5199, with a per-IP
   session table.
3. An SDES/BYE builder and parser for the V=3 dialect, and a session state
   machine: 10 s keep-alive, 50 s timeout, 5 connect retries, and inbound
   authorization against the cached directory (callsign present and IP
   matches).
4. RTP GSM packetization: 4 × 160-sample frames per packet, a sequence
   counter, a small jitter/reorder buffer, and "remote keyed" detection from
   audio activity, mapped to `controller.events` the same way
   `NodeLinkClient` does.
5. Info and chat packets: send a station description on connect, and
   rebuild/broadcast the connected-stations text when the talker changes.
6. For more than one simultaneous station: audio fan-out and mixing, plus
   ACLs (the permit/deny callsign regexes both implementations offer).
7. Safe parsing of untrusted datagrams. SvxLink's latest release fixed a
   stack overflow in `StationData::setData` and out-of-bounds reads in RTCP
   parsing ([SV/echolib/ChangeLog 1.3.7, 26 Jul 2026](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/ChangeLog)).
   Python avoids that class of bug, but the parser still needs bounds checks
   and fuzz tests.

**Rough effort** (my estimate from the scope above, not measured):

| Piece | Estimate |
|---|---|
| Directory client with tests | 2–3 days |
| SDES/BYE, sessions, UDP transport | 3–5 days |
| GSM via ctypes, packetization, jitter buffer | 3–4 days |
| Info/chat and multi-station fan-out | 3–5 days |
| Live interop against real nodes (e.g. the `*ECHOTEST*` echo server) and fixes | about 1 week |

That's **about 3–5 weeks** in total. The AllStar case was different: the
README judged reimplementing IAX2 plus app_rpt's undocumented extensions a
"multi-month project". EchoLink is much smaller, since there's no auth
handshake, no retransmission, and no trunking. The `chan_echolink` sidecar
path is closer to **1–3 days** of configuration plus a spike, not counting the
callsign validation wait.

## 6. Alternatives and recommendation

| Option | What it takes | Pros | Cons |
|---|---|---|---|
| **A. `chan_echolink` in the existing ASL3 sidecar** | Load `chan_echolink.so`, fill in `echolink.conf` (`call`, `pwd`, `node`, `astnode`, servers), forward UDP 5198–5199 ([ASL3 manual](https://github.com/AllStarLink/ASL3-Manual/blob/6b2898c50f121cb05a668743c8ac83e97a7c3d15/docs/adv-topics/echolink.md)) | Already shipped and packaged with ASL3 (`noload` by default, [AR/configs/rpt/modules.conf L52](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/configs/rpt/modules.conf#L52)). EchoLink stations appear as app_rpt links `3NNNNNN` ([AR/apps/app_rpt/app_rpt.h L1189](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/apps/app_rpt/app_rpt.h#L1189); [rpt_functions.c L109-118](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/apps/app_rpt/rpt_functions.c#L109-L118)), so the existing `NodeLinkClient`/`RPT_ALINKS`/`rpt fun` plumbing should cover control. No new process, no GPL code in-process. | EchoLink audio is stuck behind the same unresolved `rxchannel=audiosocket` issue as AllStar audio. The driver is thin: no chat, no proxy, no loop detection, no TX time limit, rudimentary ACLs ([ASL3 manual "Caveats"](https://github.com/AllStarLink/ASL3-Manual/blob/6b2898c50f121cb05a668743c8ac83e97a7c3d15/docs/adv-topics/echolink.md)). Plaintext password login. Requires Asterisk even for EchoLink-only deployments. |
| **B. Native Python client** | The section 5 scope, about 3–5 weeks | Audio goes straight into `audio_io`/`controller` as numpy, with no app_rpt audio path, so it isn't affected by the audiosocket bug. Works with no Asterisk (OpenRepeater's original EchoLink-only model). Full control over chat, info, ACLs and proxy support. Memory-safe parsing. | Undocumented protocol, and the plaintext login could be retired without notice. The project owns interop testing. It needs a libgsm dependency. Clean-room discipline is needed to stay non-GPL. |
| **C. SvxLink (ModuleEchoLink) as a second sidecar** | Run `svxlink` with a logic core whose only job is EchoLink, and route audio and control between it and Python | Most complete EchoLink feature set (proxy, Speex, ACLs, conference info) | SvxLink is itself a full repeater controller, so it duplicates this project's core. It would add a second sidecar with its own audio bridge problem. Its control interface is a PTY/DTMF, not AMI (`COMMAND_PTY` in [ModuleEchoLink.conf.in](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink/ModuleEchoLink.conf.in)). Debian's package is old (19.09.2 in bookworm, [packages.debian.org](https://packages.debian.org/bookworm/svxlink-server)). |
| **C′. `libecholib` through a small C++ shim** | SvxLink builds echolib as a shared library ([SV/echolib/CMakeLists.txt L37-67](https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib/CMakeLists.txt#L37-L67)). A tiny GPL helper process would expose it over a socket. | Reuses hardened protocol code without all of SvxLink | Still means writing and maintaining C++ plus a socket protocol, and it depends on SvxLink's `async` library. It's close to option B's effort without B's benefits. |

**Recommendation.** Do **A first**. It's cheap, it reuses verified plumbing,
and it gives moreopenrepeater EchoLink connect/disconnect/keying on the
dashboard and in DTMF macros (e.g. a macro sending `*3` + `3009999`) as soon
as a validated `-R` callsign exists. Revisit **B** only if one of these
happens: (1) the audiosocket `rxchannel` blocker stays unresolved, so no
link's audio can reach `audio_io` through app_rpt anyway; (2) you want
EchoLink-only deployments without Asterisk; or (3) chan_echolink's gaps
(no chat, no proxy for CGNAT sites, weak ACLs) turn out to matter. If B goes
ahead, build it clean-room from this note's protocol facts, with libgsm
through ctypes, and keep it behind the same `controller.events` seam as
`NodeLinkClient` so A and B can be swapped. Skip C and C′.

## Open questions / what a spike should verify

1. **Callsign.** Get `<CALL>-R` validated (turnaround unknown). Nothing
   network-side can be tested with real peers before that.
2. **Control-plane parity under option A.** Confirm that an EchoLink link
   shows up in `RPT_ALINKS` as `3NNNNNN` with keyed-state flips, like IAX2
   links. `__mklinklist` only skips names starting with `0`
   ([AR/apps/app_rpt/rpt_link.c L484-505](https://github.com/AllStarLink/app_rpt/blob/fa57a08ca4c65075a01343297157385b58606941/apps/app_rpt/rpt_link.c#L484-L505)),
   so it should. Also confirm that `rpt fun <node> *33009999` connects to
   `*ECHOTEST*` (node 9999) and that `NodeLinkClient`'s diffing handles the
   `3` prefix.
3. **Audio under option A.** Does EchoLink audio reach the node's
   `rxchannel`/AudioSocket path at all, or does it hit the same upstream
   app_rpt bug? Re-test on the current ASL3 release.
4. **Registration expiry.** Measure how long after the last `ONLINE` the node
   disappears from [logins.jsp](https://www.echolink.org/logins.jsp). The code
   comments disagree slightly (5 min versus "within 6 minutes").
5. **Directory protocol details for option B.** Is `s` (uncompressed, as
   SvxLink uses) still served, or only `F…`? What's the size of the full list?
   Can the list be fetched without being registered? Which login version
   strings does the server accept?
6. **Firewall-assist signals.** What are the undocumented "signals used to
   assist with firewall pass-through" ([faq_servers](https://www.echolink.org/faq_servers.htm))?
   Does a node without them lose reachability to some peers?
7. **Streaming GSM through `soundfile`.** If it's used instead of ctypes
   libgsm, is resetting codec state per packet audible? Easier to avoid by
   using libgsm directly.
8. **Policy #4.** Ask EchoLink Support in writing whether an `-R` node that
   is also an AllStarLink node, possibly cross-linked, is acceptable under
   "no interconnection with VoIP systems that support direct access from a
   computer".
9. **CGNAT deployments.** If a target repeater site is on cellular or
   Starlink, check whether a port-forwarding VPN is acceptable operationally.
   chan_echolink has no proxy support, so option A has no fallback there.

## Not verified from a primary source

- The content and purpose of the directory servers' "firewall pass-through"
  signals.
- The official client's encrypted login format, and whether plaintext login
  will keep being accepted.
- The exact server-side registration expiry. Only the code comment "within 6
  minutes" and the 5–6 min refresh intervals are known.
- Which login version strings and fields the server requires.
- Any EchoLink EULA or terms clause on reverse engineering or third-party
  clients. None is published on the website.
- How EchoLink applies access policy #4 to AllStarLink bridges.
- Validation turnaround time.
- Speak Freely heritage of the RTP code (inferred from code comments only).
- thelinkbox / thebridge / EchoIRLP sources. SourceForge (`cqinet`) blocked
  automated fetches and echoirlp.com timed out, so those implementations
  weren't consulted. `chan_tlb.c` only shows that app_rpt can hand off to
  thelinkbox.
- Whether PyAV wheels include a GSM encoder, and the CPU cost of a pure-Python
  GSM codec on a Pi.
- All effort estimates are judgment, not measurement.

## Sources

Primary code (pinned commits):

- SvxLink echolib, commit `fcd1301` (2026-07-26): `EchoLinkDirectory.{h,cpp}`,
  `EchoLinkDirectoryCon.{h,cpp}`, `EchoLinkDispatcher.{h,cpp}`,
  `EchoLinkQso.{h,cpp}`, `EchoLinkProxy.{h,cpp}`, `EchoLinkStationData.cpp`,
  `rtp.h`, `rtpacket.cpp`, `ChangeLog`, `CMakeLists.txt` —
  https://github.com/sm0svx/svxlink/tree/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/echolib
- SvxLink ModuleEchoLink (`ModuleEchoLink.cpp`, `ModuleEchoLink.conf.in`,
  `conf_info_msg.txt`) and man page `ModuleEchoLink.conf.5` —
  https://github.com/sm0svx/svxlink/tree/fcd13014810af75d1852b3f2cfc8d9806178d0ab/src/svxlink/modules/echolink
- SvxLink COPYRIGHT —
  https://github.com/sm0svx/svxlink/blob/fcd13014810af75d1852b3f2cfc8d9806178d0ab/COPYRIGHT
- AllStarLink app_rpt, commit `fa57a08` (2026-09-25): `channels/chan_echolink.c`,
  `configs/rpt/echolink.conf`, `configs/rpt/modules.conf`,
  `apps/app_rpt/app_rpt.h`, `apps/app_rpt/rpt_functions.c`,
  `apps/app_rpt/rpt_link.c`, `LICENSE.md` —
  https://github.com/AllStarLink/app_rpt/tree/fa57a08ca4c65075a01343297157385b58606941
- ASL3 Manual, EchoLink page, commit `6b2898c` —
  https://github.com/AllStarLink/ASL3-Manual/blob/6b2898c50f121cb05a668743c8ac83e97a7c3d15/docs/adv-topics/echolink.md

Official EchoLink pages (fetched 2026-09-28):

- Firewall Solutions — https://www.echolink.org/firewall_solutions.htm
- EchoLink, Firewalls, and Routers — https://www.echolink.org/firewall-friendly.htm
- Help: Firewall Issues — https://www.echolink.org/help_ex/firewalls.htm
- Internet Service Providers (CGNAT) — https://www.echolink.org/isps.htm
- EchoLink Proxy — https://www.echolink.org/proxy.htm
- FAQ Servers — https://www.echolink.org/faq_servers.htm
- FAQ Android / iPhone / Connecting (Relay) — https://www.echolink.org/faq_android.htm,
  https://www.echolink.org/faq_iphone.htm, https://www.echolink.org/faq_connecting.htm
- Access Policies — https://www.echolink.org/access_policies.htm
- Authentication — https://www.echolink.org/authentication.htm
- Validation — https://www.echolink.org/validation/
- FAQ Validation — https://www.echolink.org/faq_validation.htm
- Sysop (Link) Nodes — https://www.echolink.org/sysop_nodes.htm
- Download / Compatible Software — https://www.echolink.org/download.htm
- Help: Credits — https://www.echolink.org/help_ex/credits.htm

Standards, codec and licensing:

- RFC 3550 (RTP/RTCP) — https://www.rfc-editor.org/rfc/rfc3550
- RFC 3551 §4.5.8 (GSM payload) — https://www.rfc-editor.org/rfc/rfc3551#section-4.5.8
- libgsm (Degener and Bormann) and license — https://www.quut.com/gsm/,
  https://www.quut.com/berlin/gsm/COPYRIGHT
- Debian `libgsm1` / `svxlink-server` (bookworm) —
  https://packages.debian.org/bookworm/libgsm1, https://packages.debian.org/bookworm/svxlink-server
- Python `audioop` docs (3.12) and PEP 594 —
  https://docs.python.org/3.12/library/audioop.html, https://peps.python.org/pep-0594/
- PyPI `soundfile`, `pyogg` — https://pypi.org/project/soundfile/, https://pypi.org/project/pyogg/
- FSF GPL FAQ (#TranslateCode, #MereAggregation) — https://www.gnu.org/licenses/gpl-faq.html
