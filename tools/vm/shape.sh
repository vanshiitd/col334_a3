#!/bin/bash
# Run on r1 or r2 as root to impair or shape traffic for T12 / T14 tests.
#   sudo bash tools/vm/shape.sh loss    T12: delay, 5% loss, reordering
#   sudo bash tools/vm/shape.sh fair    T14: 20 Mbit/s bottleneck (r2 only)
#   sudo bash tools/vm/shape.sh off     remove it
#   sudo bash tools/vm/shape.sh show    counters (drops etc.)
# r2 acts on its Link D interface (server -> client: the data), r1 on its
# LAN B interface (client -> server: requests and ACKs).
dev_of() { ip -4 -o addr show | awk -v a="$1/" 'index($4, a) == 1 {print $2}'; }

dev=$(dev_of 10.10.4.1)
role=r2
if [ -z "$dev" ]; then
    dev=$(dev_of 10.10.2.1)
    role=r1
fi
if [ -z "$dev" ]; then
    echo "run this on r1 or r2 after setup.sh" >&2
    exit 1
fi

case $1 in
    loss)
        if [ $role = r2 ]; then
            tc qdisc replace dev "$dev" root netem delay 10ms 5ms loss 5% reorder 25% 50%
        else
            tc qdisc replace dev "$dev" root netem delay 5ms loss 5%
        fi ;;
    fair)
        if [ $role = r2 ]; then
            tc qdisc replace dev "$dev" root tbf rate 20mbit burst 32kb latency 50ms
        else
            tc qdisc del dev "$dev" root 2>/dev/null
        fi ;;
    off)
        tc qdisc del dev "$dev" root 2>/dev/null ;;
    show)
        tc -s qdisc show dev "$dev"; exit 0 ;;
    *)
        echo "usage: $0 loss|fair|off|show" >&2; exit 1 ;;
esac
echo "$role $dev: $(tc qdisc show dev "$dev" | head -1)"
