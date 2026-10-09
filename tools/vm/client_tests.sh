#!/bin/bash
# Run on the client VM as root while tools/vm/servers.sh runs on the server.
# Parts C, D and E: downloads with our client, curl and wget, compared with
# a reference copy generated locally by make_www.sh (same seeded content as
# the server's), plus exit codes and the server's error statuses.
#   sudo bash tools/vm/client_tests.sh
REPO=$(cd "$(dirname "$0")/../.." && pwd)
S=10.10.3.10
T=/var/tmp/a3
OUT=$T/client
REF=$T/ref
CLIENT=$REPO/client/run-client
mkdir -p "$OUT"
[ -f "$REF/huge.bin" ] || bash "$REPO/tools/vm/make_www.sh" "$REF" >/dev/null

fail=0
same() {    # same LABEL FILE REFERENCE
    if cmp -s "$2" "$3"; then
        echo "ok   $1  (md5 $(md5sum <"$2" | cut -c1-12))"
    else
        echo "FAIL $1  (differs from $3)"; fail=1
    fi
}
expect() {  # expect LABEL GOT WANT
    if [ "$2" = "$3" ]; then echo "ok   $1 = $2"; else echo "FAIL $1: got '$2', want '$3'"; fail=1; fi
}
header() {  # header URL NAME  -> value of response header NAME (HEAD request)
    curl -sI "$1" | tr -d '\r' | awk -F': ' -v n="$2" 'tolower($1) == n {print $2}'
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "== Part C: our client and curl <- python http.server (:8000)"
for f in index.html photo.jpg big.bin; do
    "$CLIENT" "http://$S:8000/$f" -o "$OUT/py_ours_$f" --log "$OUT/py_ours_$f.log" 2>/dev/null
    expect "our client exit code for $f" $? 0
    curl -s -o "$OUT/py_curl_$f" "http://$S:8000/$f"
    same "our client $f" "$OUT/py_ours_$f" "$REF/$f"
    same "curl       $f" "$OUT/py_curl_$f" "$REF/$f"
done
"$CLIENT" "http://$S:8000/index.html?q=tcp" -o "$OUT/py_query.html" 2>/dev/null
expect "exit code with query string" $? 0
"$CLIENT" "http://$S:8000/no-such-file" -o "$OUT/py_404" 2>/dev/null
expect "exit code for a 404" $? 1
"$CLIENT" "http://$S:9/" -o "$OUT/refused" 2>/dev/null
expect "exit code when refused" $? 2
"$CLIENT" "ftp://$S/" 2>/dev/null
expect "exit code for an invalid URL" $? 2

echo "== Parts D+E: curl, wget, our client <- our server (:8080)"
for f in index.html photo.jpg big.bin sub/index.html; do
    n=${f//\//_}
    curl -s -o "$OUT/curl_$n" "http://$S:8080/$f"
    same "curl       $f" "$OUT/curl_$n" "$REF/$f"
    wget -q -O "$OUT/wget_$n" "http://$S:8080/$f"
    same "wget       $f" "$OUT/wget_$n" "$REF/$f"
    "$CLIENT" "http://$S:8080/$f" -o "$OUT/ours_$n" --log "$OUT/ours_$n.log" 2>/dev/null
    expect "our client exit code for $f" $? 0
    same "our client $f" "$OUT/ours_$n" "$REF/$f"
done

echo "== Part D: status codes and headers"
expect "GET /sub/"              "$(code "http://$S:8080/sub/")" 200
expect "GET /sub (directory)"   "$(code "http://$S:8080/sub")" 200
expect "GET /?a=b"              "$(code "http://$S:8080/?a=b")" 200
expect "GET /nope.txt"          "$(code "http://$S:8080/nope.txt")" 404
expect "GET /sub/../index.html" "$(code --path-as-is "http://$S:8080/sub/../index.html")" 403
expect "POST /"                 "$(code -X POST "http://$S:8080/")" 501
expect "GET x (bad target)"     "$(code --request-target x "http://$S:8080/")" 400
expect "HEAD photo.jpg length"  "$(header "http://$S:8080/photo.jpg" content-length)" 150000
expect "type .jpg"  "$(header "http://$S:8080/photo.jpg" content-type)" image/jpeg
expect "type .html" "$(header "http://$S:8080/index.html" content-type)" text/html
expect "type .txt"  "$(header "http://$S:8080/notes.txt" content-type)" text/plain
expect "type .json" "$(header "http://$S:8080/data.json" content-type)" application/json
expect "type .css"  "$(header "http://$S:8080/style.css" content-type)" text/css
expect "type .bin"  "$(header "http://$S:8080/big.bin" content-type)" application/octet-stream
expect "Connection header" "$(header "http://$S:8080/" connection)" close

echo
[ $fail = 0 ] && echo "ALL CLIENT TESTS PASSED" || echo "SOME TESTS FAILED (outputs and --log files in $OUT)"
