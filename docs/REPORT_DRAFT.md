# Report draft (COL334 Assignment 3)

Text to adapt for `report.pdf`. Each section lists the screenshots it
needs. Every screenshot must be taken on the VMs, be readable, and show
the command that produced it, so type the commands yourself rather than
running the helper scripts. Numbers marked (VM) were measured on our VMs.

---

## R1. Network adapters, addresses and routes

**Setup.** Four Ubuntu Server 22.04 VMs (arm64) in VMware Fusion on an
Apple Silicon Mac. The four assignment networks are four Fusion custom
networks. Each one has NAT, DHCP and "Connect host Mac" turned off, so
they are isolated from each other and from the host.

| Fusion network | Assignment network | Attached adapters |
|----------------|--------------------|-------------------|
| vmnet3 | LAN A 10.10.1.0/24 | client enp2s0, r1 enp2s0 |
| vmnet4 | LAN B 10.10.2.0/24 | r1 enp26s0, r2 enp2s0 |
| vmnet5 | LAN C 10.10.3.0/24 | r2 enp26s0, server enp2s0 |
| vmnet6 | Link D 10.10.4.0/30 | client enp26s0, r2 enp3s0 |

The "Subnet IP" Fusion shows for each vmnet is unused: DHCP and the host
connection are off, and every address is set statically by netcfg/.

Screenshots:
- Fusion > Settings > Network Adapter for every adapter of every VM
  (client 2, r1 2, r2 3, server 1), showing which vmnet each is on.
- On each VM after its netcfg script: `ip -br addr` and `ip route`.

---

## R2. traceroute in both directions

Screenshots: `traceroute -n 10.10.3.10` on the client and
`traceroute -n 10.10.1.10` on the server.

(VM) Results:

```
client -> server            server -> client
 1  10.10.1.1   (r1)         1  10.10.3.1   (r2)
 2  10.10.4.1   (r2)         2  10.10.1.10  (client)
 3  10.10.3.10  (server)
```

**Client to server (3 hops).** The client's route to 10.10.3.0/24 points
at r1 (10.10.1.1). Hop 1 is therefore r1, which answers from its LAN A
address 10.10.1.1, the interface facing the client. r1 has a single
default route to r2 over LAN B, so hop 2 is r2. Hop 3 is the server.

**Why r2 shows up as 10.10.4.1.** The probe reaches r2 on LAN B
(10.10.2.2), yet r2 reports 10.10.4.1, its Link D address. Linux takes
the source address of an ICMP error from the interface the reply leaves
through, not the one the probe came in on (the default
`icmp_errors_use_inbound_ifaddr = 0`). r2's route back to the client
(10.10.1.10) is the host route over Link D, so the Time Exceeded message
goes out on Link D with source 10.10.4.1. It reaches the client directly
over Link D.

**Server to client (2 hops).** The server sends everything for
10.10.1.0/24 to r2 (10.10.3.1), which answers from its LAN C address,
the interface back towards the server. r2's host route
`10.10.1.10/32 via 10.10.4.2` then delivers straight to the client over
Link D, so hop 2 is already the client. r1 never appears.

**Why the two directions differ.** Each router forwards by its own
table and only looks at the destination. The client sends everything for
LANs B and C via r1 (A2). r2 sends packets for 10.10.1.10 over Link D
(A3). So the paths are asymmetric, and each traceroute only shows the
forward path of its own probes. r2 uses a /32 host route rather than all
of 10.10.1.0/24 for a reason: sending r1's 10.10.1.1 over Link D would
hand those packets to the client, which is a host and does not forward,
breaking A1.

---

## R3. Reverse-path filtering

Screenshot on each VM: `sysctl -a 2>/dev/null | grep '\.rp_filter'`.

All interfaces start strict (1). The effective value per interface is
max(`conf.all`, `conf.<if>`), and `all` stays 1.

| Machine | Interface | rp_filter | Why |
|---------|-----------|-----------|-----|
| client | LAN A (enp2s0) | 1 strict | Traffic arriving here comes from r1 or LAN A hosts, reachable via LAN A |
| client | Link D (enp26s0) | **2 loose** | Replies from LANs B/C (e.g. the server, 10.10.3.10) arrive on Link D (A3). The client's route back to them is via LAN A (A2), so strict mode would drop every reply. Loose only needs the source to be reachable through some interface |
| r1 | both | 1 strict | Required (A7). Works because traffic from the client side arrives on LAN A (connected), and everything else comes from r2 on LAN B, where the default route points |
| r2 | LAN B (enp2s0) | **2 loose** | Client to server packets (source 10.10.1.10) arrive from r1 on LAN B. r2's route back to 10.10.1.10 is the host route on Link D, so strict mode would drop all client to server traffic |
| r2 | LAN C, Link D | 1 strict | Packets arriving there come from those directly attached subnets |
| server | LAN C (enp2s0) | 1 strict | Required (A7). Everything arrives on its only interface |

Strict mode is kept everywhere it does not break the required asymmetric
paths. Loose mode is used only where asymmetric routing is intended.

---

## R4. Our client against a standard web server

On the server VM: `python3 -m http.server 8000` (in a directory holding an
HTML page and a binary of at least 100 KB). On the client, screenshot:

```
sudo ./client/run-client http://10.10.3.10:8000/index.html -o ~/ours.html; echo "exit $?"
curl -s -o ~/curl.html http://10.10.3.10:8000/index.html
md5sum ~/ours.html ~/curl.html
sudo ./client/run-client http://10.10.3.10:8000/photo.jpg -o ~/ours.jpg; echo "exit $?"
curl -s -o ~/curl.jpg http://10.10.3.10:8000/photo.jpg
md5sum ~/ours.jpg ~/curl.jpg
```

