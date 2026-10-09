"""HTTP/1.1 client over our own TCP (Part C).

    run-client URL [-o OUTFILE] [--log LOGFILE]

Downloads one URL with a single GET and writes the body to OUTFILE (or
stdout); the status line goes to stderr.

Exit codes: 0 complete response with a 2xx status, 1 complete response
with any other status, 2 anything else (bad URL, DNS failure, refused,
timeout, incomplete body).
"""

import argparse
import re
import socket
import sys

import rawtcp

IDLE_TIMEOUT = 10.0     # give up if the server sends nothing for this long
MAX_LINE = 64 * 1024    # longest status/header/chunk-size line we accept


class HTTPError(Exception):
    """Malformed URL or response, or the response ended early."""


def parse_url(url):
    """Split http://HOST[:PORT][/PATH] into (host, port, host_header, target).

    The Host header is HOST[:PORT] exactly as written and the request target
    is PATH exactly as written (with any query string), '/' if absent."""
    m = re.fullmatch(r'[hH][tT][tT][pP]://([^/?#]*)([^#]*)(#.*)?', url)
    if not m:
        raise HTTPError('unsupported URL: %s' % url)
    authority, target = m.group(1), m.group(2)
    host, sep, port_text = authority.partition(':')
    if not host or '@' in authority or ':' in port_text:
        raise HTTPError('bad host in URL: %s' % url)
    port = 80
    if sep and port_text:
        if not re.fullmatch(r'[0-9]+', port_text) or not 0 < int(port_text) < 65536:
            raise HTTPError('bad port in URL: %s' % url)
        port = int(port_text)
    if not target:
        target = '/'
    elif target.startswith('?'):        # http://host?q=1 means /?q=1
        target = '/' + target
    return host, port, authority, target


class Reader:
    """Buffered reading from the connection for the response parser."""

    def __init__(self, conn):
        self.conn = conn
        self.buf = bytearray()
        self.eof = False

    def fill(self):
        """Read more bytes; False once the server has closed."""
        if self.eof:
            return False
        data = self.conn.recv(IDLE_TIMEOUT)
        if not data:
            self.eof = True
            return False
        self.buf += data
        return True

    def read_line(self):
        """One line including its '\\n'; raises HTTPError at end of stream."""
        while True:
            i = self.buf.find(b'\n')
            if i >= 0:
                line = bytes(self.buf[:i + 1])
                del self.buf[:i + 1]
                return line
            if len(self.buf) > MAX_LINE:
                raise HTTPError('line too long')
            if not self.fill():
                raise HTTPError('connection closed in the middle of the response')

    def copy_exact(self, n, out):
        """Copy exactly n body bytes to `out`."""
        while n > 0:
            if not self.buf and not self.fill():
                raise HTTPError('connection closed with %d body bytes missing' % n)
            take = min(n, len(self.buf))
            out.write(self.buf[:take])
            del self.buf[:take]
            n -= take

    def copy_to_eof(self, out):
        """Copy everything until the server closes the connection."""
        while True:
            if self.buf:
                out.write(self.buf)
                self.buf.clear()
            if not self.fill():
                return


def read_head(reader):
    """Read a status line and headers. Returns (status_line, code, headers)
    with header names lower-cased; repeated headers are joined by ','."""
    line = reader.read_line().rstrip(b'\r\n').decode('latin-1')
    m = re.match(r'HTTP/\d+\.\d+ (\d{3})(?: |$)', line)
    if not m:
        raise HTTPError('bad status line: %r' % line)
    headers = {}
    name = None
    while True:
        raw = reader.read_line()
        text = raw.rstrip(b'\r\n').decode('latin-1')
        if not text:
            break
        if text[0] in ' \t' and name:        # obsolete line folding
            headers[name] += ' ' + text.strip()
            continue
        name, colon, value = text.partition(':')
        if not colon:
            raise HTTPError('bad header line: %r' % text)
        name = name.strip().lower()
        value = value.strip()
        headers[name] = headers[name] + ',' + value if name in headers else value
    return line, int(m.group(1)), headers


def read_chunked(reader, out):
    """Transfer-Encoding: chunked, ignoring chunk extensions and trailers."""
    while True:
        size_text = reader.read_line().split(b';', 1)[0].strip()
        if not re.fullmatch(rb'[0-9a-fA-F]+', size_text):
            raise HTTPError('bad chunk size: %r' % size_text)
        size = int(size_text, 16)
        if size == 0:
            break
        reader.copy_exact(size, out)
        if reader.read_line().strip():
            raise HTTPError('missing CRLF after chunk')
    # Trailer section up to the final empty line. The body is already
    # complete, so a server closing before that last CRLF is tolerated.
    try:
        while reader.read_line().strip():
            pass
    except HTTPError:
        pass


def fetch(conn, request, output_path):
    """Send the request and save the body. Returns the status code once the
    response is complete; raises HTTPError/TCPError otherwise."""
    conn.sendall(request)
    reader = Reader(conn)
    while True:
        status_line, code, headers = read_head(reader)
        if 100 <= code < 200 and code != 101:
            continue                    # interim response; the real one follows
        break
    print(status_line, file=sys.stderr)

    out = open(output_path, 'wb') if output_path else sys.stdout.buffer
    try:
        codings = [c.strip().lower() for c in headers.get('transfer-encoding', '').split(',')]
        if code in (204, 304) or 100 <= code < 200:
            pass                        # these responses never have a body
        elif codings[-1] == 'chunked':
            read_chunked(reader, out)
        elif 'transfer-encoding' in headers:
            reader.copy_to_eof(out)     # other codings: body ends at close
        elif 'content-length' in headers:
            values = {v.strip() for v in headers['content-length'].split(',')}
            length = values.pop() if len(values) == 1 else ''
            if not re.fullmatch(r'[0-9]+', length):
                raise HTTPError('bad Content-Length')
            reader.copy_exact(int(length), out)
        else:
            reader.copy_to_eof(out)     # body ends when the server closes
    finally:
        out.flush()
        if output_path:
            out.close()
    return code


def main():
    parser = argparse.ArgumentParser(description='HTTP client over raw-socket TCP')
    parser.add_argument('url')
    parser.add_argument('-o', dest='output', metavar='OUTFILE')
    parser.add_argument('--log', metavar='LOGFILE')
    args = parser.parse_args()          # usage errors exit with code 2

    try:
        host, port, host_header, target = parse_url(args.url)
        ip = socket.gethostbyname(host)
    except (HTTPError, OSError, UnicodeError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 2

    request = ('GET %s HTTP/1.1\r\n'
               'Host: %s\r\n'
               'User-Agent: col334-client/1.0\r\n'
               'Accept: */*\r\n'
               'Connection: close\r\n'
               '\r\n' % (target, host_header)).encode('latin-1')

    try:
        conn = rawtcp.connect(ip, port, args.log)
    except (rawtcp.TCPError, OSError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 2
    try:
        code = fetch(conn, request, args.output)
    except (HTTPError, OSError, UnicodeError) as e:
        print('error: %s' % e, file=sys.stderr)
        code = None
    except rawtcp.TCPError as e:
        # Reset or timed out: the connection is unusable, nothing to close.
        print('error: %s' % e, file=sys.stderr)
        return 2
    try:
        conn.close()                    # T9, once the response is complete
    except rawtcp.TCPError:
        pass
    if code is None:
        return 2
    return 0 if 200 <= code < 300 else 1


if __name__ == '__main__':
    sys.exit(main())
