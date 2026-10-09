"""Development helper (not part of the submission): run the HTTP client or
server with random loss and reordering injected into what our TCP receives.
Useful where netem is not available.

    python3 tools/impair.py [--drop P] [--reorder P] client URL [-o FILE] ...
    python3 tools/impair.py [--drop P] [--reorder P] server [-p PORT] ...

--reorder P holds a packet back with probability P and releases it after
1-4 later packets (or after 20 ms if nothing else arrives).
"""

import collections
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import rawtcp  # noqa: E402

drop = reorder = 0.0
args = sys.argv[1:]
while args and args[0].startswith('--'):
    opt, val = args[0], float(args[1])
    args = args[2:]
    if opt == '--drop':
        drop = val
    elif opt == '--reorder':
        reorder = val
which, sys.argv = args[0], [args[0]] + args[1:]

original_receive = rawtcp.RawSocket.receive
stats = collections.Counter()


def impaired_receive(self, timeout):
    if not hasattr(self, '_held'):
        self._held = []              # [packets still to pass, deadline, packet]
        self._ready = collections.deque()
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        if self._ready:
            return self._ready.popleft()
        now = time.monotonic()
        for h in [h for h in self._held if h[1] <= now]:
            self._held.remove(h)
            self._ready.append(h[2])
        if self._ready:
            continue
        wait = deadline - now
        if self._held:
            wait = min(wait, min(h[1] for h in self._held) - now)
        pkt = original_receive(self, max(wait, 0.0))
        if pkt is None:
            if time.monotonic() >= deadline:
                return None
            continue
        if random.random() < drop:
            stats['dropped'] += 1
            continue
        for h in self._held:
            h[0] -= 1
        due = [h for h in self._held if h[0] <= 0]
        for h in due:
            self._held.remove(h)
        if random.random() < reorder:
            stats['reordered'] += 1
            self._held.append([random.randint(1, 4), time.monotonic() + 0.02, pkt])
        else:
            self._ready.append(pkt)
        self._ready.extend(h[2] for h in due)


rawtcp.RawSocket.receive = impaired_receive

try:
    if which == 'client':
        import http_client
        code = http_client.main()
    else:
        import http_server
        code = http_server.main()
finally:
    print('impair: %s' % dict(stats), file=sys.stderr)
sys.exit(code)
