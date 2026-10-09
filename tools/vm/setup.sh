#!/bin/bash
# Run on a VM as root after each boot: reset rp_filter to strict on every
# interface (the assignment's starting state), then run that machine's
# netcfg script with the interface names of our Fusion VMs.
#   sudo bash tools/vm/setup.sh client|r1|r2|server [IF ...]
# Pass interface names explicitly to override the built-in map.
set -e
cd "$(dirname "$0")/../.."
role=$1
shift || true
if [ $# -eq 0 ]; then
    case $role in
        client) set -- enp2s0 enp26s0 ;;          # LAN A, Link D
        r1)     set -- enp2s0 enp26s0 ;;          # LAN A, LAN B
        r2)     set -- enp2s0 enp26s0 enp3s0 ;;   # LAN B, LAN C, Link D
        server) set -- enp2s0 ;;                  # LAN C
        *) echo "usage: $0 client|r1|r2|server [IF ...]" >&2; exit 1 ;;
    esac
fi
for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 1 > "$f"; done
bash "netcfg/$role.sh" "$@"
echo "== configured $role"
ip -br addr
ip route
