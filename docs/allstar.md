# AllStarLink audio

The controller can be the radio for an AllStarLink node. What the repeater
repeats goes out over the node's links, and whatever the node transmits (a
linked station talking, or the node's own announcements) keys the repeater
and goes out over the air, with the controller's own courtesy tone, hang
time and time-out timer.

## How it works

app_rpt runs in the Asterisk beside the controller (ASL3's), but only as the
network side. Its node's radio channel is app_rpt's USRP channel (the one
DVSwitch and SvxLink use), pointed at the controller:

1. While the controller is repeating a user, it sends that audio to the
   node as 20 ms UDP packets. The node treats the packets arriving as its
   receiver keying up and sends the audio over its links.
2. While the node transmits, it sends its audio back the same way, marked
   as keyed, and then a "key up" packet with the flag cleared when it stops. The controller keys
   up, transmits it, and plays its courtesy tone when the node unkeys.

A carrier that the controller isn't repeating, such as a kerchunk, a signal
without the required CTCSS tone, or a parrot recording, isn't sent. If the
local user and a linked station talk at once, the repeater stays up until
both have finished.

The AMI connection (`MOREOPENREPEATER_AMI_*`) is separate. It shows links
and remote key-ups on the dashboard and sends DTMF macros' link commands.
Set up both for a complete AllStar node.

## Setup

Tested with ASL3 (Asterisk 22.10.1, app_rpt 3.10.5).

### 1. Load the USRP channel

ASL3 turns it off in `/etc/asterisk/modules.conf`. Change

```ini
noload = chan_usrp.so
```

to

```ini
load = chan_usrp.so
```

### 2. Point the node at the controller

In `/etc/asterisk/rpt.conf`, in your node's stanza:

```ini
[1999](node-main)
rxchannel = USRP/127.0.0.1:34001:32001
duplex = 0
linktolink = yes
hangtime = 0
althangtime = 0
nounkeyct = 1
```

- `rxchannel = USRP/<controller host>:<controller port>:<node port>`. The
  controller listens on the first port and the node on the second.
- `duplex = 0` is app_rpt's mode for an external repeater controller. The
  stock `duplex = 2` repeats the node's receiver, so everything the
  controller sent would come straight back and go out twice.
- `linktolink = yes` lets the node send a local user over the links while
  a linked station is talking. Without it, `duplex = 0` is half duplex, and
  a user who keys up over a linked station isn't heard on the link.
- `hangtime = 0`, `althangtime = 0` and `nounkeyct = 1` leave the hang time
  and courtesy tone to the controller. Without them the node holds its
  transmitter for its own hang time (two seconds as shipped), which keeps
  the repeater up, and adds its courtesy tone to the controller's.

To keep the node's own voice IDs off the air too, comment out `idrecording`
and `idtalkover` in the stanza. The controller sends its own ID.

Then `sudo systemctl restart asterisk`.

### 3. Tell the controller where the node is

In `/etc/moreopenrepeater/env`:

```sh
MOREOPENREPEATER_USRP_NODE=127.0.0.1:32001
#MOREOPENREPEATER_USRP_LISTEN=127.0.0.1:34001
```

`USRP_NODE` is the node's host and the last port in `rxchannel`. Setting it
turns this on. `USRP_LISTEN` is where the controller listens, which is the
first port (34001 by default). Restart the service. The log shows
`AllStar audio over USRP: listening on ...`, and `GET /api/link/audio`
reports whether it's running and whether the node is keyed.

Audio is accepted only from the node's host, because anything that arrives
is transmitted. Keep the listener on `127.0.0.1` when Asterisk runs on the
same machine.

## Checking it

`spikes/usrp_controller_check.py` runs the controller as node 1999's radio
with a simulated receiver. It checks both directions over a link to a second
node, and `doubling` checks a local user and a linked station talking at
once. `spikes/usrp_asl3_spike.py` checks the USRP channel alone.
