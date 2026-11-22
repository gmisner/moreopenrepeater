#!/bin/sh
# Starts the Winlink RMS gateway (LinBPQ) from settings in the environment (winlink.env, docs/winlink.md).
#
# The configuration is made from the environment at every start, so what runs is what the file says and nothing is edited by
# hand inside the container. It is checked first and the gateway does not start on a setting it cannot trust: a wrong
# character in a password or a callsign would be a broken or a dangerous configuration file, and a gateway that is up but
# misconfigured is worse than one that says why it is not.
#
# It also starts the reporter (winlink-reporter.py) beside LinBPQ: it reads the two logs LinBPQ keeps in its tmpfs (the session
# record and the connect log) and writes a small summary into the in-memory volume the dashboard reads (docs/winlink.md).
#
#   --check   (the container's health check) the admin page is answering
#   (for tests: MOR_WINLINK_RENDER_ONLY=1 writes the configuration to MOR_WINLINK_CONFIG and stops)
set -eu

CONFIG="${MOR_WINLINK_CONFIG:-/run/bpq/bpq32.cfg}"

if [ "${1:-}" = "--check" ]; then
  # The admin page's port (8080 = 1F90) is listening: LinBPQ only opens it once its configuration loaded.
  awk '$4 == "0A" && $2 ~ /:1F90$/ { found = 1 } END { exit !found }' /proc/net/tcp /proc/net/tcp6 2>/dev/null
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

fail() { printf 'winlink: %s\n' "$*" >&2; exit 2; }
note() { printf 'winlink: %s\n' "$*"; }

need() { eval "[ -n \"\${$1:-}\" ]" || fail "$1 is required (winlink.env, see docs/winlink.md)"; }
# matches NAME PATTERN MESSAGE: the value of NAME must match the extended regex whole
matches() {
  eval "value=\${$1:-}"
  ok "$value" "$2" || fail "$1 $3"
}

# --- who the station is ----------------------------------------------------------------------------------------------

need MOR_WINLINK_CALL
CALL="$(printf '%s' "$MOR_WINLINK_CALL" | tr 'a-z' 'A-Z')"
ok "$CALL" '[A-Z0-9]{3,7}' || fail "MOR_WINLINK_CALL is not a callsign (letters and digits, 3 to 7)"
# The gateway is an amateur station. A GMRS call (four letters then three or four digits, like WQAB123) is not one, and a
# general email gateway is not what GMRS allows (docs/winlink.md). This is a guard against a mistake, not a licence check.
if ok "$CALL" '[A-Z]{3,4}[0-9]{3,4}'; then
  fail "MOR_WINLINK_CALL looks like a GMRS callsign. The Winlink gateway is for amateur stations only"
fi
case "${MOR_WINLINK_SERVICE:-amateur}" in
  amateur) ;;
  *) fail "MOR_WINLINK_SERVICE must be amateur (or left out): the gateway is not offered on GMRS" ;;
esac
SSID="${MOR_WINLINK_SSID:-10}"
ok "$SSID" '([1-9]|1[0-5])' || fail "MOR_WINLINK_SSID must be 1 to 15"
GATEWAY="$CALL-$SSID"

need MOR_WINLINK_LOCATOR
LOCATOR="$(printf '%s' "$MOR_WINLINK_LOCATOR" | tr 'a-z' 'A-Z')"
ok "$LOCATOR" '[A-R]{2}[0-9]{2}([A-X]{2})?' || fail "MOR_WINLINK_LOCATOR is not a grid square (like FN31pr)"

# --- the modem, and the admin login ----------------------------------------------------------------------------------

