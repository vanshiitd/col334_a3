#!/bin/bash
# R6: run on the client VM as root. Captures one complete connection of our
# client with tcpdump (on all interfaces, since requests leave via LAN A and
# replies arrive via Link D) and prints it.
#   sudo bash tools/vm/capture.sh [URL]    (default: photo.jpg from our server)
REPO=$(cd "$(dirname "$0")/../.." && pwd)
url=${1:-http://10.10.3.10:8080/photo.jpg}
T=/var/tmp/a3
mkdir -p "$T"

# -Z root: stay root instead of switching to the unprivileged "tcpdump"
# user, so writing the capture never depends on that user's permissions.
tcpdump -Z root -nn -i any -w "$T/conn.pcap" 'tcp and host 10.10.3.10 and portrange 61000-65535' \
    2>/dev/null &
pid=$!
sleep 1
"$REPO/client/run-client" "$url" -o "$T/capture.out" --log "$T/capture.log"
echo "client exit code: $?"
sleep 1
kill "$pid"
wait "$pid" 2>/dev/null
tcpdump -nn -r "$T/conn.pcap" 2>/dev/null | head -40
echo "... $(tcpdump -nn -r "$T/conn.pcap" 2>/dev/null | wc -l) packets in $T/conn.pcap (packet log: $T/capture.log)"
