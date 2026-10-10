# Implementation plan, design notes and VM test checklist

Status: all parts are implemented and tested on a network-namespace replica
of the topology (`tools/topo.sh`). Nothing has run on the VMs yet; section 5
lists what to run there.

## 1. Plan and status

| Part | Files | Status |
|------|-------|--------|
| A Network | `netcfg/*.sh` | done; replica passes the full ping matrix and traceroutes |
| B TCP | `src/rawtcp.py` | done; tested under loss, reordering, GRO, competing flows |
| C Client | `src/http_client.py`, `client/run-client` | done; tested against `python3 -m http.server` and edge-case servers |
| D Server | `src/http_server.py`, `server/run-server` | done; tested with curl, wget, raw malformed requests |
| E Interop | (the above) | done; our client <-> our server, md5 checked |
| Report | `report.pdf` | yours: screenshots must come from the VMs (notes in section 6) |
| README | `README.md` | fill in team names and entry numbers |

Language: Python 3.10, standard library only. It is the easiest to explain
in the viva, and fast enough: about 16 MB/s on the replica, far above any
bottleneck a fairness test would use.

## 2. Part A design

| Machine | Addresses | Routes | forwarding | rp_filter |
|---------|-----------|--------|------------|-----------|
| client | 10.10.1.10/24 (A), 10.10.4.2/30 (D) | 10.10.2.0/24 and 10.10.3.0/24 via 10.10.1.1 | 0 | A strict (1), **D loose (2)** |
| r1 | 10.10.1.1/24 (A), 10.10.2.1/24 (B) | default via 10.10.2.2 (A5: one route) | 1 | strict on both |
| r2 | 10.10.2.2/24 (B), 10.10.3.1/24 (C), 10.10.4.1/30 (D) | 10.10.1.0/24 via 10.10.2.1, **10.10.1.10/32 via 10.10.4.2** | 1 | **B loose (2)**, C and D strict |
| server | 10.10.3.10/24 (C) | 10.10.1.0/24 and 10.10.2.0/24 via 10.10.3.1 | 0 | strict |

Key points (useful for R2, R3 and the viva):

- **The /32 host route on r2.** A3 needs server -> client over Link D. A route
  for all of 10.10.1.0/24 via Link D would break A1: packets for r1's
  10.10.1.1 would go to the client, a host that does not forward. So only
  the client's own address takes Link D; the rest of LAN A goes via r1.
- **Why the client's Link D is loose.** Replies from the server arrive on
  Link D, but the client's route back to 10.10.3.0/24 is via LAN A. Strict
  mode checks "would I route the source out of the arrival interface?",
  gets "no" and drops the packet. Loose mode only checks that the source is
  reachable at all.
- **Why r2's LAN B is loose.** Client -> server packets (source 10.10.1.10)
  arrive at r2 from r1 on LAN B, but r2 routes 10.10.1.10 out of Link D.
  Strict mode would drop them.
- **Why r1 and the server can stay strict.** Every packet they receive comes
  in on the interface they would use to reach its source.
- `ip_forward` is 1 on the routers and 0 on the hosts (A4).
- Only `ip` and `sysctl`, and only the interfaces passed as arguments.
  `rp_filter` is written with the `/` sysctl syntax so interface names
  containing dots still work.

Expected traceroutes (R2):

```
client$ traceroute -n 10.10.3.10        server$ traceroute -n 10.10.1.10
 1  10.10.1.1    (r1)                    1  10.10.3.1   (r2)
 2  10.10.4.1    (r2)                    2  10.10.1.10  (client)
 3  10.10.3.10   (server)
```

Client -> server takes three hops via r1. The reply path, and the server's
own traceroute, take Link D. r2 appears as **10.10.4.1** in the client's
traceroute, not 10.10.2.2 where the probe entered. Linux sends the ICMP
Time Exceeded from the address of the interface it uses to reach the
destination (10.10.1.10), and r2 reaches the client over Link D.

## 3. Part B design (src/rawtcp.py)

One raw socket, `socket(AF_INET, SOCK_RAW, IPPROTO_TCP)` with `IP_HDRINCL`,
is used for both sending and receiving. Everything runs in one thread. Any
call that has to wait (`recv`, `sendall`, `close`) runs `_step()`. `_step()`
reads queued packets and handles each one. It then fires the
retransmission timer and sends whatever `min(cwnd, peer window)` allows.

