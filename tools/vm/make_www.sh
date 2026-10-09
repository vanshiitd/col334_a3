#!/bin/bash
# Create a test document root with fixed, reproducible content (seeded),
# so the client VM can build an identical reference copy and compare
# md5sums without copying files between VMs.
#   bash tools/vm/make_www.sh [DIR]      (default /var/tmp/a3/www)
dir=${1:-/var/tmp/a3/www}
python3 - "$dir" <<'EOF'
import os
import random
import sys

root = sys.argv[1]
rng = random.Random(334)
files = {
    'index.html': (b'<!DOCTYPE html>\n<html><head><title>COL334 A3</title></head>'
                   b'<body><h1>It works</h1><p>' + b'lorem ipsum ' * 300 +
                   b'</p></body></html>\n'),
    'sub/index.html': b'<html><body>sub directory index</body></html>\n',
    'notes.txt': b'plain text line\n' * 200,
    'data.json': b'{"course": "COL334", "assignment": 3}\n',
    'style.css': b'body { font-family: sans-serif; }\n',
    'photo.jpg': rng.randbytes(150_000),          # >= 100 KB binary for R4/R5
    'big.bin': rng.randbytes(5_000_000),
    'huge.bin': rng.randbytes(20_000_000),        # fairness tests
}
for name, data in files.items():
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
EOF
cd "$dir" && md5sum index.html sub/index.html notes.txt data.json style.css photo.jpg big.bin huge.bin
