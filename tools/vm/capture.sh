#!/bin/bash
# R6: run on the client VM as root. Captures one complete connection of our
# client with tcpdump (on all interfaces, since requests leave via LAN A and
# replies arrive via Link D) and prints it.
#   sudo bash tools/vm/capture.sh [URL]    (default: photo.jpg from our server)
REPO=$(cd "$(dirname "$0")/../.." && pwd)
url=${1:-http://10.10.3.10:8080/photo.jpg}
T=/var/tmp/a3
mkdir -p "$T"
rm -f "$T/conn.pcap"

tcpdump -nn -i any -w "$T/conn.pcap" 'tcp and host 10.10.3.10 and portrange 61000-65535' \
    2>"$T/tcpdump.err" &
pid=$!
sleep 2                             # let tcpdump start listening
"$REPO/client/run-client" "$url" -o "$T/capture.out" --log "$T/capture.log"
echo "client exit code: $?"
sleep 2                             # let the last packets reach tcpdump
kill "$pid"
wait "$pid" 2>/dev/null

n=$(tcpdump -nn -r "$T/conn.pcap" 2>/dev/null | wc -l)
if [ "$n" -eq 0 ]; then
    echo "tcpdump captured nothing; its messages:"
    cat "$T/tcpdump.err"
    exit 1
fi
tcpdump -nn -r "$T/conn.pcap" 2>/dev/null | head -40
echo "... $n packets in $T/conn.pcap (packet log: $T/capture.log)"
