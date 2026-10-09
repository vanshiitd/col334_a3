#!/bin/bash
# Run on each VM (no root needed). Prints what the tests depend on:
# Python, iptables, kernel hold, NIC driver and offloads, netem availability,
# rp_filter state. Send the output if anything looks off.
echo "== $(hostname): $(uname -r) $(uname -m)"
echo "python3:  $(python3 --version 2>&1)"
echo "iptables: $(command -v iptables || echo MISSING)"
echo "ethtool:  $(command -v ethtool || echo MISSING)"
echo "tcpdump:  $(command -v tcpdump || echo MISSING)"
echo "netem:    $(modinfo -n sch_netem 2>/dev/null || echo 'MISSING (needed on r1/r2 only)')"
echo "tbf:      $(modinfo -n sch_tbf 2>/dev/null || echo 'MISSING (needed on r2 only)')"
echo "held:     $(apt-mark showhold | tr '\n' ' ')"
echo "tcp cc:   $(sysctl -n net.ipv4.tcp_congestion_control)"
for dev in /sys/class/net/*; do
    ifname=${dev##*/}
    [ "$ifname" = lo ] && continue
    echo "-- $ifname driver=$(ethtool -i "$ifname" 2>/dev/null | awk '/^driver/{print $2}')" \
         "mtu=$(cat "$dev/mtu") $(ethtool -k "$ifname" 2>/dev/null |
         grep -E '^(rx-checksumming|tx-checksumming|generic-receive-offload|large-receive-offload|tcp-segmentation-offload):' |
         sed 's/-offload//; s/-checksumming/csum/' | tr -d ' ' | tr '\n' ' ')"
done
echo "rp_filter: $(grep . /proc/sys/net/ipv4/conf/*/rp_filter |
    sed 's|/proc/sys/net/ipv4/conf/||; s|/rp_filter||' | tr '\n' ' ')"