# winlink.env's MOR_WINLINK_KISS_HOST (a hardware TNC) wins; else the modem container's fixed address, which compose.yaml passes
# as MOR_DIREWOLF_IP; else its name, which is looked up below.
KISS_HOST="${MOR_WINLINK_KISS_HOST:-${MOR_DIREWOLF_IP:-direwolf}}"
KISS_PORT="${MOR_WINLINK_KISS_PORT:-8001}"
ok "$KISS_HOST" '[A-Za-z0-9._-]{1,253}' || fail "MOR_WINLINK_KISS_HOST is not a host name or address"
ok "$KISS_PORT" '[0-9]{1,5}' && [ "$KISS_PORT" -ge 1 ] && [ "$KISS_PORT" -le 65535 ] || fail "MOR_WINLINK_KISS_PORT is not a port"

# LinBPQ takes the modem's address as an IP and cannot look a name up (a name leaves it connecting to 255.255.255.255). So a
# name is looked up here, waiting a little for the modem's container to be there, and the gateway does not start without one.
# (compose.yaml gives the modem a fixed address and names that, so a recreated modem is found again at the same place.)
if ! ok "$KISS_HOST" '[0-9]{1,3}(\.[0-9]{1,3}){3}'; then
  GETENT="${MOR_WINLINK_GETENT:-getent}"
  tries=0
  KISS_ADDRESS=""
  while [ -z "$KISS_ADDRESS" ]; do
    KISS_ADDRESS="$("$GETENT" ahostsv4 "$KISS_HOST" 2>/dev/null | awk 'NR == 1 { print $1 }')"
    [ -n "$KISS_ADDRESS" ] && break
    tries=$((tries + 1))
    [ "$tries" -lt "${MOR_WINLINK_RESOLVE_TRIES:-30}" ] || fail "MOR_WINLINK_KISS_HOST ($KISS_HOST) has no address; is the modem running?"
    sleep 2
  done
  KISS_HOST="$KISS_ADDRESS"
fi

# The admin page and the telnet port answer on the private network only (nothing is published), and the page asks for this
# login from anywhere but the container itself. There is no default: a published default login is no login.
need MOR_WINLINK_ADMIN_PASSWORD
matches MOR_WINLINK_ADMIN_PASSWORD '[A-Za-z0-9._-]{12,64}' "must be 12 to 64 letters, digits, dot, underscore or dash"

# --- Winlink's side --------------------------------------------------------------------------------------------------

CMS=0
if [ -n "${MOR_WINLINK_CMS_PASSWORD:-}" ]; then
  # No ';' (it starts a comment in this file), no spaces or quotes.
  matches MOR_WINLINK_CMS_PASSWORD '[A-Za-z0-9._+=!@#%^*-]{1,32}' "has a character the gateway's file cannot hold (letters, digits and . _ + = ! @ # % ^ * -)"
  CMS=1
fi
REPORT="${MOR_WINLINK_REPORT:-0}"
case "$REPORT" in 0|1) ;; *) fail "MOR_WINLINK_REPORT must be 0 or 1" ;; esac
FREQ="${MOR_WINLINK_FREQ_HZ:-}"
if [ "$REPORT" = 1 ]; then
  [ "$CMS" = 1 ] || fail "MOR_WINLINK_REPORT=1 lists the gateway on Winlink, which needs MOR_WINLINK_CMS_PASSWORD"
  ok "$FREQ" '[0-9]{8,10}' || fail "MOR_WINLINK_FREQ_HZ (the channel's frequency in hertz) is needed to list the gateway"
fi
POWER="${MOR_WINLINK_POWER_W:-10}"; HEIGHT="${MOR_WINLINK_ANTENNA_HEIGHT_FT:-20}"; GAIN="${MOR_WINLINK_ANTENNA_GAIN_DB:-0}"; DIRECTION="${MOR_WINLINK_ANTENNA_DIRECTION:-0}"
for pair in "POWER:$POWER" "HEIGHT:$HEIGHT" "GAIN:$GAIN" "DIRECTION:$DIRECTION"; do
  ok "${pair#*:}" '[0-9]{1,4}' || fail "the antenna and power settings (MOR_WINLINK_POWER_W and the ANTENNA ones) must be whole numbers"
done

# --- the file ---------------------------------------------------------------------------------------------------------

