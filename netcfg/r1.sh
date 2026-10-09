#!/bin/bash
# Part A: configure router r1.
# Usage: r1.sh IF_A IF_B
#   IF_A  interface on LAN A (10.10.1.1/24, towards the client)
#   IF_B  interface on LAN B (10.10.2.1/24, towards r2)
set -e

if [ $# -ne 2 ]; then
    echo "usage: $0 IF_A IF_B" >&2
    exit 1
fi
IF_A=$1
IF_B=$2

# A4: r1 is a router.
sysctl -w net.ipv4.ip_forward=1

ip -4 addr flush dev "$IF_A"
ip addr add 10.10.1.1/24 dev "$IF_A"
ip link set "$IF_A" up

ip -4 addr flush dev "$IF_B"
ip addr add 10.10.2.1/24 dev "$IF_B"
ip link set "$IF_B" up

# A5: everything r1 is not attached to (LAN C, Link D) is behind r2, so a
# single default route via r2 covers it. The only other routes are the two
# connected ones the kernel adds for LANs A and B.
ip route replace default via 10.10.2.2 dev "$IF_B"

# A7: rp_filter must stay strict on r1. Every packet r1 receives arrives on
# the interface it would use to reach the source (client side on IF_A,
# everything else via r2 on IF_B), so strict mode drops nothing legitimate.
sysctl -w "net/ipv4/conf/$IF_A/rp_filter=1"
sysctl -w "net/ipv4/conf/$IF_B/rp_filter=1"
