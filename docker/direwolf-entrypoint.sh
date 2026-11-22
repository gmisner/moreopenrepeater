#!/bin/sh
# Starts Dire Wolf, the packet modem for the Winlink gateway, from settings in the environment (direwolf.env, never winlink.env:
# the modem decodes untrusted radio traffic and is not given the gateway's passwords).
#
# It is given one sound card and one PTT device by compose.yaml (never all of /dev/snd), and opens only the card it was
# given: the repeater's sound card and PTT are not in this container, so a wrong setting here cannot key the repeater.
# The configuration is made from the environment at every start and checked first.
#
# It runs Dire Wolf through the reporter (winlink-reporter.py), which counts the frames heard and the frames sent for the
# dashboard's counters and passes Dire Wolf's output on, except the block Dire Wolf prints for each frame (the sender's call, the
# frame's text and what it decoded). Docker keeps this container's output on disk, so each such block becomes one line,
# "frame heard, audio level ..." or "frame sent", with no call and no text (docs/winlink.md, What it does and does not do).
#
#   --check   (the container's health check) the KISS port is listening, and the PTT device is a real one when cm108 PTT is asked for
#   (for tests: MOR_DIREWOLF_RENDER_ONLY=1 writes the configuration to MOR_DIREWOLF_CONFIG and stops;
#    MOR_PACKET_PTT_DEVICE names the PTT device, /dev/packet-ptt in the container)
set -eu

CONFIG="${MOR_DIREWOLF_CONFIG:-/run/direwolf/direwolf.conf}"
PTT_DEVICE="${MOR_PACKET_PTT_DEVICE:-/dev/packet-ptt}"

# real_device PATH: PATH is a character device that is not /dev/null. compose.yaml maps /dev/null at /dev/packet-ptt when
# MOR_PACKET_PTT_HIDRAW is not set (a path must be there for the container to start), and /dev/null is a character device, so
# `-c` alone would take "no PTT device" for one and Dire Wolf would run, write its PTT bytes to nowhere, and never key.
# Inside the container the mapped node is a new node with /dev/null's device numbers (1:3), not the same file, so `-ef` is not enough.
real_device() {
  [ -c "$1" ] || return 1
  [ "$1" -ef /dev/null ] && return 1
  null_numbers="$(stat -L -c '%t:%T' /dev/null 2>/dev/null || true)"
  [ -z "$null_numbers" ] || [ "$(stat -L -c '%t:%T' "$1" 2>/dev/null || true)" != "$null_numbers" ]
}

if [ "${1:-}" = "--check" ]; then
  if [ "${MOR_PACKET_PTT:-none}" = cm108 ] && ! real_device "$PTT_DEVICE"; then
    exit 1  # asked to key by CM108 and there is no device to key: not healthy, whatever the KISS port says
  fi
  awk '$4 == "0A" && $2 ~ /:1F41$/ { found = 1 } END { exit !found }' /proc/net/tcp /proc/net/tcp6 2>/dev/null  # 8001
  exit $?
fi

# One line only: a value with a newline in it would add lines of its own to the file made below, and the pattern checks
# below look at one line at a time. ok VALUE REGEX: the whole value matches.
NL='
'
ok() {
  case "$1" in *"$NL"*) return 1 ;; esac
  printf '%s' "$1" | grep -Eq "^($2)\$"
}

fail() { printf 'direwolf: %s\n' "$*" >&2; exit 2; }

[ -n "${MOR_WINLINK_CALL:-}" ] || fail "MOR_WINLINK_CALL is required (direwolf.env: run sh ./split-winlink-env.sh to make it from winlink.env, or copy direwolf.env.example)"
CALL="$(printf '%s' "$MOR_WINLINK_CALL" | tr 'a-z' 'A-Z')"
ok "$CALL" '[A-Z0-9]{3,7}' || fail "MOR_WINLINK_CALL is not a callsign"
if ok "$CALL" '[A-Z]{3,4}[0-9]{3,4}'; then
  fail "MOR_WINLINK_CALL looks like a GMRS callsign. The packet modem is for amateur stations only"
fi
SSID="${MOR_WINLINK_SSID:-10}"
ok "$SSID" '([1-9]|1[0-5])' || fail "MOR_WINLINK_SSID must be 1 to 15"

# The sound card: its number on the host, which compose.yaml maps (only that card's device files) into this container.
CARD="${MOR_PACKET_CARD:-}"
UDP="${MOR_DIREWOLF_TEST_AUDIO_UDP:-}"
if [ -n "$UDP" ]; then
  ok "$UDP" '[0-9]{4,5}' || fail "MOR_DIREWOLF_TEST_AUDIO_UDP is not a port"
  ADEVICE="UDP:$UDP null"   # test audio in over UDP, none out: no sound card (never for a station)
else
  ok "$CARD" '[0-9]{1,2}' || fail "MOR_PACKET_CARD (the packet radio's sound card number, from /proc/asound/cards on the host) is required"
  ADEVICE="plughw:$CARD,0"
fi

case "${MOR_PACKET_PTT:-none}" in
  none) PTT="" ;;     # a radio with VOX, or no transmitting yet
  cm108)
    GPIO="${MOR_PACKET_PTT_GPIO:-3}"
    ok "$GPIO" '[1-8]' || fail "MOR_PACKET_PTT_GPIO must be 1 to 8"
    real_device "$PTT_DEVICE" || fail "MOR_PACKET_PTT=cm108 but there is no PTT device: MOR_PACKET_PTT_HIDRAW is not set in .env (the container then gets /dev/null there), or it names something that is not there. Set it to the packet radio's interface, for example /dev/moreopenrepeater-packet (./setup.sh --packet-radio)"
    PTT="PTT CM108 $GPIO $PTT_DEVICE" ;;
  *) fail "MOR_PACKET_PTT must be none or cm108" ;;
esac

umask 077
{
  printf 'ADEVICE %s\nCHANNEL 0\nMYCALL %s-%s\nMODEM 1200\n' "$ADEVICE" "$CALL" "$SSID"
  [ -z "$PTT" ] || printf '%s\n' "$PTT"
  printf 'KISSPORT 8001\nAGWPORT 0\n'  # the gateway's KISS port, on this container's network only; no AGW
} > "$CONFIG"

printf 'direwolf: %s-%s on %s, KISS on 8001%s\n' "$CALL" "$SSID" "$ADEVICE" "${PTT:+, $PTT}"
[ "${MOR_DIREWOLF_RENDER_ONLY:-0}" = 1 ] && exit 0

DIREWOLF="${MOR_DIREWOLF_BIN:-/usr/local/bin/direwolf}"
STATUS_DIR="${MOR_WINLINK_STATUS_DIR:-/winlink-status}"
REPORTER="${MOR_WINLINK_REPORTER:-/usr/local/bin/winlink-reporter.py}"
if [ -d "$STATUS_DIR" ] && [ -f "$REPORTER" ] && command -v python3 >/dev/null 2>&1; then
  exec python3 "$REPORTER" modem --out "$STATUS_DIR/modem.json" --interval "${MOR_WINLINK_SCAN_SECONDS:-5}" -- "$DIREWOLF" -t 0 -c "$CONFIG"
fi
printf 'direwolf: sessions and counters are off: no status folder or no python here (the modem runs the same)\n'
exec "$DIREWOLF" -t 0 -c "$CONFIG"