TELNET_PORT=2  # the port number of the telnet port in the file below, and of the application's "C n CMS"
umask 077
{
  # Two calls, on purpose: the node answers as the bare call and the Winlink application as CALL-SSID. With both on one call a
  # connection reaches the node and never the application. ENABLEADIFLOG writes one record for each session that reached Winlink,
  # and LogAllConnects one line for each connection accepted, both in LinBPQ's log folder (a tmpfs): what the dashboard's sessions
  # and counters are made from.
  printf 'SIMPLE\nLOCATOR=%s\nNODECALL=%s\nNODEALIAS=MOR\nENABLEADIFLOG=1\nLogAllConnects=1\n\n' "$LOCATOR" "$CALL"
  printf 'PORT\n PORTNUM=1\n ID=Dire Wolf 1200\n TYPE=ASYNC\n PROTOCOL=KISS\n IPADDR=%s\n TCPPORT=%s\n CHANNEL=A\n' "$KISS_HOST" "$KISS_PORT"
  printf ' MAXFRAME=2\n FRACK=7000\n RESPTIME=1000\n RETRIES=10\n PACLEN=236\n TXDELAY=300\n SLOTTIME=100\n PERSIST=64\n'
  [ "$REPORT" = 0 ] || printf ' WL2KREPORT PUBLIC, www.winlink.org, 8778, %s, %s, 00-23, %s, PKT1200, %s, %s, %s, %s\n' \
    "$GATEWAY" "$LOCATOR" "$FREQ" "$POWER" "$HEIGHT" "$GAIN" "$DIRECTION"
  printf 'ENDPORT\n\n'
  printf 'PORT\n PORTNUM=%s\n DRIVER=TELNET\n CONFIG\n LOGGING=1\n DisconnectOnClose=1\n HTTPPORT=8080\n TCPPORT=8010\n' "$TELNET_PORT"
  if [ "$CMS" = 1 ]; then
    printf ' CMS=1\n CMSCALL=%s\n CMSPASS=%s\n' "$CALL" "$MOR_WINLINK_CMS_PASSWORD"
  fi
  printf ' USER=sysop,%s,%s,"",SYSOP\nENDPORT\n\n' "$MOR_WINLINK_ADMIN_PASSWORD" "$CALL"
  printf 'APPLICATION 1,RMS,C %s CMS,%s\n' "$TELNET_PORT" "$GATEWAY"  # "C n CMS": n is the telnet port, which holds the CMS link
} > "$CONFIG"

if [ "$CMS" = 1 ]; then
  note "gateway $GATEWAY, modem at $KISS_HOST:$KISS_PORT, Winlink CMS on$([ "$REPORT" = 1 ] && printf ', listed on Winlink')"
else
  note "gateway $GATEWAY, modem at $KISS_HOST:$KISS_PORT, Winlink CMS OFF: MOR_WINLINK_CMS_PASSWORD is not set, so nothing can reach Winlink"
fi
[ "${MOR_WINLINK_RENDER_ONLY:-0}" = 1 ] && exit 0

cd "$(dirname "$CONFIG")"

# Sessions and counters for the dashboard. The reporter reads only what is in LinBPQ's log folder and writes only into the status
# folder; if that is not there or there is no python, the gateway runs the same and says so.
STATUS_DIR="${MOR_WINLINK_STATUS_DIR:-/winlink-status}"
REPORTER="${MOR_WINLINK_REPORTER:-/usr/local/bin/winlink-reporter.py}"
if [ -d "$STATUS_DIR" ] && [ -f "$REPORTER" ] && command -v python3 >/dev/null 2>&1; then
  python3 "$REPORTER" gateway --logs "$PWD/logs" --out "$STATUS_DIR/gateway.json" --interval "${MOR_WINLINK_SCAN_SECONDS:-5}" --parent $$ &
else
  note "sessions and counters are off: no status folder or no python here (the gateway runs the same)"
fi
exec "${MOR_WINLINK_BIN:-/usr/local/bin/linbpq}"
