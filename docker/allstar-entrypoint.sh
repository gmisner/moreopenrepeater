#!/bin/sh
# Starts the AllStarLink node (Asterisk with app_rpt) in a container.
#
# It owns two files and nothing else. The rest of /etc/asterisk (rpt.conf, modules.conf, echolink.conf,
# extensions.conf, iax.conf, ...) belongs to the node's owner and the dashboard's AllStar setup, which edit them
# in the volume, so they are left exactly as they are.
#
#   manager.conf  Asterisk's management interface (AMI), which the API's link service uses. The package ships it with a
#                 well-known admin secret, safe only because it listens on the host's loopback. A container's AMI is
#                 reachable by every container on its network, so this refuses to start without a real secret, and
#                 allows only the services network.
#   logger.conf   Everything to the console, so `docker logs` has it and nothing is written to the disk. The package's
#                 default also logs DTMF digits to a file; the log is no place for what someone keyed.
#
# Both are written afresh at every start from the settings in the environment, so they cannot drift from them.
#
# It also switches off the two radio drivers that open a USB sound card (chan_simpleusb, chan_usbradio) if
# modules.conf loads them, and touches nothing else in that file. A container has no sound card (the repeater's
# audio reaches the node from the core over USRP), and a driver looking for one fills the log, thousands of lines
# in half a minute, which on a Pi is wear on the SD card.
set -eu

CONF_DIR="${ASL_CONF_DIR:-/etc/asterisk}"
ASTERISK="${ASL_ASTERISK:-asterisk}"
USER_NAME="${MOR_AMI_USER:-admin}"
SECRET="${MOR_AMI_SECRET:-}"
PERMIT="${MOR_AMI_PERMIT:-172.28.0.0/24}"
DEFAULT_SECRET="make4An1ce-secret"

fail() {
  echo "allstar: $1" >&2
  exit 2
}

[ -n "$SECRET" ] || fail "MOR_AMI_SECRET is not set. Set it in allstar.env (for example: openssl rand -hex 16); the API's link service uses the same value."
[ "$SECRET" != "$DEFAULT_SECRET" ] || fail "MOR_AMI_SECRET is the package's published default. Choose your own."
[ "${#SECRET}" -ge 16 ] || fail "MOR_AMI_SECRET is too short: use at least 16 characters."
case "$SECRET" in
  *[!A-Za-z0-9._~+=-]*) fail "MOR_AMI_SECRET may only contain letters, digits and . _ ~ + = -" ;;
esac
case "$USER_NAME" in
  ''|*[!A-Za-z0-9._-]*) fail "MOR_AMI_USER may only contain letters, digits and . _ -" ;;
esac
# an address with a prefix length (172.28.0.0/24) or a mask (172.28.0.0/255.255.255.0)
case "$PERMIT" in
  *[!0-9./]*|''|*/|/*|*//*) fail "MOR_AMI_PERMIT must be a network such as 172.28.0.0/24" ;;
esac

write() {
  # write NAME: the text on stdin becomes the file, replaced in one step so a reader never sees half of it
  cat > "$CONF_DIR/$1.new"
  mv "$CONF_DIR/$1.new" "$CONF_DIR/$1"
}

write manager.conf <<CONF
; Written by the container's entrypoint at every start. Change MOR_AMI_* in allstar.env, not this file.
[general]
enabled = yes
port = 5038
bindaddr = 0.0.0.0

[$USER_NAME]
secret = $SECRET
deny = 0.0.0.0/0.0.0.0
permit = $PERMIT
permit = 127.0.0.1/255.255.255.255
read = all,system,call,log,verbose,command,agent,user,config
write = all,system,call,log,verbose,command,agent,user,config
CONF

write logger.conf <<'CONF'
; Written by the container's entrypoint at every start.
[general]
dateformat=%F %T.%3q

[logfiles]
console => notice,warning,error
CONF

MODULES="$CONF_DIR/modules.conf"
if [ -f "$MODULES" ] && grep -Eq '^[[:space:]]*(load|preload|require)[[:space:]]*=[[:space:]]*chan_(simpleusb|usbradio)\.so' "$MODULES"; then
  sed -E 's/^([[:space:]]*)(load|preload|require)([[:space:]]*=[[:space:]]*chan_(simpleusb|usbradio)\.so)/\1noload\3/' "$MODULES" > "$MODULES.new"
  mv "$MODULES.new" "$MODULES"
  echo "allstar: chan_simpleusb and chan_usbradio are switched off: there is no sound card in this container (the audio comes from the core over USRP)." >&2
fi

exec $ASTERISK -f
