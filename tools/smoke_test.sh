#!/bin/bash
# Development helper: end-to-end check on a fresh namespace topology.
# Builds the topology, checks Part A, then downloads files with our client,
# curl and wget from python's http.server and from our server, with and
# without 5% random loss on both routers, and compares md5sums.
#   sudo tools/smoke_test.sh
cd "$(dirname "$0")/.."
REPO=$PWD
WORK=$(mktemp -d)
trap 'kill $(cat "$WORK"/*.pid 2>/dev/null) 2>/dev/null; rm -rf "$WORK"' EXIT

tools/topo.sh up || exit 1
tools/check_network.sh >"$WORK/net.txt" || { cat "$WORK/net.txt"; exit 1; }
echo "part A: ok"

mkdir -p "$WORK/www/dir"
echo '<html><body>index</body></html>' >"$WORK/www/index.html"
echo 'nested' >"$WORK/www/dir/index.html"
head -c 2000000 /dev/urandom >"$WORK/www/big.bin"

ip netns exec server python3 -m http.server 8000 --directory "$WORK/www" \
    >/dev/null 2>&1 & echo $! >"$WORK/py.pid"
ip netns exec server "$REPO/server/run-server" -p 8080 -d "$WORK/www" \
    >"$WORK/server.err" 2>&1 & echo $! >"$WORK/srv.pid"
sleep 1

fail=0
check() {  # check NAME FILE ORIGINAL
    if cmp -s "$2" "$3"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
round() {
    for f in index.html big.bin; do
        ip netns exec client "$REPO/client/run-client" "http://10.10.3.10:8000/$f" \
            -o "$WORK/c_py_$f" 2>/dev/null
        check "$1 ours <- python  $f (exit $?)" "$WORK/c_py_$f" "$WORK/www/$f"
        ip netns exec client "$REPO/client/run-client" "http://10.10.3.10:8080/$f" \
            -o "$WORK/c_us_$f" 2>/dev/null
        check "$1 ours <- ours    $f (exit $?)" "$WORK/c_us_$f" "$WORK/www/$f"
        ip netns exec client curl -s -o "$WORK/curl_$f" "http://10.10.3.10:8080/$f"
        check "$1 curl <- ours    $f" "$WORK/curl_$f" "$WORK/www/$f"
        ip netns exec client wget -q -O "$WORK/wget_$f" "http://10.10.3.10:8080/$f"
        check "$1 wget <- ours    $f" "$WORK/wget_$f" "$WORK/www/$f"
    done
}
round "clean"
ip netns exec r1 iptables -A FORWARD -p tcp -m statistic --mode random --probability 0.05 -j DROP
ip netns exec r2 iptables -A FORWARD -p tcp -m statistic --mode random --probability 0.05 -j DROP
round "5%loss"

[ $fail = 0 ] && echo "ALL OK" || echo "SOME CHECKS FAILED"
tools/topo.sh down
exit $fail
