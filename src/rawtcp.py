"""TCP over IPv4 raw sockets (Part B).

Every IPv4 and TCP header we send is built here, and every header we
receive is parsed and checked here. The raw socket is opened with
IP_HDRINCL, so the kernel does not add an IP header of its own.

The HTTP programs use a small blocking API:

    conn = connect(ip, port, log_path)        # client: active open
    listener = Listener(port, log_path)       # server: passive open
    conn = listener.accept()
    conn.sendall(data)
    data = conn.recv()                        # b'' once the peer has closed
    conn.close()

Everything runs in one thread. Whenever a call has to wait, it runs
_step(), which reads packets from the raw socket, handles them, fires
the retransmission timer and sends whatever the windows allow.

Requirement numbers (T1-T14) refer to the assignment text.
"""

import array
import collections
import ctypes
import random
import select
import socket
import struct
import sys
import time

# TCP flag bits (byte 13 of the TCP header).
FIN = 0x01
SYN = 0x02
RST = 0x04
PSH = 0x08
ACK = 0x10

TTL = 64
MSS = 1460                # announced in SYN/SYN-ACK; also our largest payload (T3)
DEFAULT_PEER_MSS = 536    # RFC 1122 default when the peer sends no MSS option
RCV_WND = 65535           # fixed advertised window (T11)
MAX_CWND = 65535          # without window scaling the peer can never accept more

SYN_RTO = 1.0             # handshake retransmission interval (T7)
SYN_GIVE_UP = 8.0         # keep retrying the handshake this long (T7: >= 6 s)
RTO_INITIAL = 1.0
RTO_MIN = 0.2
RTO_MAX = 1.0             # T10: retransmission timeout of at most 1 s
IDLE_GIVE_UP = 10.0       # T10: give up if nothing is acknowledged for 10 s
FIN_GIVE_UP = 2.0         # T9: stop waiting for the FIN exchange after 2 s

SEND_BUFFER = 256 * 1024  # sendall() blocks while more than this is queued
RECV_BATCH = 64           # packets read from the socket per wake-up

SEQ_MOD = 1 << 32


class TCPError(Exception):
    """The connection failed; the message says why."""


class ConnectionRefused(TCPError):
    pass


class ConnectionReset(TCPError):
    pass


class ConnectionTimeout(TCPError):
    pass


def seq_diff(a, b):
    """Signed distance a - b between two 32-bit sequence numbers."""
    d = (a - b) % SEQ_MOD
    return d - SEQ_MOD if d >= SEQ_MOD // 2 else d


