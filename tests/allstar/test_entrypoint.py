"""The AllStar container's entrypoint (#221, #229): a real AMI secret or no start, the two files it owns, and nothing else touched."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "allstar-entrypoint.sh"
GOOD = "0123456789abcdef0123456789abcdef"
MODULES = """\
; modules.conf of a node
[modules]
autoload = no
;   chan_simpleusb.so  Simple USB Radio Interface Channel Driver
load    = app_dial.so                    ; Dialing Application
require = app_rpt.so                     ; Radio Repeater/Remote Base Application
load    = chan_simpleusb.so              ; SimpleUSB Radio Interface Channel Driver
noload  = chan_usrp.so                   ; USRP Channel Module
  preload = chan_usbradio.so             ; USB Console Channel Driver
load    = chan_simpleusb_extras.so       ; a different module that starts the same way
require = res_usbradio.so                ; USB Radio Resource
"""


@pytest.fixture
def node(tmp_path):
    """A config directory with a fake asterisk that records how it was started."""
    conf = tmp_path / "etc-asterisk"
    conf.mkdir()
    (conf / "rpt.conf").write_text("[1999]\nrxchannel=USRP/127.0.0.1:32001:34001\n")
    (conf / "iax.conf").write_text("[general]\nsecret=do-not-touch\n")
    (conf / "modules.conf").write_text(MODULES)
    fake = tmp_path / "fake-asterisk"
    fake.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/started\n')
    fake.chmod(0o755)
    return conf, fake, tmp_path


def run(node, **env):
    conf, fake, tmp = node
    environment = {"PATH": os.environ["PATH"], "ASL_CONF_DIR": str(conf), "ASL_ASTERISK": str(fake), "MOR_AMI_SECRET": GOOD, **env}
    environment = {k: v for k, v in environment.items() if v is not None}
    return subprocess.run(["sh", str(SCRIPT)], env=environment, capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize(
    "env, words",
    [
        ({"MOR_AMI_SECRET": None}, "is not set"),
        ({"MOR_AMI_SECRET": ""}, "is not set"),
        ({"MOR_AMI_SECRET": "make4An1ce-secret"}, "published default"),
        ({"MOR_AMI_SECRET": "short"}, "too short"),
        ({"MOR_AMI_SECRET": "fifteen-chars-ok"[:15]}, "too short"),
        ({"MOR_AMI_SECRET": "has a space in it 1234567"}, "may only contain"),
        ({"MOR_AMI_SECRET": "semi;colon;0123456789abc"}, "may only contain"),
        ({"MOR_AMI_SECRET": "line\nbreak0123456789abcdef"}, "may only contain"),
        ({"MOR_AMI_SECRET": "[bracket]0123456789abcdef"}, "may only contain"),
        ({"MOR_AMI_USER": "ad min"}, "MOR_AMI_USER"),
        ({"MOR_AMI_USER": "adm;in"}, "MOR_AMI_USER"),
        ({"MOR_AMI_USER": "]\n[root"}, "MOR_AMI_USER"),
        ({"MOR_AMI_PERMIT": "not-a-network"}, "MOR_AMI_PERMIT"),
        ({"MOR_AMI_PERMIT": "/24"}, "MOR_AMI_PERMIT"),
        ({"MOR_AMI_PERMIT": "172.28.0.0/"}, "MOR_AMI_PERMIT"),
        ({"MOR_AMI_PERMIT": "172.28.0.0\npermit=0.0.0.0/0"}, "MOR_AMI_PERMIT"),
    ],
)
def test_it_will_not_start_without_a_real_secret_and_sane_settings_and_leaves_no_config_behind(node, env, words):
    conf, _, tmp = node
    result = run(node, **env)
    assert result.returncode == 2 and words in result.stderr
    assert not (conf / "manager.conf").exists() and not (tmp / "started").exists()  # never started


def test_the_secret_is_never_printed_on_a_good_start_or_a_refusal(node):
    good = run(node)
    assert good.returncode == 0 and GOOD not in good.stdout + good.stderr
    for secret in ("make4An1ce-secret", "tooshort1", "semi;colon;0123456789abc"):  # refused as the default, too short, bad characters
        refused = run(node, MOR_AMI_SECRET=secret)
        assert refused.returncode == 2 and secret not in refused.stdout + refused.stderr


def test_a_good_start_writes_the_ami_config_and_starts_asterisk_in_the_foreground(node):
    conf, _, tmp = node
    result = run(node, MOR_AMI_USER="mor", MOR_AMI_PERMIT="172.30.4.0/24")
    assert result.returncode == 0
    assert (tmp / "started").read_text().strip() == "-f"
    manager = (conf / "manager.conf").read_text()
    lines = [line for line in manager.splitlines() if line and not line.startswith(";")]
    assert lines[:4] == ["[general]", "enabled = yes", "port = 5038", "bindaddr = 0.0.0.0"]
    assert "[mor]" in lines and f"secret = {GOOD}" in lines
    assert lines.index("deny = 0.0.0.0/0.0.0.0") < lines.index("permit = 172.30.4.0/24")  # everything refused, then the network allowed
    assert "permit = 127.0.0.1/255.255.255.255" in lines
    assert "make4An1ce-secret" not in manager and "[admin]" not in manager


@pytest.mark.parametrize("network", ["172.28.0.0/24", "10.0.0.0/255.0.0.0", "192.168.1.0/24"])
def test_a_network_may_be_a_prefix_or_a_mask(node, network):
    assert run(node, MOR_AMI_PERMIT=network).returncode == 0
    assert f"permit = {network}" in (node[0] / "manager.conf").read_text()


def test_the_default_network_is_the_one_compose_defines(node):
    assert run(node, MOR_AMI_PERMIT=None).returncode == 0
    assert "permit = 172.28.0.0/24" in (node[0] / "manager.conf").read_text()


def test_the_log_goes_to_the_console_only_and_never_records_dtmf(node):
    run(node)
    logger = (node[0] / "logger.conf").read_text()
    logfiles = logger.split("[logfiles]")[1].strip().splitlines()
    assert logfiles == ["console => notice,warning,error"]  # no file, and not the dtmf level the package's default logs
    assert "dtmf" not in logger.lower() and "debug" not in logger.lower()


def test_it_rewrites_its_two_files_at_every_start_so_they_cannot_drift(node):
    conf = node[0]
    run(node)
    (conf / "manager.conf").write_text("[general]\nbindaddr = 0.0.0.0\n[admin]\nsecret = make4An1ce-secret\n")
    (conf / "logger.conf").write_text("[logfiles]\nmessages.log => dtmf\n")
    run(node)
    assert "make4An1ce-secret" not in (conf / "manager.conf").read_text() and GOOD in (conf / "manager.conf").read_text()
    assert "dtmf" not in (conf / "logger.conf").read_text()


def test_nothing_the_node_owner_edits_is_touched(node):
    conf = node[0]
    before = {name: (conf / name).read_bytes() for name in ("rpt.conf", "iax.conf")}
    run(node)
    assert {name: (conf / name).read_bytes() for name in before} == before


def test_no_half_written_file_is_left_behind(node):
    run(node)
    assert not list(node[0].glob("*.new"))


def test_only_the_two_sound_card_drivers_are_switched_off_and_the_rest_of_modules_conf_is_as_it_was(node):
    conf = node[0]
    result = run(node)
    expected = MODULES.replace(
        "\nload    = chan_simpleusb.so ", "\nnoload    = chan_simpleusb.so "
    ).replace("\n  preload = chan_usbradio.so ", "\n  noload = chan_usbradio.so ")
    assert (conf / "modules.conf").read_text() == expected  # the keyword and nothing else; indent and comments are kept
    after = expected.splitlines()
    assert "load    = chan_simpleusb_extras.so       ; a different module that starts the same way" in after  # a longer name
    assert "require = res_usbradio.so                ; USB Radio Resource" in after  # not a sound card driver
    assert ";   chan_simpleusb.so  Simple USB Radio Interface Channel Driver" in after  # a comment
    assert "no sound card" in result.stderr


def test_it_says_nothing_about_modules_when_there_is_nothing_to_switch_off(node):
    run(node)
    again = run(node)
    assert "no sound card" not in again.stderr  # already off


def test_a_node_with_no_modules_conf_still_starts(node):
    (node[0] / "modules.conf").unlink()
    assert run(node).returncode == 0


def test_the_script_is_executable_and_valid_shell():
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
