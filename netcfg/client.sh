#!/bin/bash
# Part A: configure the client.
# Usage: client.sh IF_A IF_D
#   IF_A  interface on LAN A  (10.10.1.10/24, towards r1)
#   IF_D  interface on Link D (10.10.4.2/30,  towards r2)
set -e

if [ $# -ne 2 ]; then
    echo "usage: $0 IF_A IF_D" >&2
    exit 1
fi
IF_A=$1
IF_D=$2

# A4: the client is a host and must not forward packets.
sysctl -w net.ipv4.ip_forward=0

ip -4 addr flush dev "$IF_A"
ip addr add 10.10.1.10/24 dev "$IF_A"
ip link set "$IF_A" up

ip -4 addr flush dev "$IF_D"
ip addr add 10.10.4.2/30 dev "$IF_D"
ip link set "$IF_D" up

# A1/A2: LANs B and C are reached through r1, so client -> server traffic
# goes client -> r1 -> r2 -> server. Link D is only used to talk to r2's
# 10.10.4.1 directly (connected route).
ip route replace 10.10.2.0/24 via 10.10.1.1 dev "$IF_A"
ip route replace 10.10.3.0/24 via 10.10.1.1 dev "$IF_A"

# A3/A7: replies from LANs B and C come back over Link D (r2 sends them
# straight to us), but our route back to those sources points at LAN A.
# Strict reverse-path filtering on IF_D would drop them, so IF_D uses loose
# mode (2: the source only has to be reachable through some interface).
# IF_A stays strict. The "/" form keeps interface names containing dots
# working.
sysctl -w "net/ipv4/conf/$IF_A/rp_filter=1"
sysctl -w "net/ipv4/conf/$IF_D/rp_filter=2"
