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

On a simplex node (Timing page), local users still go to the node, but the
controller holds linked stations off the air until the local user unkeys,
since one radio can't transmit and receive at once. `rpt.conf` stays the
same in both modes: the controller does the half duplex itself, so switching
doesn't restart Asterisk.

The AMI connection (`MOREOPENREPEATER_AMI_*`) is separate. It shows links
and remote key-ups on the dashboard and sends DTMF macros' link commands.
Set up both for a complete AllStar node.

## Setup

Tested with ASL3 (Asterisk 22.10.1, app_rpt 3.10.5).
`scripts/install-pi.sh --allstar` installs ASL3 beside the controller, and
sets up the file permissions and AMI login below. On a new ASL3 it also turns
off `chan_simpleusb` and `chan_usbradio` right away: the placeholder node
1999 that ASL3 comes with would open the controller's sound card.

You can set the portal node number, password and callsign from the dashboard
(below), or with `sudo asl-menu` as for any ASL3 node. Use `asl-menu` for
USB tuning, duplex options, and adding a second local node.

### From the dashboard

On the **AllStarLink** page, under **Set node number**, enter the node
number, password and callsign from the AllStarLink portal and click **Save
and use this node**. That renames the ASL3 placeholder (usually 1999) in
`rpt.conf`, updates HTTP registration in `rpt_http_registrations.conf`, sets
`idrecording`, and then binds the controller as that node's radio.

If the node stanza already exists, choose it and click **Use this node**.
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
sudo chmod g+w /etc/asterisk/rpt.conf /etc/asterisk/modules.conf /etc/asterisk/echolink.conf \
  /etc/asterisk/rpt_http_registrations.conf /etc/asterisk/extensions.conf \
  /etc/asterisk/simpleusb.conf /etc/asterisk/usbradio.conf /etc/asterisk/voter.conf \
  /etc/asterisk/savenode.conf /etc/asterisk/iax.conf
