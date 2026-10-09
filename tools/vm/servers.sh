#!/bin/bash
# Run on the server VM as root: (re)create /var/tmp/a3/www and (re)start
#   python3 -m http.server on 8000    (the "standard web server")
#   our server on 8080 with a packet log, and on 9001-9003 without one
#   sudo bash tools/vm/servers.sh          start (or restart) everything
#   sudo bash tools/vm/servers.sh stop     stop everything
# stderr of each server and the 8080 packet log go to /var/tmp/a3/.
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LOGS=/var/tmp/a3
WWW=$LOGS/www

pkill -f 'http_server.py' 2>/dev/null
pkill -f 'http.server 8000 --directory' 2>/dev/null
sleep 0.5
[ "$1" = stop ] && { echo "stopped"; exit 0; }

mkdir -p "$LOGS"
[ -f "$WWW/huge.bin" ] || bash "$REPO/tools/vm/make_www.sh" "$WWW" >/dev/null
nohup python3 -m http.server 8000 --directory "$WWW" >"$LOGS/python8000.err" 2>&1 &
nohup "$REPO/server/run-server" -p 8080 -d "$WWW" --log "$LOGS/server8080.log" \
    >"$LOGS/server8080.err" 2>&1 &
for p in 9001 9002 9003; do
    nohup "$REPO/server/run-server" -p $p -d "$WWW" >"$LOGS/server$p.err" 2>&1 &
done
sleep 1
ps -eo pid,args | grep -E 'http_serve[r].py|http.serve[r] 8000' | grep -v grep
echo "serving $WWW; logs in $LOGS"
