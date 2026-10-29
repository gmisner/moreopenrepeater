#!/usr/bin/env bash
# Strip world access on ASL secrets and, on a fresh ASL3, keep its USB radio
# drivers from claiming the controller's CM108. Used by install-pi.sh --allstar.
#
# Usage: asl-harden.sh <asterisk-dir> <fresh:0|1>
set -euo pipefail

dir="${1:?asterisk config directory}"
fresh="${2:?fresh install flag 0 or 1}"

for file in manager.conf pjsip.conf; do
  if [ -f "$dir/$file" ]; then chmod o-rwx "$dir/$file"; fi
done

if [ "$fresh" -eq 1 ] && [ -f "$dir/modules.conf" ]; then
  # A new ASL3's placeholder node 1999 runs on chan_simpleusb, which would
  # open the controller's CM108. An existing install's drivers are its own.
  sed -i -E 's/^load([[:space:]]*=>?[[:space:]]*)(chan_simpleusb|chan_usbradio)\.so/noload\1\2.so/' \
    "$dir/modules.conf"
fi