sudo systemctl restart moreopenrepeater
```

This gives the controller nothing it couldn't already do: its AMI login can
rewrite the dialplan anyway. It also needs the AMI settings
(`MOREOPENREPEATER_AMI_*`) to restart Asterisk. Without them, restart it
yourself after each change.

**Updates without `--allstar`**: If you run `install-pi.sh` without `--allstar`
(e.g. a regular update), the installer re-applies `usermod -aG asterisk` and
`chmod g+w` on all `/etc/asterisk` conf files whenever `/etc/asterisk/rpt.conf`
is present. This prevents ASL package upgrades from leaving the service user
without write access.

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

## Importing a node from an existing ASL3 install

This is for someone who already runs ASL3 on the repeater's computer and is moving to the container install
(`docs/docker.md`). Instead of typing the node number, callsign and password again, the dashboard can read the old node's
`/etc/asterisk` and bring the node over. It reads that folder and never writes to it.

```sh
cd docker
./setup.sh --callsign W1AW --import-allstar /etc/asterisk    # implies --allstar
```

`setup.sh` records the folder as `MOR_ALLSTAR_IMPORT_DIR` in `docker/.env` (and keeps a value you already set). Compose
mounts it read-only into the API container only, as `/import/asterisk`. Without it, compose mounts the empty `docker/empty`
folder, so an install that isn't importing has nothing new on its AllStar page.

Then open the **AllStarLink** page, **Node and EchoLink** tab. As an admin you see **Found an existing node
configuration**: the node number, callsign, how many allowed links it has, its startup macro, and whether it has a
registration (yes or no; the password is never shown, and no API response or log line carries it). If the file has more than
one node, pick the one that is the repeater. Nothing is written until you click **Import node**.

What it takes:

- **Node number.** The container node's stock placeholder (1999) is renamed to it, the same way **Set node number** does.
- **Callsign**, from `idrecording = |iW1AW` (or `idtalkover`), also when it comes from a template such as `[node-main]`.
  If the old ID is a recording and not a callsign, the page asks for one.
- **Registration password**, from `rpt_http_registrations.conf`, then `iax.conf`, then `savenode.conf`, written to the
  container's own registration files. It is read again from the old files at the moment you click Import, held in memory for
  that one step and not kept. A password the dashboard can't write safely (it takes 6 or more letters, digits, `_` or `-`)
  is left out and the page says so; add it under **Edit node**.
- **Startup macro** (`startup_macro`), if it is a plain DTMF string.
- **Allowed links**: the entries in the node's `nodes` list (the stanza its `nodes =` setting names, `[nodes]` if none).
  Entries the container already has are kept, and entries that point at the old machine's loopback are left out, since there
  that address would be the container itself.

What it deliberately leaves: the radio. In the container the radio is the controller's core over USRP, so
`rxchannel`, `txchannel`, `duplex` and the other channel driver settings, the sound card, and the PTT and COS settings
(`simpleusb.conf`, `usbradio.conf`, `voter.conf`) are neither copied nor read. `manager.conf` isn't read either (the container
writes its own management login), and IAX users and peers, EchoLink and the rest of the old files stay where they are. Importing
then makes the container node's radio the controller, exactly as **Use this node** does, and restarts Asterisk.

The files are read the way Asterisk reads them where it matters: comments and `;-- --;` blocks, `#include` and `#tryinclude`
(only files inside the folder; `/etc/asterisk/...` paths mean the same place in it), and templates such as
`[1999](node-main)`. `#exec` is never run. A file that is missing or can't be read is named on the page with the reason.
The most likely one is permissions: the API container runs as user 999, and a host node's files are usually readable only by
root and the `asterisk` group. If the page says so, make a private copy and point the import at it, then delete the copy
(it holds the node's password):

```sh
sudo cp -a /etc/asterisk /var/tmp/asterisk-import
sudo chown -R 999:999 /var/tmp/asterisk-import && sudo chmod -R go-rwx /var/tmp/asterisk-import
# in docker/.env: MOR_ALLSTAR_IMPORT_DIR=/var/tmp/asterisk-import   then:  docker compose up -d api
```

If the container node already has a different node set up (a number that isn't a stock placeholder, or one that registers),
the import refuses until you tick **Replace node N**; that renames it and changes its callsign and registration. Importing the
same node again changes nothing: a second click, or a second run, leaves the files as they are.

**Undoing it.** Every write is backed up as for any other change, and the import also keeps one restore point first: a folder
`import-<date>-<time>` in the data folder's `asterisk-backups` with every file it might touch, as it was. The first one is
never removed. To go back, copy the files out of it:

```sh
docker compose exec api sh -c 'cp /data/asterisk-backups/import-20261120-101010/* /etc/asterisk/'
docker compose restart allstar
```

Copy back only the files you want. The old node on the host isn't touched, so it can simply be started again.

Not done: nothing here was tried against a real ASL3 node or on a Pi; it is covered by tests on realistic sample files.

## EchoLink

ASL3 includes an EchoLink channel driver, `chan_echolink`. With it on,
EchoLink stations connect to the repeater's node like AllStar links: they're
heard on the repeater, key it up and show in the link list. Everything the
repeater repeats goes to them.

You need an EchoLink callsign for the repeater, validated by EchoLink. For a
repeater that's the callsign with `-R` (`W1AW-R`), and EchoLink assigns the
node number and password when it validates it. Forward UDP ports 5198 and
5199 on the router to the machine running Asterisk.

Turn it on from the **EchoLink** card on the **AllStarLink** page, after
choosing the node. Blank fields start from the repeater's settings: the
callsign with `-R`, and the APRS page's position, frequency and tone. Saving
writes them to `echolink.conf`'s `[el0]` section, marked like the node's lines
in `rpt.conf`, loads `chan_echolink` in `modules.conf` and restarts Asterisk.
The password is written there but never shown on the dashboard. **Turn off
EchoLink** puts `modules.conf` back and keeps the details for next time.
Choosing a different node moves EchoLink to it.

To connect to an EchoLink node over the air, dial `*3` and its number with a
leading 3, padded to six digits: `*33009999` connects to node 9999 (the echo
test server). `*1` and the same number disconnects it. EchoLink users connect
to the repeater with its EchoLink node number as usual.

## Links

The **Links** card on the dashboard lists the node's links with each
station's callsign, description and location, whether it's a full link or
monitor only, and which one is talking. **Connect** links any node number,
optionally monitor only (you hear it, it doesn't hear the repeater);
**Disconnect** drops one link and **Disconnect all** drops them all. These
send the same `ilink` commands as the DTMF codes, through Asterisk's manager
interface, so they need `MOREOPENREPEATER_AMI_HOST` and the node chosen.
Viewers see the links but can't change them.

Callsigns come from AllStarLink's list of registered nodes, downloaded from
`allmondb.allstarlink.org` once a day into `data/allstar-nodes.txt`. Without
internet the last copy keeps working, and without any copy nodes are shown by
number. EchoLink stations show as their 3xxxxxx number, and their callsign
comes from `chan_echolink`'s directory when EchoLink is on.

**Favorite nodes** on the AllStarLink page become one-click buttons on the
Links card. Each has an optional name (shown instead of the callsign) and can
connect monitor only, which suits a busy hub you just want to listen to.

**Scheduled links**, also on the AllStarLink page, link a node at a set time
each week and drop it after a number of minutes: a net's hub on Tuesdays at
19:00 for 60 minutes, say. Times are the station's clock, like scheduled
announcements. 0 minutes leaves the link up until someone disconnects it. A
scheduled link shows "until" and its end time on the dashboard. The
controller checks the schedule every 15 seconds, so a link can start up to
15 seconds late. It only drops links it made: if the node was already linked
at the start time, it's left alone. Disconnecting a scheduled link by hand
keeps it down until the next start. If the controller restarts during a
window, it links the node again, or, if the link is still up, still drops it
at the end. A 0-minute schedule is only started within 5 minutes of its
start time.

## Checking it

`spikes/usrp_controller_check.py` runs the controller as node 1999's radio
with a simulated receiver. It checks both directions over a link to a second
node, and `doubling` checks a local user and a linked station talking at
once. `spikes/usrp_asl3_spike.py` checks the USRP channel alone.