| Req | Where / how |
|-----|-------------|
| T1 | `build_packet`: full IPv4 header and `checksum()`. ID counter cycles 1..65535, never 0. DF set, TTL 64 |
| T2 | `build_packet`: TCP header and pseudo-header checksum |
| T3 | MSS option (kind 2, len 4, 1460) only on SYN/SYN-ACK. Payload <= `min(1460, peer MSS)`; 536 if the peer sent no MSS |
| T4 | `parse_packet`: IPv4 and TCP checksums, MSS parsed, other options skipped by length |
| T5 | `_accept`: exact 4-tuple match before parsing. Also a kernel BPF port filter (an optimisation) |
| T6 | `random.getrandbits(32)` ISN |
| T7 | SYN / SYN-ACK resent every 1 s, for 8 s in total |
| T8 | Any RST for the 4-tuple aborts (`ConnectionReset`). In SYN-SENT it must ACK our SYN (RFC 793) |
| T9 | `close()`: wait until all data is ACKed, then FIN with retransmission, then wait for ACK + peer FIN, at most 2 s. Peer FINs are always ACKed |
| T10 | In flight <= min(cwnd, rwnd). Cumulative ACKs. RTO per RFC 6298, clamped to [0.2 s, 1 s], with backoff. 10 s without progress -> give up |
| T11 | Pure ACK for every data segment (duplicate and out-of-order too), including each segment inside a GRO-merged packet (see below). Window always 65535. In-order delivery to `inbox` |
| T12 | Out-of-order segments inside the window kept in `out_of_order` and drained when the gap fills |
| T13 | Client port `randint(61000, 65535)`; server port from `-p` |
| T14 | Reno + NewReno, see below |

Congestion control:
- **Initial window and slow start.** cwnd starts at 10 MSS (RFC 6928,
  like Linux). Slow start adds up to 2 MSS per ACK (RFC 3465).
- **Congestion avoidance.** +1 MSS per cwnd bytes ACKed, i.e. +1 MSS per
  RTT.
- **Fast retransmit and recovery.** On 3 duplicate ACKs, ssthresh =
  flight/2 and the first unacked segment is resent. cwnd is inflated
  during recovery. NewReno resends on each partial ACK and deflates to
  ssthresh on the full ACK (RFC 6582, with the `recover` guard).
- **Early retransmit (RFC 5827).** With only 2-3 segments in flight and
  nothing new allowed out, the dupack threshold drops to (segments - 1).
  Small windows then don't always end in a timeout. On the replica this
  cut transfer time under 5% loss from 3.8 s to 2.4 s on average.
- **Timeout.** ssthresh = flight/2, cwnd = 1 MSS, then go-back-N from
  snd_una. The receiver's cumulative ACK skips whatever it had buffered.
  Karn's rule: retransmitted segments are never timed.
- cwnd is capped at 65535, the most an unscaled window can ever allow.

Things the kernel would otherwise get in the way of:

1. **Kernel RSTs.** The kernel has no socket for our connections and
   would RST them. The launchers add an iptables rule dropping outgoing
   RSTs from our ports. This is allowed: launchers may run programs. The
   client range 61000-65535 lies outside Linux's ephemeral ports, so the
   rule never touches the kernel's own connections.
2. **GRO / checksum offload.** Linux may merge several received segments
   into one large packet before the raw socket sees it, after verifying
   each one. It then leaves only the pseudo-header sum in the checksum
   field. `tcp_checksum_ok` accepts exactly that value. With offloads on,
   997 of 1220 received packets were merged ones, up to 11680 bytes. With
   a strict check, nothing at all got through (verified). Corrupted
   packets are never merged, so they still fail.
   Such a packet is then handled as the 1460-byte segments it was made
   from, each with its own ACK. The peer still gets one ACK per segment
   (T11), and one duplicate ACK per segment after a loss.
3. **Choosing the source address.** `connect()` on a UDP socket (allowed;
   only TCP connect is banned) makes the routing table pick our address
   for the server. Nothing is sent.

## 4. Test results on the namespace replica

All downloads were checked by md5 against the original file.

- Part A: full ping matrix OK, traceroutes as in section 2.
- C: 3 MB binary from `python3 -m http.server` in 0.18 s, same md5 as curl.
  Chunked (with extensions and trailers), close-delimited, 100-Continue,
  204, 418 (exit 1), short body (exit 2), refused (exit 2 at once), no host
  (exit 2 after 8 s), silent server (exit 2 after 10 s), DNS failure, bad
  URLs, Host header kept as written, query string kept.
