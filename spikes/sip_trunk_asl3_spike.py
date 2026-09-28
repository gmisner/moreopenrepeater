"""One-off spike: set up the autopatch SIP trunk in real Asterisk over AMI.

Not part of the test suite. Runs api.sip_trunk against the ASL3 Lima VM:
turns on the SIP and AudioSocket modules, saves a trunk, and reports its
registration. The "provider" is the same Asterisk on another port -- a
registrar that challenges for a password and sends calls to the patchtest
dialplan (see autopatch_asl3_spike.py) -- so real SIP and RTP are
exercised without a phone company.

Fake provider, in the VM as root. After this spike has run once (so the
trunk's transport on 5060 comes first in pjsip.conf), append to
/etc/asterisk/pjsip.conf:

    [provider-transport]
    type = transport
    protocol = udp
    bind = 0.0.0.0:5070

    [patchuser]
    type = endpoint
    transport = provider-transport
    context = patchtest
    disallow = all
    allow = ulaw
    auth = patchuser-auth
    aors = patchuser

    [patchuser-auth]
    type = auth
    auth_type = userpass
    username = patchuser
    password = pa;ss w0rd

    [patchuser]
    type = aor
    max_contacts = 1

    [patchuser-identify]
    type = identify
    endpoint = patchuser
    match = 127.0.0.1

and load the provider-side modules:

    for m in res_pjsip_registrar res_pjsip_authenticator_digest res_pjsip_endpoint_identifier_ip; do
        asterisk -rx "module load $m.so"; done
    asterisk -rx "module reload res_pjsip.so"

Then run it again, and autopatch_asl3_spike.py with
SPIKE_DIAL_STRING='PJSIP/{number}@mor-trunk':

    MOREOPENREPEATER_AMI_SECRET=... PYTHONPATH=src .venv/bin/python spikes/sip_trunk_asl3_spike.py
"""
from __future__ import annotations

import asyncio
import json
import os

from api.autopatch import PatchSettings
from api.sip_trunk import SipTrunk, TrunkSettings


async def main():
    settings = PatchSettings(
        "127.0.0.1", 5038, "admin", os.environ["MOREOPENREPEATER_AMI_SECRET"], "0.0.0.0", 9092, "192.168.5.2:9092"
    )
    trunk = SipTrunk(settings)

    print("1) status before:", json.dumps(await trunk.status(), indent=1))
    print("2) enabling modules")
    await trunk.enable_modules()
    print("   missing after:", (await trunk.status())["missing_modules"])
    print("3) saving the trunk")
    await trunk.save(TrunkSettings(server="127.0.0.1", port=5070, username="patchuser", password="pa;ss w0rd"))
    for _ in range(10):
        status = await trunk.status()
        if status["registration"] == "Registered" and status["reachability"]:
            break
        await asyncio.sleep(1)
    print("   status after:", json.dumps(status, indent=1))
    print("4) saving again without a password keeps it")
    await trunk.save(TrunkSettings(server="127.0.0.1", port=5070, username="patchuser"))
    await asyncio.sleep(2)
    print("   registration:", (await trunk.status())["registration"])


if __name__ == "__main__":
    asyncio.run(main())
