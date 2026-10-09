#!/bin/bash
# Development helper: check the Part A requirements on the namespace
# topology built by tools/topo.sh (pings + traceroutes).
# On the VMs, run the same ping/traceroute commands by hand.

ADDRS_ABC="10.10.1.10 10.10.1.1 10.10.2.1 10.10.2.2 10.10.3.1 10.10.3.10"
fail=0

ping_ok() {  # ping_ok NS ADDR
    if ip netns exec "$1" ping -c1 -W1 "$2" >/dev/null 2>&1; then
        echo "ok   $1 -> $2"
    else
        echo "FAIL $1 -> $2"
        fail=1
    fi
}

echo "== A1: every machine reaches every address on LANs A, B, C"
for ns in client r1 r2 server; do
    for a in $ADDRS_ABC; do ping_ok "$ns" "$a"; done
done
echo "== A1: client <-> r2 over Link D"
ping_ok client 10.10.4.1
ping_ok r2 10.10.4.2

echo "== A2: client -> server (expect r1, then r2's Link D address, then server)"
ip netns exec client traceroute -n -q1 -w1 10.10.3.10
echo "== A3: server -> client (expect r2, then client)"
ip netns exec server traceroute -n -q1 -w1 10.10.1.10

echo "== A4: forwarding"
for ns in client r1 r2 server; do
    echo "$ns: $(ip netns exec "$ns" sysctl -n net.ipv4.ip_forward)"
done

[ $fail = 0 ] && echo "ALL PINGS OK" || echo "SOME PINGS FAILED"
exit $fail
