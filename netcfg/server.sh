#!/bin/bash
# Part A: configure the server.
# Usage: server.sh IF_C
#   IF_C  interface on LAN C (10.10.3.10/24, towards r2)
set -e

if [ $# -ne 1 ]; then
    echo "usage: $0 IF_C" >&2
    exit 1
fi
IF_C=$1

# A4: the server is a host and must not forward packets.
sysctl -w net.ipv4.ip_forward=0

ip -4 addr flush dev "$IF_C"
ip addr add 10.10.3.10/24 dev "$IF_C"
ip link set "$IF_C" up

# A1/A3: LANs A and B are reached through r2 (r2 delivers packets for the
# client over Link D).
ip route replace 10.10.1.0/24 via 10.10.3.1 dev "$IF_C"
ip route replace 10.10.2.0/24 via 10.10.3.1 dev "$IF_C"

# A7: rp_filter must stay strict on the server. All traffic enters on IF_C,
# which is also the way back to every source.
sysctl -w "net/ipv4/conf/$IF_C/rp_filter=1"
