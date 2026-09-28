"""One-off spike: verify link.node_link's app_rpt AMI event translation
against real Asterisk, and pin down the RPT_ALINKS wire format before
trusting it.

Not part of the test suite -- run manually against the same Lima VM used by
audiosocket_asl3_spike.py. Confirmed working 2026-09-27 against AllStarLink
ASL3 (asl3-asterisk 2:22.10.1+asl3-3.10.5-1.deb12) on the same Debian 12
arm64 VM, using a second local node (1998) as a link partner.

Findings (also captured in link/node_link.py's module docstring):

- app_rpt's own source (github.com/AllStarLink/app_rpt,
  apps/app_rpt/rpt_link.c's `rpt_update_links`/`__mklinklist`) confirms the
  RPT_ALINKS EventValue format: `<count>,<name><mode><keyed>[,...]` where
  mode in {T,R,L,C} and keyed in {K,U}. Re-triggered on every link-set
  change (connect/disconnect) *and* every time a linked node's `lastrx1`
  (keyed) flag flips -- confirmed both properties live, not just read from
  source.
- The default ASL3 install's rpt.conf templates a *real-radio* node
  (`rxchannel = SimpleUSB/1999`) -- wrong shape for this project, where the
  local "radio" is entirely our own audio_io/controller, not app_rpt. The
  correct node config for our own sidecar node is the documented hub form:
  `rxchannel = Local/pseudo`. Reconfigured node 1999 to this before testing.
- AMIClient had a real bug here too: a Command action's response repeats
  the "Output" header once per output line, which a plain dict silently
  collapsed to the last line only -- fixed in ami_client.py (see its
  regression test) before writing the RPT_ALINKS-consuming code that
  depends on multi-line Command output (`rpt xnode`/`rpt stats`) for
  ad-hoc debugging.

To reproduce:

    # 1. VM from audiosocket_asl3_spike.py, still running. Add a second
    #    node as a no-radio "hub" link partner, and switch node 1999 itself
    #    to the same hub shape (matching our actual architecture, not the
    #    installer's SimpleUSB default):
    limactl shell asl3 -- sudo sed -i \\
        's/^rxchannel = SimpleUSB\\/1999.*/rxchannel = Local\\/pseudo/' \\
        /etc/asterisk/rpt.conf
    limactl shell asl3 -- sudo bash -c "cat >> /etc/asterisk/rpt.conf <<'EOF'

1998 = radio@127.0.0.1/1998,NONE
EOF"
    # (add "1998 = radio@127.0.0.1/1998,NONE" to the [nodes] stanza too,
    # and a [1998](node-main) stanza with rxchannel = Local/pseudo)
    limactl shell asl3 -- sudo systemctl restart asterisk

    # 2. AMI tunnel (reuse the -L 5038 half of audiosocket_asl3_spike.py's
    #    tunnel command; AudioSocket's -R half isn't needed for this spike):
    ssh -F ~/.lima/asl3/ssh.config -o ControlPath=/tmp/asl3-my-tunnel.sock \\
        -fN -L 5038:127.0.0.1:5038 lima-asl3

    # 3. Run this script, then in another shell drive real link activity:
    #    limactl shell asl3 -- sudo asterisk -rx "rpt fun 1999 *31998"  # connect
    #    limactl shell asl3 -- sudo asterisk -rx "rpt fun 1999 *11998"  # disconnect
    python3 spikes/node_link_ami_spike.py
"""
import asyncio

from link.node_link import NodeLinkClient

AMI_HOST = "127.0.0.1"
AMI_PORT = 5038
AMI_USER = "admin"
AMI_SECRET = "make4An1ce-secret"  # matches manager.conf's [admin] stanza on the spike VM
LOCAL_NODE_ID = "1999"


async def main() -> None:
    client = NodeLinkClient(AMI_HOST, AMI_PORT, AMI_USER, AMI_SECRET, LOCAL_NODE_ID)
    await client.connect()
    print(f"connected; watching node {LOCAL_NODE_ID} for link/keyed changes for 60s...")
    try:
        async for event in _with_timeout(client.events(), seconds=60):
            print("controller event:", event)
    finally:
        await client.close()


async def _with_timeout(aiter, seconds: float):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        try:
            yield await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
        except asyncio.TimeoutError:
            return


if __name__ == "__main__":
    asyncio.run(main())
