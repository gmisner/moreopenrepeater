import pytest

from link.asl_config import (
    MARK,
    control_node,
    controller_settings,
    module_state,
    nodes,
    release_node,
    restore_module,
    set_module,
)

RPT = """\
[general]
node_lookup_method = dns

[node-main](!)
rxchannel = Local/pseudo            ; No radio (hub)
duplex = 2                          ; full duplex
hangtime = 2000
idrecording = |iNOTSET

[functions-main](!)
1 = ilink,1

[functions](functions-main)

#tryinclude "custom/rpt.conf"
#tryinclude "custom/rpt/*.conf"

;;;;;;;;;;;;;;;;;;;;;;;;; Your node settings here ;;;;;;;;;;;;;;;;;;;;;;;;;;
[1999](node-main)
rxchannel = SimpleUSB/1999      ; SimpleUSB
;startup_macro = *8132000

;;;;;;;;;;;;;;;;;; Settings for another node ;;;;;;;;;;;;;;;;;;
[1998](node-main)
"""

MODULES = """\
[modules]
autoload = no

load = chan_simpleusb.so              ; SimpleUSB Radio Interface Channel Driver
noload = chan_usbradio.so               ; USB Console Channel Driver
noload = chan_usrp.so                   ; USRP Channel Module
require = res_usbradio.so

[global]
"""

OURS = controller_settings("USRP/127.0.0.1:34001:32001")


def test_nodes_resolve_their_template():
    assert [(n.number, n.rxchannel, n.duplex, n.controlled) for n in nodes(RPT)] == [
        ("1999", "SimpleUSB/1999", "2", False),
        ("1998", "Local/pseudo", "2", False),
    ]


def test_controlling_a_node_changes_only_its_stanza():
    edited = control_node(RPT, "1999", OURS)
    before, after = RPT.split("[1999](node-main)\n"), edited.split("[1999](node-main)\n")
    assert after[0] == before[0]
    assert after[1].split("[1998]")[1] == before[1].split("[1998]")[1]
    assert after[1].startswith(
        f"rxchannel = USRP/127.0.0.1:34001:32001  {MARK}\n"
        f"duplex = 0  {MARK}\n"
    )
    assert ";moreopenrepeater was: rxchannel = SimpleUSB/1999      ; SimpleUSB\n" in edited
    node = nodes(edited)[0]
    assert (node.rxchannel, node.duplex, node.controlled) == ("USRP/127.0.0.1:34001:32001", "0", True)
    assert node.usrp() == ("127.0.0.1", 34001, 32001)


def test_releasing_puts_the_stanza_back_exactly():
    edited = control_node(RPT, "1999", OURS)
    assert release_node(edited, "1999") == RPT
    assert release_node(RPT, "1999") == RPT


def test_controlling_again_replaces_our_settings_instead_of_stacking():
    once = control_node(RPT, "1999", OURS)
    twice = control_node(once, "1999", controller_settings("USRP/127.0.0.1:34001:32005"))
    assert twice.count(MARK) == len(OURS)
    assert twice.count(";moreopenrepeater was:") == 1
    assert release_node(twice, "1999") == RPT


def test_a_node_without_its_own_settings():
    edited = control_node(RPT, "1998", OURS)
    assert edited.endswith(f"[1998](node-main)\n" + "".join(f"{k} = {v}  {MARK}\n" for k, v in OURS.items()))
    assert release_node(edited, "1998") == RPT


def test_unknown_node():
    with pytest.raises(ValueError):
        control_node(RPT, "2000", OURS)


def test_modules_switch_in_place_and_restore():
    edited = set_module(set_module(MODULES, "chan_usrp.so", True), "chan_simpleusb.so", False)
    assert module_state(edited, "chan_usrp.so") == "load"
    assert module_state(edited, "chan_simpleusb.so") == "noload"
    assert f"load = chan_usrp.so  {MARK}\n;moreopenrepeater was: noload = chan_usrp.so" in edited
    assert restore_module(restore_module(edited, "chan_usrp.so"), "chan_simpleusb.so") == MODULES


def test_modules_already_right_or_missing():
    assert set_module(MODULES, "chan_usbradio.so", False) == MODULES
    added = set_module(MODULES, "chan_echolink.so", True)
    assert f"require = res_usbradio.so\nload = chan_echolink.so  {MARK}\n\n[global]" in added
    assert module_state(added, "chan_echolink.so") == "load"
    assert restore_module(added, "chan_echolink.so") == MODULES
