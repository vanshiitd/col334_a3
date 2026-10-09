#!/bin/bash
# T14: run on the client VM as root, with tools/vm/servers.sh on the server
# and `shape.sh fair` on r2. Starts N_OURS downloads (our client <- our
# servers on ports 9001..) and N_CURL downloads (curl <- python http.server)
# of the same file at the same moment and prints when each one finishes.
# With a fair share, all finish at about the same time.
#   sudo bash tools/vm/fairness.sh N_OURS N_CURL [FILE]   (N_OURS <= 3)
REPO=$(cd "$(dirname "$0")/../.." && pwd)
S=10.10.3.10
n_ours=${1:-1}
n_curl=${2:-1}
file=${3:-huge.bin}
OUT=/var/tmp/a3/fair
mkdir -p "$OUT"
rm -f "$OUT"/*

start=$(date +%s.%N)
elapsed() { awk "BEGIN { printf \"%.1f\", $(date +%s.%N) - $start }"; }
for i in $(seq 1 "$n_ours"); do
    ( "$REPO/client/run-client" "http://$S:900$i/$file" -o "$OUT/ours$i" 2>/dev/null
      echo "ours$i  exit=$?  finished at $(elapsed) s" ) &
done
for i in $(seq 1 "$n_curl"); do
    ( curl -s -o "$OUT/curl$i" "http://$S:8000/$file"
      echo "curl$i  exit=$?  finished at $(elapsed) s" ) &
done
wait
md5sum "$OUT"/* | awk '{print $1}' | sort | uniq -c | sed 's/^/md5 count: /'
