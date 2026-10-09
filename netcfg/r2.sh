#!/bin/bash
# Part A: configure router r2.
# Usage: r2.sh IF_B IF_C IF_D
#   IF_B  interface on LAN B  (10.10.2.2/24, towards r1)
#   IF_C  interface on LAN C  (10.10.3.1/24, towards the server)
#   IF_D  interface on Link D (10.10.4.1/30, towards the client)
set -e

if [ $# -ne 3 ]; then
    echo "usage: $0 IF_B IF_C IF_D" >&2
    exit 1
fi
IF_B=$1
IF_C=$2
IF_D=$3

# A4: r2 is a router.
sysctl -w net.ipv4.ip_forward=1

ip -4 addr flush dev "$IF_B"
ip addr add 10.10.2.2/24 dev "$IF_B"
ip link set "$IF_B" up

ip -4 addr flush dev "$IF_C"
ip addr add 10.10.3.1/24 dev "$IF_C"
ip link set "$IF_C" up

ip -4 addr flush dev "$IF_D"
ip addr add 10.10.4.1/30 dev "$IF_D"
ip link set "$IF_D" up

# A6: no default route, only explicit ones.
# LAN A lives behind r1 ...
ip route replace 10.10.1.0/24 via 10.10.2.1 dev "$IF_B"
# ... except the client itself. A3 requires server -> client traffic to take
# Link D, so this more specific host route sends packets for 10.10.1.10
# straight to the client's Link D address. A route for all of 10.10.1.0/24
# via Link D would not work: the client is a host and would not forward
# packets for r1's 10.10.1.1, breaking A1.
ip route replace 10.10.1.10/32 via 10.10.4.2 dev "$IF_D"

# A7: client -> server packets (source 10.10.1.10) arrive on IF_B from r1,
# but r2's route back to 10.10.1.10 is via IF_D. Strict mode would drop
# them, so IF_B uses loose mode. IF_C and IF_D stay strict: packets
# arriving there come from the attached subnets.
sysctl -w "net/ipv4/conf/$IF_B/rp_filter=2"
sysctl -w "net/ipv4/conf/$IF_C/rp_filter=1"
sysctl -w "net/ipv4/conf/$IF_D/rp_filter=1"
