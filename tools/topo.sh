#!/bin/bash
# Development helper (not part of the submission): rebuild the assignment
# topology on one Linux box with network namespaces, so netcfg/ and the
# programs can be tested without the VMs.
#
#   sudo tools/topo.sh up      create namespaces client, r1, r2, server and
#                              run netcfg/*.sh inside them
#   sudo tools/topo.sh down    delete them
#
# Run a command on a "machine" with:  ip netns exec client <cmd>
#
# Interface names mirror the LANs: cA/cD on the client, r1A/r1B on r1,
# r2B/r2C/r2D on r2, sC on the server.
# Set OFFLOAD=1 to keep veth checksum offload on (the default here is off,
# so packets carry real checksums, as on the wire between VMs).
set -e
cd "$(dirname "$0")/.."

NS="client r1 r2 server"

down() {
    for ns in $NS; do ip netns del "$ns" 2>/dev/null || true; done
}

link() {  # link NS1 IF1 NS2 IF2
    ip link add "$2" netns "$1" type veth peer name "$4" netns "$3"
    if [ "${OFFLOAD:-0}" != 1 ]; then
        ip netns exec "$1" ethtool -K "$2" tx off >/dev/null 2>&1 || true
        ip netns exec "$3" ethtool -K "$4" tx off >/dev/null 2>&1 || true
    fi
}

up() {
    down
    for ns in $NS; do
        ip netns add "$ns"
        ip -n "$ns" link set lo up
        # Ubuntu's default; this box may default to something else (BBR).
        ip netns exec "$ns" sysctl -qw net.ipv4.tcp_congestion_control=cubic 2>/dev/null || true
    done
    link client cA r1 r1A        # LAN A
    link r1 r1B r2 r2B           # LAN B
    link r2 r2C server sC        # LAN C
    link client cD r2 r2D        # Link D

    # Starting state required by the assignment: strict rp_filter everywhere.
    for ns in $NS; do
        ip netns exec "$ns" sh -c \
            'for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 1 > "$f"; done'
    done

    ip netns exec client bash netcfg/client.sh cA cD >/dev/null
    ip netns exec r1 bash netcfg/r1.sh r1A r1B >/dev/null
    ip netns exec r2 bash netcfg/r2.sh r2B r2C r2D >/dev/null
    ip netns exec server bash netcfg/server.sh sC >/dev/null
    echo "topology up"
}

case "$1" in
    up) up ;;
    down) down ;;
    *) echo "usage: $0 up|down" >&2; exit 1 ;;
esac