Both exit codes are 0 and each pair of md5sums matches. (VM) Also
verified by the automated test run: index.html, photo.jpg (150 KB) and
big.bin (5 MB), with body on stdout or `-o`, plus a 404 (exit 1),
connection refused (exit 2) and an invalid URL (exit 2).

---

## R5. curl, wget and our client against our server

On the server VM: `sudo ./server/run-server -p 8080 -d <dir>`. On the
client, screenshot:

```
curl -s -o ~/c.jpg http://10.10.3.10:8080/photo.jpg
wget -q -O ~/w.jpg http://10.10.3.10:8080/photo.jpg
sudo ./client/run-client http://10.10.3.10:8080/photo.jpg -o ~/o.jpg; echo "exit $?"
md5sum ~/c.jpg ~/w.jpg ~/o.jpg
```

Plus, on the server, `md5sum <dir>/photo.jpg`: all four values match.
(VM) The server also returned the right status for each case in Table 4
(400, 501, 403, 404, 200, HEAD with no body) and the right Content-Type
for each extension tested (.html, .txt, .css, .json, .jpg, and
application/octet-stream for others).

---

## R6. One connection, retransmission, buffering, congestion control

Screenshot (client): `sudo tcpdump -nn -r /var/tmp/a3/conn.pcap` of our
client downloading index.html (3,704 bytes) from our server. It was
captured with `tcpdump -i any` (`tools/vm/capture.sh`), since the
connection uses two interfaces.

**What the capture shows (VM).**

| Packets | Meaning |
|---------|---------|
| `[S]` from 10.10.1.10.63714, `options [mss 1460]` | Our SYN: random source port in 61000-65535, random ISN, the MSS option only |
| `[S.]` with `options [mss 1460]`, then our `[.] ack 1` | SYN-ACK and the final handshake ACK |
| `[P.] seq 1:115 … GET /index.html` | The request (114 bytes) |
| server `[.] ack 115` | Server acknowledges the request |
| server `[P.] seq 1:3854, length 3853` | The response: 3 segments of 1460 + 1460 + 933 bytes, merged by GRO on the client before tcpdump saw them |
| our `ack 1461`, `ack 2921`, `ack 3854` | One cumulative ACK per segment received (T11) |
| our `[F.] seq 115` | The response is complete (Content-Length reached), so our client closes (T9) |
| server `[F.] seq 3854` | The server closes after all its data is acknowledged |
| our `ack 3855`, server `ack 116` | Each side acknowledges the other's FIN |

All our packets leave on enp2s0 (LAN A, via r1). All the server's
packets arrive on enp26s0 (Link D, via r2): the asymmetric paths of R2.

**Retransmission.**
- Every sent segment stays in the send buffer until it is acknowledged
  cumulatively.
- The retransmission timeout follows RFC 6298: smoothed RTT and its
  variance, clamped to 0.2-1 s, doubled on each timeout up to 1 s.
  Retransmitted segments are never timed (Karn).
- Three duplicate ACKs trigger fast retransmit. With only 2-3 segments in
  flight, one fewer duplicate is enough (early retransmit, RFC 5827).
- During recovery, each partial ACK resends the next hole at once
  (NewReno).
- On a timeout, sending restarts from the first unacknowledged byte. The
  receiver's cumulative ACK then skips anything it already buffered.
- SYN and SYN-ACK are resent every second for up to 8 s. FIN is resent
  until acknowledged, for at most 2 s. A transfer gives up after 10 s
  without any new acknowledgement.

**Out-of-order buffering.**
- A segment that arrives ahead of the next expected byte (`rcv_nxt`) but
  inside our 65535-byte window is stored in a buffer keyed by sequence
  number. Data we already have is trimmed off.
- Every arriving segment is acknowledged with `rcv_nxt`. While a gap
  exists, these ACKs are duplicates, which is what drives the sender's
  fast retransmit.
- When the missing segment arrives, all contiguous buffered data is
  handed to HTTP in order, exactly once, and the next ACK jumps over the
  whole range.

**Congestion control (Reno with NewReno recovery).**
- The amount in flight never exceeds min(cwnd, peer's window).
- cwnd starts at 10 MSS. In slow start it grows by the bytes acknowledged
  (at most 2 MSS per ACK, RFC 3465), roughly doubling each RTT.
- Above ssthresh it grows by 1 MSS per window acknowledged, i.e. 1 MSS
  per RTT.
- On three duplicate ACKs: ssthresh = max(flight/2, 2 MSS), cwnd = ssthresh + 3 MSS.
  cwnd is inflated by one MSS per further duplicate and deflated to
  ssthresh when everything outstanding at the loss is acknowledged.
- On a timeout: ssthresh = max(flight/2, 2 MSS) and cwnd = 1 MSS.

(VM) Measured results:
- **Loss and reordering.** netem added 10±5 ms delay, 5% loss and 25%
  reordering on r2's Link D, plus 5 ms delay and 5% loss on r1's LAN B.
  netem dropped 1050 packets during the run. Every download (HTML,
  150 KB, 5 MB; by our client, curl and wget) still matched byte for byte.
- **Fairness.** With a 20 Mbit/s bottleneck (tbf on r2's Link D), each
  flow downloaded the same 20 MB file at the same time:

  | flows | finish times |
  |-------|--------------|
  | 1 ours + 1 curl | curl 15.4 s, ours 16.7 s |
  | 3 ours + 3 curl | ours 28.0 / 49.3 / 49.4 s, curl 38.0 / 49.4 / 50.0 s |

  An equal share of 20 Mbit/s finishes both 1-vs-1 transfers at about
  16 s and all six 3-vs-3 transfers at about 48 s. The measured times
  match that, so the two kinds share the link roughly equally (curl
  ran over Linux's default CUBIC).
