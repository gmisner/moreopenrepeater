#!/bin/sh
# Makes direwolf.env, the packet modem's own settings, from winlink.env (docs/winlink.md).
#
#   cd docker && sh ./split-winlink-env.sh
#
# Dire Wolf decodes whatever it hears on the air and holds an audio device, so compose.yaml gives it direwolf.env and never
# winlink.env, which also holds the gateway's admin password and the Winlink password. This copies the five lines the modem
# uses (MOR_WINLINK_CALL, MOR_WINLINK_SSID, MOR_PACKET_PTT, MOR_PACKET_PTT_GPIO, MOR_WINLINK_SCAN_SECONDS) and nothing else.
#
# It only fills in a direwolf.env that is not there yet, so an owner who already has a winlink.env keeps working after an update
# (setup.sh and update.sh run this), and it never overwrites a direwolf.env you made or edited. After you change one of those
# lines in winlink.env later, change it in direwolf.env too (it is a copy), then `docker compose --profile winlink up -d`.
set -eu

cd "$(dirname "$0")"

[ -f winlink.env ] || exit 0
[ ! -e direwolf.env ] || exit 0

(
  umask 077
  {
    echo "# The packet modem's settings, copied from winlink.env by split-winlink-env.sh. No passwords belong here."
    grep -E '^(MOR_WINLINK_CALL|MOR_WINLINK_SSID|MOR_PACKET_PTT|MOR_PACKET_PTT_GPIO|MOR_WINLINK_SCAN_SECONDS)=' winlink.env || true
  } > direwolf.env.new
)
mv direwolf.env.new direwolf.env
chmod 600 direwolf.env
echo "  made direwolf.env (the packet modem's own settings: no passwords) from winlink.env"