- D: curl, wget and our client get identical files. Table 4 checked with
  15 raw requests (400 / 501 / 403 / 404, HEAD has no body, LF-only
  heads). An incomplete head is closed after 10 s with no response. A
  client aborting mid-download does not disturb the next request.
- T12: 5% random loss in both directions (iptables on both routers):
  every combination completed. With the injected 20% reordering plus 3%
  drop of `tools/impair.py` on top: all correct.
- T14 (16 Mbit/s tbf on r2's Link D, competing with curl from
  `python3 -m http.server` using CUBIC, the Ubuntu default):

  | flows | queue | finish times (s) |
  |-------|-------|------------------|
  | 1 ours + 1 curl | 50 ms | ours 10.6, curl 12.5 (about 56/44) |
  | 3 + 3 | 20 ms | ours 14.5-15.7, curl 16.7-18.8 |
  | 3 + 3 | 50 ms | ours 17.0-17.7, curl 18.3-18.8 |
  | 3 + 3 | 200 ms | interleaved, 16.3-18.8 |
  | 1 + 1 | 200 ms | curl 8.4, ours 12.6 |

  The last row is a built-in limit, not a bug. Our client must advertise
  a fixed 65535-byte window with no scaling (T11), so our flow can never
  have more than 64 KB in flight. A CUBIC flow can keep growing into a
  deep queue. With queues of a few tens of ms, the split is roughly even.

## 5. What to run on the VMs

The step-by-step version for our Fusion VMs, with helper scripts, is in
`docs/VM_RUNBOOK.md`. The generic outline:

Before starting:
- Each VM's adapters must be on the internal networks only. Make sure
  NetworkManager / netplan does not manage those interfaces. Otherwise it
  may run DHCP and later remove our addresses. On Ubuntu Desktop:
  `nmcli dev set IF managed no` for each one. On Server: no netplan entry
  for them.
- Get the code onto each VM, e.g. `git clone` while the NAT adapter is
  still attached, or a shared folder.
- While NAT is still attached, also install what the tests need:
  `sudo apt install tcpdump traceroute curl wget iproute2`. For loss and
  reordering also install the netem module: `linux-modules-extra-$(uname -r)`
  on Server images.
- Before the scripts: `for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 1 | sudo tee $f; done`
- Interface names: `ip -br link`. Match MAC addresses with the VirtualBox
  adapter settings to know which one is LAN A/B/C/D.

Then:
1. Run the four netcfg scripts. Check with `ping` from every machine to
   10.10.1.10, 10.10.1.1, 10.10.2.1, 10.10.2.2, 10.10.3.1, 10.10.3.10,
   plus client <-> 10.10.4.1/10.10.4.2. Run the two traceroutes.
2. Server VM: `cd ~/www && python3 -m http.server 8000`. Client VM:
   `sudo ./client/run-client http://10.10.3.10:8000/index.html -o a.html --log c.log`
   and a binary file of at least 100 KB. Compare `md5sum` with `curl -o`.
3. Server VM: `sudo ./server/run-server -p 8080 -d ~/www --log s.log`.
   Client VM: curl, wget and our client against `http://10.10.3.10:8080/...`.
   Compare md5sums.
4. Loss/reordering on a router, e.g. r2:
   `sudo tc qdisc add dev IF_D root netem delay 10ms 5ms loss 5% reorder 25% 50%`.
   Repeat 2 and 3, then `sudo tc qdisc del dev IF_D root`.
5. Fairness:
   `sudo tc qdisc add dev IF_D root tbf rate 20mbit burst 32kb latency 50ms` on r2.
   Start our client <- our server and curl <- python http.server at the
   same time on a large file (tens of MB) and compare finish times. Then
   3 + 3, using three of our servers on different ports.
6. Capture for R6:
   `sudo tcpdump -nn -i IF_D -w conn.pcap host 10.10.3.10` on the client
   during one download.

If anything fails, send me: the command, its stderr and exit code, the
`--log` files from both sides, and `ip -br addr; ip route` from the
machines involved.

## 6. Report notes

- R1: screenshots of each VM's adapter settings, plus `ip -br addr` and
  `ip route` after the scripts.
- R2: traceroutes and explanation from section 2.
- R3: the rp_filter table and reasons from section 2.
- R4: python http.server downloads (HTML + >= 100 KB binary) with exit
  codes and md5 vs curl.
- R5: curl, wget and our client from our server, three md5s vs the
  original.
- R6: the tcpdump of one complete connection. Retransmission: RTO and
  fast retransmit / NewReno / early retransmit. Out-of-order buffering:
  `out_of_order` dict drained on gap fill. Congestion control: section 3.