def checksum(data):
    """RFC 1071 Internet checksum, as the 16-bit value to store big-endian.

    The data is summed as native-endian 16-bit words, which is fast with
    array.array. The one's complement sum does not depend on byte order
    except that the result comes out byte-swapped (RFC 1071, section 2B),
    so on little-endian machines we swap it back at the end. Running it
    over data that already contains a correct checksum gives 0.
    """
    if len(data) % 2:
        data += b'\x00'
    total = sum(array.array('H', data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    total = ~total & 0xFFFF
    if sys.byteorder == 'little':
        total = ((total << 8) | (total >> 8)) & 0xFFFF
    return total


def pseudo_header(src, dst, tcp_length):
    """IPv4 pseudo-header used in the TCP checksum (src/dst are 4 bytes)."""
    return struct.pack('!4s4sBBH', src, dst, 0, socket.IPPROTO_TCP, tcp_length)


class Segment:
    """A TCP segment together with the IPv4 fields we fill in or log."""

    __slots__ = ('src', 'dst', 'sport', 'dport', 'seq', 'ack', 'flags',
                 'win', 'payload', 'mss', 'ttl', 'ip_id')

    def __init__(self, src, dst, sport, dport, seq, ack, flags, win,
                 payload=b'', mss=None, ttl=TTL, ip_id=0):
        self.src = src          # dotted-quad strings
        self.dst = dst
        self.sport = sport
        self.dport = dport
        self.seq = seq
        self.ack = ack
        self.flags = flags
        self.win = win
        self.payload = payload
        self.mss = mss          # value of the MSS option, None if absent
        self.ttl = ttl
        self.ip_id = ip_id


def build_packet(seg):
    """Build the complete IPv4 + TCP packet for `seg` (T1, T2, T3)."""
    src = socket.inet_aton(seg.src)
    dst = socket.inet_aton(seg.dst)
    # T3: SYN and SYN-ACK carry exactly one option, MSS (kind 2, length 4).
    options = b'' if seg.mss is None else struct.pack('!BBH', 2, 4, seg.mss)
    data_offset = (20 + len(options)) // 4
    tcp = struct.pack('!HHIIBBHHH', seg.sport, seg.dport, seg.seq, seg.ack,
                      data_offset << 4, seg.flags, seg.win,
                      0,    # checksum, filled in below
                      0)    # urgent pointer
    tcp += options
    csum = checksum(pseudo_header(src, dst, len(tcp) + len(seg.payload))
                    + tcp + seg.payload)
    tcp = tcp[:16] + struct.pack('!H', csum) + tcp[18:]

    total_length = 20 + len(tcp) + len(seg.payload)
    ip = struct.pack('!BBHHHBBH4s4s',
                     0x45,              # version 4, header length 5 words
                     0,                 # DSCP/ECN
                     total_length,
                     seg.ip_id,         # T1: never zero (see RawSocket.send)
                     0x4000,            # don't fragment, offset 0
                     seg.ttl,
                     socket.IPPROTO_TCP,
                     0,                 # checksum, filled in below
                     src, dst)
    ip = ip[:10] + struct.pack('!H', checksum(ip)) + ip[12:]
    return ip + tcp + seg.payload


def parse_mss_option(options):
    """Return the MSS option value in a TCP options field, or None (T4).

    Other options are skipped using their length byte."""
    i = 0
    while i < len(options):
        kind = options[i]
        if kind == 0:                       # end of option list
            break
        if kind == 1:                       # no-operation
            i += 1
            continue
        if i + 1 >= len(options):
            break
        length = options[i + 1]
        if length < 2 or i + length > len(options):
            break                           # malformed; ignore the rest
        if kind == 2 and length == 4:
            return struct.unpack_from('!H', options, i + 2)[0]
        i += length
    return None


def tcp_checksum_ok(src, dst, tcp):
    """Check the TCP checksum of a received segment (T4).

    One exception: when Linux GRO (generic receive offload) merges several
    consecutive segments of a flow into one big packet before handing it to
    us, it has already verified each original segment, and it leaves only
    the pseudo-header sum in the checksum field (the CHECKSUM_PARTIAL
    convention). We accept exactly that value. Corrupted packets are never
    merged, so they still arrive with their original, wrong checksum.
    """
    pseudo = pseudo_header(src, dst, len(tcp))
    if checksum(pseudo + tcp) == 0:
        return True
    stored = struct.unpack_from('!H', tcp, 16)[0]
    return stored == ~checksum(pseudo) & 0xFFFF


def parse_packet(pkt):
    """Parse a received IPv4 packet carrying TCP.

    Returns a Segment, or None if the packet is malformed or either checksum
    is wrong; such packets are dropped silently (T4)."""
    if len(pkt) < 40 or pkt[0] >> 4 != 4:
        return None
    ihl = (pkt[0] & 0x0F) * 4
    total_length, ip_id, frag, ttl, proto = struct.unpack_from('!2xHHHBB', pkt)
    if (proto != socket.IPPROTO_TCP or ihl < 20
            or total_length < ihl + 20 or total_length > len(pkt)):
        return None
    if frag & 0x3FFF:               # fragments are not supported
        return None
    if checksum(pkt[:ihl]) != 0:    # IPv4 header checksum
        return None

    src, dst = pkt[12:16], pkt[16:20]
    tcp = pkt[ihl:total_length]
    if not tcp_checksum_ok(src, dst, tcp):
        return None
    sport, dport, seq, ack, offset, flags, win = struct.unpack_from('!HHIIBBH', tcp)
    data_offset = (offset >> 4) * 4
    if data_offset < 20 or data_offset > len(tcp):
        return None
    return Segment(socket.inet_ntoa(src), socket.inet_ntoa(dst), sport, dport,
                   seq, ack, flags, win, payload=tcp[data_offset:],
                   mss=parse_mss_option(tcp[20:data_offset]),
                   ttl=ttl, ip_id=ip_id)


def peek_ports(pkt):
    """(src, dst, sport, dport) of a raw packet without full parsing, so
    packets of other connections can be skipped cheaply. None if too short."""
    if len(pkt) < 20:
        return None
    ihl = (pkt[0] & 0x0F) * 4
    if len(pkt) < ihl + 4:
        return None
    sport, dport = struct.unpack_from('!HH', pkt, ihl)
    return pkt[12:16], pkt[16:20], sport, dport


class RawSocket:
    """The raw IPv4 socket shared by everything in one program, plus the
    packet log (--log)."""

    def __init__(self, port, log_path=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW,
                                  socket.IPPROTO_TCP)
        # We supply the whole IPv4 header ourselves (section 6.2).
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        self._grow_buffers()
        self._attach_port_filter(port)
        self.ip_id = random.randint(1, 0xFFFF)
        self.backlog = collections.deque()
        self.log = open(log_path, 'w') if log_path else None

    def _grow_buffers(self):
        # A raw socket sees every incoming TCP packet for our port; a large
        # receive buffer avoids drops when a window's worth arrives at once.
        # SO_RCVBUFFORCE (33) lets root go past net.core.rmem_max.
        for force, plain in ((33, socket.SO_RCVBUF), (32, socket.SO_SNDBUF)):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, force, 4 << 20)
            except OSError:
                self.sock.setsockopt(socket.SOL_SOCKET, plain, 4 << 20)

    def _attach_port_filter(self, port):
        """Have the kernel queue only TCP packets addressed to `port` on this
        socket (a classic BPF program, SO_ATTACH_FILTER), so unrelated
        traffic cannot fill the receive buffer. Only an optimisation: the
        connection code checks addresses and ports again."""
        insns = [
            (0xB1, 0, 0, 0),         # ldxb 4*([0]&0xf)   X = IP header length
            (0x48, 0, 0, 2),         # ldh  [x+2]         A = TCP destination port
            (0x15, 0, 1, port),      # jeq  #port         accept : drop
            (0x06, 0, 0, 0x40000),   # ret  #262144       accept the packet
            (0x06, 0, 0, 0),         # ret  #0            drop it
        ]
        code = b''.join(struct.pack('HBBI', *i) for i in insns)
        self._bpf = ctypes.create_string_buffer(code, len(code))
        prog = struct.pack('HP', len(insns), ctypes.addressof(self._bpf))
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, 26, prog)   # SO_ATTACH_FILTER
        except OSError:
            pass

    def send(self, seg):
        self.ip_id = self.ip_id % 0xFFFF + 1     # 1..65535, never 0 (T1)
        seg.ip_id = self.ip_id
        self.sock.sendto(build_packet(seg), (seg.dst, 0))
        self.log_segment('SEND', seg)

    def receive(self, timeout):
        """Next raw packet (bytes), waiting at most `timeout` seconds for one
        to arrive; None if none did."""
        if not self.backlog:
            ready, _, _ = select.select([self.sock], [], [], max(timeout, 0.0))
            if not ready:
                return None
            for _ in range(RECV_BATCH):
                try:
                    self.backlog.append(self.sock.recv(65535, socket.MSG_DONTWAIT))
                except BlockingIOError:
                    break
        return self.backlog.popleft() if self.backlog else None

    def log_segment(self, direction, seg):
        """One line per segment sent or accepted (section 2.6)."""
        if self.log is None:
            return
        mss = '-' if seg.mss is None else seg.mss
        self.log.write(
            f'{direction} {seg.src}:{seg.sport} > {seg.dst}:{seg.dport} '
            f'ttl={seg.ttl} id={seg.ip_id} seq={seg.seq} ack={seg.ack} '
            f'flags=0x{seg.flags:02x} win={seg.win} len={len(seg.payload)} '
            f'mss={mss}\n')
        self.log.flush()


