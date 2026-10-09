#!/bin/bash
# Part A check: run on any VM once all four are configured (no root
# needed). Pings every address on LANs A-C (plus the Link D peer on the
# client and r2), and on the client/server runs the traceroute for R2.
has() { ip -4 -o addr show | grep -q " $1/"; }

echo "== $(hostname)"
ip -br addr
ip route
echo "ip_forward=$(sysctl -n net.ipv4.ip_forward)"
echo "rp_filter: $(grep . /proc/sys/net/ipv4/conf/*/rp_filter |
    sed 's|/proc/sys/net/ipv4/conf/||; s|/rp_filter||' | tr '\n' ' ')"

targets="10.10.1.10 10.10.1.1 10.10.2.1 10.10.2.2 10.10.3.1 10.10.3.10"
has 10.10.4.2 && targets="$targets 10.10.4.1"
has 10.10.4.1 && targets="$targets 10.10.4.2"
fail=0
for a in $targets; do
    if ping -c1 -W1 "$a" >/dev/null 2>&1; then echo "ok   ping $a"; else echo "FAIL ping $a"; fail=1; fi
done
has 10.10.1.10 && traceroute -n -q1 -w1 10.10.3.10
has 10.10.3.10 && traceroute -n -q1 -w1 10.10.1.10
[ $fail = 0 ] && echo "ALL PINGS OK" || echo "SOME PINGS FAILED"
