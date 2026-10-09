# COL334 Assignment 3: TCP and HTTP over raw sockets

## Team

| Name | Entry number |
|------|--------------|
| _TODO_ | _TODO_ |
| _TODO_ | _TODO_ |

## Language and runtime

Python 3 (written for Python 3.10 on Ubuntu 22.04), standard library only.
Nothing needs to be built, and there is no Makefile.

## Layout

```
netcfg/     client.sh r1.sh r2.sh server.sh   Part A, one script per machine
client/     run-client                        launcher for the HTTP client
server/     run-server                        launcher for the HTTP server
src/        rawtcp.py                         TCP/IPv4 over raw sockets (Part B)
            http_client.py                    HTTP client (Part C)
            http_server.py                    HTTP server (Part D)
```

`tools/` and `docs/` hold development helpers and notes. They are not part
of the submission.

## Running

```
sudo netcfg/client.sh IF_A IF_D          # likewise r1.sh, r2.sh, server.sh
sudo ./client/run-client URL [-o OUTFILE] [--log LOGFILE]
sudo ./server/run-server [-p PORT] [-d DOCROOT] [--log LOGFILE]
```

Both programs need root for the raw socket.

The kernel has no socket for a connection that our user-space TCP owns,
so it would answer the peer's segments with RSTs. Before starting Python,
each launcher adds one iptables rule (once; it is left in place) that drops
outgoing RSTs from our ports:

- the client drops RSTs from source ports 61000-65535. Its source port is
  always in that range, which lies outside Linux's ephemeral range
  32768-60999.
- the server drops RSTs from the port given with `-p` (default 8080).
