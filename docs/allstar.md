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

Tested with ASL3 (Asterisk 22.10.1, app_rpt 3.10.5). Set up the node itself
first (node number, callsign, password) with `sudo asl-menu`, as for any
ASL3 node. `scripts/install-pi.sh --allstar` installs ASL3 beside the
controller, and sets up the file permissions and AMI login below.

### From the dashboard

On the **AllStarLink** page, choose the node and click **Use this node**.
The controller:

- puts these lines at the top of the node's stanza in `rpt.conf`, keeping
  any line they replace as a `;moreopenrepeater was:` comment:

  ```ini
  [1999](node-main)
  rxchannel = USRP/127.0.0.1:34001:32001  ; set by moreopenrepeater
  duplex = 0  ; set by moreopenrepeater
  linktolink = yes  ; set by moreopenrepeater
  hangtime = 0  ; set by moreopenrepeater
  althangtime = 0  ; set by moreopenrepeater
  nounkeyct = 1  ; set by moreopenrepeater
  ```

- changes `noload = chan_usrp.so` to `load` in `modules.conf`, and turns off
  `chan_simpleusb` and `chan_usbradio` if no other node uses them. Those
  drivers would open the USB sound card that the controller is using.
- restarts Asterisk over AMI. A reload keeps the node's old radio channel.
  The restart drops links and any autopatch call for a few seconds.
- starts sending and receiving the node's audio. After that, it finds the
  node from `rpt.conf` whenever it starts.

**Stop using this node** takes those lines out and puts back what they
replaced. A copy of each file from before every change is kept in the data
folder's `asterisk-backups`.

The controller edits the files directly, because saving `rpt.conf` over AMI
would copy every template's settings into the sections that use it. So the
service needs write access to them, and the installer sets that up:

```sh
sudo usermod -aG asterisk moreopenrepeater
sudo chmod g+w /etc/asterisk/rpt.conf /etc/asterisk/modules.conf /etc/asterisk/echolink.conf
sudo systemctl restart moreopenrepeater
```

This gives the controller nothing it couldn't already do: its AMI login can
rewrite the dialplan anyway. It also needs the AMI settings
(`MOREOPENREPEATER_AMI_*`) to restart Asterisk. Without them, restart it
yourself after each change.

The settings do this:

- `rxchannel = USRP/<controller host>:<controller port>:<node port>`. The
  controller listens on the first port (34001, or the port in
  `MOREOPENREPEATER_USRP_LISTEN`) and the node on the second (32001, or the
  next port no other USRP node uses).
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

The node still sends its own ID (`idrecording` in the `node-main` template,
every `idtime`), and the repeater transmits it like anything else the node
sends.

### By hand

Make the same changes to `rpt.conf` and `modules.conf` yourself, restart
Asterisk, and tell the controller where the node is in
`/etc/moreopenrepeater/env`:

```sh
MOREOPENREPEATER_USRP_NODE=127.0.0.1:32001
#MOREOPENREPEATER_USRP_LISTEN=127.0.0.1:34001
```

`USRP_NODE` is the node's host and the last port in `rxchannel`. The
dashboard then shows the node but leaves its settings alone.

### Where the controller listens

Audio is accepted only from the node's host, because anything that arrives
is transmitted. Keep the listener on `127.0.0.1` (the default) when Asterisk
runs on the same machine. If it doesn't, set `MOREOPENREPEATER_USRP_LISTEN`
to `0.0.0.0:34001`, and set `MOREOPENREPEATER_USRP_ADDRESS` to the address
Asterisk reaches the controller at.

For development with Asterisk in a VM, `MOREOPENREPEATER_ASTERISK_SHELL` is
a command prefix that reaches its files, for example
`limactl shell asl3 -- sudo`.

## Checking it

`spikes/usrp_controller_check.py` runs the controller as node 1999's radio
with a simulated receiver. It checks both directions over a link to a second
node, and `doubling` checks a local user and a linked station talking at
once. `spikes/usrp_asl3_spike.py` checks the USRP channel alone.
