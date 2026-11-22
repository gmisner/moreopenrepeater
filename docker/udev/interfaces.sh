#!/bin/sh
# The repeater's and the link radio's USB interfaces, told apart (#221). Used by ../setup.sh; runs on the host.
#
#   sh udev/interfaces.sh list            the CM108 and AIOC interfaces plugged in, with the serial number and the USB
#                                         port each one is on
#   sh udev/interfaces.sh rule ID         the udev rule that gives the interface ID the name /dev/moreopenrepeater-link
#   sh udev/interfaces.sh rule ID packet  the same for the Winlink packet radio's interface: /dev/moreopenrepeater-packet
#
# ID is a serial number as `list` shows it, or `port:1-1.3` for an interface that has none (many CM108 dongles do not,
# and two of the same model can share one). A port names the socket, so the interface must stay in that socket.
#
# Two interfaces cannot both be /dev/moreopenrepeater-ptt, so the link radio's (or the packet radio's) is named first and the
# rule for the repeater's name (61-moreopenrepeater-ptt.rules) skips an interface that one of them has claimed. Any CM108 or
# AIOC interface that is not the repeater's needs one of these, whether or not its PTT is used: the repeater's rule names every
# one it is not told to leave out.
set -eu

SYS="${MOR_SYS_ROOT:-/sys}"

# interfaces: one line per interface, "hidrawN vendor product serial port" with - for what it does not have
interfaces() {
  for node in "$SYS"/class/hidraw/hidraw*; do
    [ -e "$node" ] || continue
    name="$(basename "$node")"
    dir="$(cd -P "$node/device" 2>/dev/null && pwd -P)" || continue
    while [ "$dir" != "/" ] && [ ! -f "$dir/idVendor" ]; do dir="$(dirname "$dir")"; done
    [ -f "$dir/idVendor" ] || continue
    vendor="$(cat "$dir/idVendor")"
    product="$(cat "$dir/idProduct" 2>/dev/null || echo -)"
    case "$vendor:$product" in 1209:7388|0d8c:*) ;; *) continue ;; esac
    serial="$(cat "$dir/serial" 2>/dev/null || true)"
    printf '%s %s %s %s %s\n' "$name" "$vendor" "$product" "${serial:--}" "$(basename "$dir")"
  done
}

case "${1:-}" in
  list)
    found="$(interfaces)"
    if [ -z "$found" ]; then
      echo "No CM108 or AIOC interface is plugged in."
      exit 0
    fi
    printf '%s\n' "$found" | while read -r name vendor product serial port; do
      kind="CM108/CM119 ($vendor:$product)"
      [ "$vendor" != "1209" ] || kind="AIOC"
      if [ "$serial" = "-" ]; then
        echo "  /dev/$name  $kind  no serial number  on USB port $port  ->  --link-radio port:$port  (or --packet-radio port:$port)"
      elif [ "$(printf '%s\n' "$found" | awk -v s="$serial" '$4 == s' | wc -l | tr -d ' ')" -gt 1 ]; then
        echo "  /dev/$name  $kind  serial $serial (another interface has the same one)  on USB port $port  ->  --link-radio port:$port  (or --packet-radio port:$port)"
      else
        echo "  /dev/$name  $kind  serial $serial  on USB port $port  ->  --link-radio $serial  (or --packet-radio $serial)"
      fi
    done
    ;;
  rule)
    id="${2:-}"
    kind="${3:-link}"
    case "$kind" in
      link) who="the link radio's"; name="moreopenrepeater-link"; mark="MOREOPENREPEATER_LINK"; flag="--link-radio" ;;
      packet) who="the Winlink packet radio's"; name="moreopenrepeater-packet"; mark="MOREOPENREPEATER_PACKET"; flag="--packet-radio" ;;
      *) echo "interfaces: rule's kind is link or packet, not '$kind'" >&2; exit 2 ;;
    esac
    case "$id" in
      port:*)
        port="${id#port:}"
        printf '%s' "$port" | grep -Eq '^[0-9]+-[0-9]+(\.[0-9]+)*$' || { echo "interfaces: '$port' is not a USB port like 1-1.3" >&2; exit 2; }
        match="KERNELS==\"$port\""
        ;;
      "")
        echo "interfaces: rule needs the interface's serial number or port:USB-PORT" >&2
        exit 2
        ;;
      *)
        printf '%s' "$id" | grep -Eq '^[A-Za-z0-9._:-]+$' || { echo "interfaces: '$id' does not look like a serial number (letters, digits and . _ : - only)" >&2; exit 2; }
        match="ATTRS{serial}==\"$id\""
        ;;
    esac
    cat <<RULES
# Written by docker/setup.sh $flag: names $who interface $id /dev/$name.
# It must sort before 61-moreopenrepeater-ptt.rules, which then leaves this interface out of the repeater's name.
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7388", $match, GROUP="audio", MODE="0660", SYMLINK+="$name", ENV{$mark}="1"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0d8c", $match, GROUP="audio", MODE="0660", SYMLINK+="$name", ENV{$mark}="1"
RULES
    ;;
  *)
    echo "usage: interfaces.sh list | rule ID [link|packet]" >&2
    exit 2
    ;;
esac