class TCPConnection:
    """One TCP connection: handshake, reliable transfer, congestion control
    and teardown."""

    def __init__(self, raw, local_ip, local_port, remote_ip, remote_port):
        self.raw = raw
        self.local_ip, self.local_port = local_ip, local_port
        self.remote_ip, self.remote_port = remote_ip, remote_port
        self._local_b = socket.inet_aton(local_ip)
        self._remote_b = socket.inet_aton(remote_ip)
        now = time.monotonic()

        # Receive side.
        self.rcv_nxt = 0            # next in-order sequence number expected
        self.out_of_order = {}      # seq -> payload kept until the gap fills (T12)
        self.inbox = bytearray()    # in-order bytes not yet read by the app
        self.peer_fin_seq = None    # sequence number of the peer's FIN, once seen
        self.peer_closed = False    # FIN reached in order: no more data

        # Send side. sndbuf holds every byte from snd_una on: first the
        # unacknowledged ones, then the not yet sent ones.
        self.iss = random.getrandbits(32)                  # T6
        self.snd_una = self.snd_nxt = self.snd_max = self.iss
        self.sndbuf = bytearray()
        self.snd_wnd = 0            # peer's advertised window
        self.mss = DEFAULT_PEER_MSS
        self.fin_seq = None         # sequence number of our FIN once queued
        self.fin_acked = False

        # Congestion control (T14): Reno with NewReno fast recovery.
        self.cwnd = 10 * MSS        # recomputed from the peer's MSS later
        self.ssthresh = 1 << 30
        self.ca_acked = 0           # bytes acked since cwnd last grew in CA
        self.dupacks = 0
        self.in_recovery = False
        self.recover = self.iss     # snd_max when the last loss was detected

        # Retransmission timer (T10).
        self.srtt = None
        self.rttvar = None
        self.rto = RTO_INITIAL
        self.rto_deadline = None    # when the oldest unacked segment times out
        self.rtt_seq = None         # sequence number whose ACK we are timing
        self.rtt_start = 0.0
        self.last_progress = now    # last time new data was acknowledged

    # ------------------------------------------------------------------
    # Connection setup (T6, T7)

    def _set_peer_mss(self, mss):
        self.mss = max(1, min(MSS, mss if mss else DEFAULT_PEER_MSS))   # T3
        self.cwnd = 10 * self.mss   # initial window, as in RFC 6928 / Linux

    def _active_open(self):
        """Client side of the three-way handshake."""
        self._send(SYN, self.iss, mss=MSS)
        self.snd_nxt = self.snd_max = (self.iss + 1) % SEQ_MOD
        start = sent = time.monotonic()
        retransmitted = False
        while True:
            seg = self._next_segment(sent + SYN_RTO)
            now = time.monotonic()
            if seg is None:
                if now >= sent + SYN_RTO:
                    if now - start >= SYN_GIVE_UP:
                        raise ConnectionTimeout('no answer to SYN')
                    self._send(SYN, self.iss, mss=MSS)
                    sent, retransmitted = now, True
                continue
            acks_syn = seg.flags & ACK and seg.ack == self.snd_nxt
            if seg.flags & RST:
                if acks_syn:            # RFC 793: only a RST that acks our SYN counts
                    raise ConnectionRefused('connection refused')
                continue
            if seg.flags & SYN and acks_syn:
                self.rcv_nxt = (seg.seq + 1) % SEQ_MOD
                self.snd_una = seg.ack
                self.snd_wnd = seg.win
                self._set_peer_mss(seg.mss)
                if not retransmitted:
                    self._rtt_sample(now - sent)
                self.last_progress = now
                self._send_ack()
                return

    def _passive_open(self, syn):
        """Server side of the handshake, starting from the client's SYN."""
        self.rcv_nxt = (syn.seq + 1) % SEQ_MOD
        self.snd_wnd = syn.win
        self._set_peer_mss(syn.mss)
        self._send(SYN | ACK, self.iss, mss=MSS)
        self.snd_nxt = self.snd_max = (self.iss + 1) % SEQ_MOD
        start = sent = time.monotonic()
        retransmitted = False
        while True:
            seg = self._next_segment(sent + SYN_RTO)
            now = time.monotonic()
            if seg is None:
                if now >= sent + SYN_RTO:
                    if now - start >= SYN_GIVE_UP:
                        raise ConnectionTimeout('no answer to SYN-ACK')
                    self._send(SYN | ACK, self.iss, mss=MSS)
                    sent, retransmitted = now, True
                continue
            if seg.flags & RST:
                raise ConnectionReset('reset during handshake')
            if seg.flags & SYN:         # client did not get our SYN-ACK
                self._send(SYN | ACK, self.iss, mss=MSS)
                continue
            if seg.flags & ACK and seg.ack == self.snd_nxt:
                self.snd_una = seg.ack
                self.snd_wnd = seg.win
                if not retransmitted:
                    self._rtt_sample(now - sent)
                self.last_progress = now
                # The ACK may already carry data (e.g. if the bare handshake
                # ACK was lost and this is the request itself).
                if seg.payload or seg.flags & FIN:
                    self._handle(seg)
                return

    # ------------------------------------------------------------------
    # Application interface

    def sendall(self, data):
        """Queue `data` for sending; blocks while the send buffer is full."""
        if not self.sndbuf:
            self.last_progress = time.monotonic()
        self.sndbuf += data
        self._output()
        while len(self.sndbuf) > SEND_BUFFER:
            self._step(time.monotonic() + 1.0)

    def recv(self, timeout=IDLE_GIVE_UP):
        """Return received bytes, in order and exactly once (T11). b'' means
        the peer has closed its side. Raises ConnectionTimeout if nothing
        arrives within `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while not self.inbox and not self.peer_closed:
            if time.monotonic() >= deadline:
                raise ConnectionTimeout('no data for %g s' % timeout)
            self._step(deadline)
        data = bytes(self.inbox)
        self.inbox.clear()
        return data

    def close(self):
        """Teardown (T9). First wait until all our data is acknowledged (the
        10 s rule still applies), then send FIN, retransmit it until it is
        acknowledged and wait for the peer's FIN, giving up after 2 s."""
        if self.fin_seq is not None:
            return                      # already closed
        try:
            while self.sndbuf:
                self._step(time.monotonic() + 1.0)
            self.fin_seq = self.snd_nxt
            self.last_progress = time.monotonic()
            self._output()
            deadline = time.monotonic() + FIN_GIVE_UP
            while not (self.fin_acked and self.peer_closed):
                if time.monotonic() >= deadline:
                    break
                self._step(deadline)
        except ConnectionReset:
            pass

    # ------------------------------------------------------------------
    # Event loop

    def _next_segment(self, deadline):
        """Wait until `deadline` for one segment of this connection."""
        while True:
            pkt = self.raw.receive(deadline - time.monotonic())
            if pkt is None:
                return None
            seg = self._accept(pkt)
            if seg is not None:
                return seg

    def _step(self, deadline):
        """Wait for packets (at most until `deadline` or the retransmission
        timer), handle every queued packet, then run the timers and send
        whatever the windows allow."""
        wake = deadline
        if self.rto_deadline is not None:
            wake = min(wake, self.rto_deadline)
        pkt = self.raw.receive(wake - time.monotonic())
        while pkt is not None:
            seg = self._accept(pkt)
            if seg is not None:
                self._handle(seg)
            pkt = self.raw.receive(0) if self.raw.backlog else None
        self._check_timers()
        self._output()

    def _accept(self, pkt):
        """Parse `pkt` if it belongs to this connection (T5) and both its
        checksums are right (T4); log it as RECV. Otherwise None."""
        ports = peek_ports(pkt)
        if ports != (self._remote_b, self._local_b,
                     self.remote_port, self.local_port):
            return None
        seg = parse_packet(pkt)
        if seg is not None:
            self.raw.log_segment('RECV', seg)
        return seg

    def _handle(self, seg):
        """Process one incoming segment of an established connection."""
        if seg.flags & RST:                         # T8
            self.peer_closed = True
            raise ConnectionReset('connection reset by peer')
        if seg.flags & SYN:
            # A retransmitted SYN-ACK: our handshake ACK was lost.
            self._send_ack()
            return
        if not seg.flags & ACK:
            return
        self._process_ack(seg)
        if seg.payload or seg.flags & FIN:
            self._process_data(seg)

    # ------------------------------------------------------------------
    # Receiving (T11, T12)

    def _process_data(self, seg):
        seq, data = seg.seq, seg.payload
        if seg.flags & FIN and self.peer_fin_seq is None:
            self.peer_fin_seq = (seq + len(data)) % SEQ_MOD

        # Drop the part we already have.
        already = seq_diff(self.rcv_nxt, seq)
        if already > 0:
            data = data[already:]
            seq = self.rcv_nxt
        # Keep only what fits in our window.
        start = seq_diff(seq, self.rcv_nxt)
        if data and start < RCV_WND:
            data = data[:RCV_WND - start]
            if start == 0:
                self._deliver(data)
            elif len(data) > len(self.out_of_order.get(seq, b'')):
                self.out_of_order[seq] = data       # T12: keep, don't discard

        if self.peer_fin_seq is not None and self.rcv_nxt == self.peer_fin_seq \
                and not self.peer_closed:
            self.peer_closed = True
            self.rcv_nxt = (self.rcv_nxt + 1) % SEQ_MOD     # the FIN itself
        # T11: a cumulative ACK for every data segment, including duplicate
        # and out-of-order ones (this also acknowledges the peer's FIN, T9).
        self._send_ack()

    def _deliver(self, data):
        """Append in-order data, then whatever buffered segments it unlocks."""
        self.inbox += data
        self.rcv_nxt = (self.rcv_nxt + len(data)) % SEQ_MOD
        while self.out_of_order:
            progressed = False
            for seq in list(self.out_of_order):
                behind = seq_diff(self.rcv_nxt, seq)
                if behind >= 0:                     # starts at or before rcv_nxt
                    chunk = self.out_of_order.pop(seq)
                    if behind < len(chunk):
                        self.inbox += chunk[behind:]
                        self.rcv_nxt = (self.rcv_nxt + len(chunk) - behind) % SEQ_MOD
                        progressed = True
            if not progressed:
                break

    # ------------------------------------------------------------------
    # Sending (T10) and congestion control (T14)

    def _send(self, flags, seq, payload=b'', mss=None):
        self.raw.send(Segment(self.local_ip, self.remote_ip, self.local_port,
                              self.remote_port, seq, self.rcv_nxt, flags,
                              RCV_WND, payload, mss))

    def _send_ack(self):
        self._send(ACK, self.snd_nxt)

    def _output(self):
        """Send new (or, after a timeout, resent) segments from snd_nxt while
        the unacknowledged data stays within min(cwnd, peer window) (T10)."""
        window = min(self.cwnd, self.snd_wnd)
        while True:
            in_flight = seq_diff(self.snd_nxt, self.snd_una)
            unsent = len(self.sndbuf) - in_flight
            if unsent > 0:
                size = min(self.mss, unsent)
                if in_flight + size > window:
                    if in_flight > 0 or window <= 0:
                        return
                    size = window           # nothing in flight: send what fits
                payload = bytes(self.sndbuf[in_flight:in_flight + size])
                flags = ACK | (PSH if size == unsent else 0)
                self._send(flags, self.snd_nxt, payload)
                now = time.monotonic()
                if self.snd_nxt == self.snd_max and self.rtt_seq is None:
                    self.rtt_seq = (self.snd_nxt + size) % SEQ_MOD  # time new data only
                    self.rtt_start = now
                self.snd_nxt = (self.snd_nxt + size) % SEQ_MOD
            elif self.fin_seq is not None and self.snd_nxt == self.fin_seq:
                self._send(FIN | ACK, self.fin_seq)
                now = time.monotonic()
                self.snd_nxt = (self.fin_seq + 1) % SEQ_MOD
            else:
                return
            if seq_diff(self.snd_nxt, self.snd_max) > 0:
                self.snd_max = self.snd_nxt
            if self.rto_deadline is None:
                self.rto_deadline = now + self.rto

    def _retransmit_first(self):
        """Resend the first unacknowledged segment (fast retransmit and
        NewReno partial ACKs) and restart the timer."""
        if self.sndbuf:
            payload = bytes(self.sndbuf[:min(self.mss, len(self.sndbuf))])
            self._send(ACK, self.snd_una, payload)
        elif self.fin_seq is not None and not self.fin_acked:
            self._send(FIN | ACK, self.fin_seq)
        self.rtt_seq = None                 # Karn: no samples across a resend
        self.rto_deadline = time.monotonic() + self.rto

    def _process_ack(self, seg):
        ack = seg.ack
        in_flight = seq_diff(self.snd_max, self.snd_una)
        newly_acked = seq_diff(ack, self.snd_una)
        if 0 < newly_acked <= in_flight:
            # New data acknowledged (cumulative ACK).
            del self.sndbuf[:min(newly_acked, len(self.sndbuf))]
            if self.fin_seq is not None and ack == (self.fin_seq + 1) % SEQ_MOD:
                self.fin_acked = True
            self.snd_una = ack
            if seq_diff(self.snd_nxt, ack) < 0:
                self.snd_nxt = ack
            now = time.monotonic()
            self.last_progress = now
            if self.rtt_seq is not None and seq_diff(ack, self.rtt_seq) >= 0:
                self._rtt_sample(now - self.rtt_start)
                self.rtt_seq = None
            else:
                self.rto = self._rto_from_estimates()   # drop any backoff
            self._on_new_ack(newly_acked)
            self.dupacks = 0
            self.rto_deadline = now + self.rto if self.snd_una != self.snd_max else None
        elif newly_acked == 0 and in_flight > 0 and not seg.payload \
                and not seg.flags & (SYN | FIN):
            self._on_dupack(in_flight)
        if newly_acked >= 0:
            self.snd_wnd = seg.win

    def _on_new_ack(self, acked):
        if self.in_recovery:
            if seq_diff(self.snd_una, self.recover) >= 0:
                # Full ACK: everything sent before the loss is acknowledged.
                self.in_recovery = False
                self.cwnd = self.ssthresh
            else:
                # Partial ACK (NewReno): the next segment was lost as well.
                self._retransmit_first()
                self.cwnd = max(self.cwnd - acked + self.mss, self.mss)
            return
        if self.cwnd < self.ssthresh:
            # Slow start: grow by the bytes acked (at most 2 MSS per ACK,
            # RFC 3465), i.e. roughly double every RTT.
            self.cwnd += min(acked, 2 * self.mss)
        else:
            # Congestion avoidance: one MSS per window of data acked, i.e.
            # one MSS per RTT.
            self.ca_acked += acked
            if self.ca_acked >= self.cwnd:
                self.ca_acked -= self.cwnd
                self.cwnd += self.mss
        self.cwnd = min(self.cwnd, MAX_CWND)

    def _dupack_threshold(self, in_flight):
        """Duplicate ACKs that signal a loss: normally 3. With 2 or 3
        segments outstanding and nothing new allowed out, 3 duplicates can
        never arrive, so use one less than the number outstanding (early
        retransmit, RFC 5827) instead of waiting for the timer. A single
        outstanding segment cannot cause duplicates, so it is left to the
        timer."""
        segments = -(-in_flight // self.mss)
        unsent = len(self.sndbuf) - seq_diff(self.snd_nxt, self.snd_una)
        can_send = unsent > 0 and \
            in_flight + min(self.mss, unsent) <= min(self.cwnd, self.snd_wnd)
        if 2 <= segments < 4 and not can_send:
            return segments - 1
        return 3

    def _on_dupack(self, in_flight):
        self.dupacks += 1
        if self.in_recovery:
            # Each further duplicate means another segment left the network.
            self.cwnd = min(self.cwnd + self.mss, MAX_CWND + 3 * self.mss)
        elif self.dupacks >= self._dupack_threshold(in_flight) \
                and seq_diff(self.snd_una, self.recover) >= 0:
            # Fast retransmit + fast recovery: halve the window. The
            # `recover` check stops a second halving for the same window
            # of data (e.g. duplicates caused by our own go-back-N resends).
            self.ssthresh = max(in_flight // 2, 2 * self.mss)
            self.recover = self.snd_max
            self.in_recovery = True
            self._retransmit_first()
            self.cwnd = self.ssthresh + 3 * self.mss

    def _check_timers(self):
        now = time.monotonic()
        if (self.snd_una != self.snd_max or self.sndbuf) \
                and now - self.last_progress >= IDLE_GIVE_UP:
            raise ConnectionTimeout('nothing acknowledged for %g s' % IDLE_GIVE_UP)
        if self.rto_deadline is not None and now >= self.rto_deadline:
            self._on_timeout()

    def _on_timeout(self):
        """Retransmission timeout: back to one segment and slow start, and
        resend everything from the first unacknowledged byte (go-back-N; the
        receiver's cumulative ACKs skip whatever it had buffered)."""
        self.rto_deadline = None
        in_flight = seq_diff(self.snd_max, self.snd_una)
        if in_flight == 0:
            return
        self.ssthresh = max(in_flight // 2, 2 * self.mss)
        self.cwnd = self.mss
        self.ca_acked = 0
        self.dupacks = 0
        self.in_recovery = False
        self.recover = self.snd_max
        self.snd_nxt = self.snd_una
        self.rtt_seq = None
        self.rto = min(self.rto * 2, RTO_MAX)     # exponential backoff, capped at 1 s
        self._output()

    # ------------------------------------------------------------------
    # Round-trip time estimation (RFC 6298)

    def _rtt_sample(self, rtt):
        if self.srtt is None:
            self.srtt = rtt
            self.rttvar = rtt / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - rtt)
            self.srtt = 0.875 * self.srtt + 0.125 * rtt
        self.rto = self._rto_from_estimates()

    def _rto_from_estimates(self):
        if self.srtt is None:
            return RTO_INITIAL
        return min(max(self.srtt + 4 * self.rttvar, RTO_MIN), RTO_MAX)


def local_address_for(remote_ip):
    """Source address the routing table picks for `remote_ip`. Connecting a
    UDP socket sends nothing; it only makes the kernel choose a route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((remote_ip, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def connect(remote_ip, remote_port, log_path=None):
    """Open a connection to remote_ip:remote_port (client side)."""
    local_ip = local_address_for(remote_ip)
    local_port = random.randint(61000, 65535)            # T13
    raw = RawSocket(local_port, log_path)
    conn = TCPConnection(raw, local_ip, local_port, remote_ip, remote_port)
    conn._active_open()
    return conn


class Listener:
    """Accepts connections on one port, one at a time (server side)."""

    def __init__(self, port, log_path=None):
        self.port = port
        self.raw = RawSocket(port, log_path)

    def accept(self):
        """Wait for a SYN and complete the handshake. Failed handshakes are
        abandoned and we keep listening. The connection replies from the
        address the SYN was sent to."""
        while True:
            syn = self._wait_for_syn()
            conn = TCPConnection(self.raw, syn.dst, self.port, syn.src, syn.sport)
            try:
                conn._passive_open(syn)
                return conn
            except TCPError:
                continue

    def _wait_for_syn(self):
        while True:
            pkt = self.raw.receive(60.0)
            if pkt is None:
                continue
            ports = peek_ports(pkt)
            if ports is None or ports[3] != self.port:
                continue
            seg = parse_packet(pkt)
            if seg is not None and seg.flags & (SYN | ACK | RST) == SYN:
                self.raw.log_segment('RECV', seg)
                return seg
