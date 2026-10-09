"""HTTP/1.1 file server over our own TCP (Part D).

    run-server [-p PORT] [-d DOCROOT] [--log LOGFILE]

Serves files from DOCROOT, one connection at a time, until killed. Each
connection gets exactly one response and is then closed.
"""

import argparse
import email.utils
import os
import re
import sys
import time

import rawtcp

HEAD_TIMEOUT = 10.0         # close without answering if the head takes longer
MAX_HEAD = 64 * 1024
FILE_CHUNK = 64 * 1024

REASONS = {
    200: 'OK',
    400: 'Bad Request',
    403: 'Forbidden',
    404: 'Not Found',
    501: 'Not Implemented',
}

CONTENT_TYPES = {
    '.html': 'text/html',
    '.htm': 'text/html',
    '.txt': 'text/plain',
    '.css': 'text/css',
    '.js': 'application/javascript',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.pdf': 'application/pdf',
}

# RFC 9110 token characters, for the method.
TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
VERSION = re.compile(r'HTTP/[0-9]\.[0-9]')


def content_type(path):
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(),
                             'application/octet-stream')


def read_request_head(conn):
    """Read until the blank line ending the request head. Returns the head,
    or None if the client closed or the head was not complete within
    HEAD_TIMEOUT seconds."""
    deadline = time.monotonic() + HEAD_TIMEOUT
    head = bytearray()
    while True:
        text = head.lstrip(b'\r\n')     # tolerate empty lines before the request
        for end in (b'\r\n\r\n', b'\n\n'):
            i = text.find(end)
            if i >= 0:
                return bytes(text[:i])
        if len(head) > MAX_HEAD:
            return bytes(head)          # oversized: let it fail as a bad request
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            data = conn.recv(remaining)
        except rawtcp.ConnectionTimeout:
            return None
        if not data:
            return None
        head += data


def choose_response(head, docroot):
    """Apply Table 4. Returns (status, method, file_path or None)."""
    request_line = head.split(b'\n', 1)[0].rstrip(b'\r').decode('latin-1')
    parts = request_line.split(' ')
    if (len(parts) != 3 or not TOKEN.fullmatch(parts[0])
            or not parts[1].startswith('/') or not VERSION.fullmatch(parts[2])):
        return 400, None, None
    method, target = parts[0], parts[1]
    if method not in ('GET', 'HEAD'):
        return 501, method, None
    path = target.split('?', 1)[0]      # the query string is ignored
    if '..' in path.split('/'):
        return 403, method, None
    file_path = os.path.join(docroot, path.lstrip('/'))
    try:
        if path.endswith('/') or os.path.isdir(file_path):
            file_path = os.path.join(file_path, 'index.html')
        if not os.path.isfile(file_path):
            return 404, method, None
    except ValueError:                  # e.g. a NUL byte in the path
        return 404, method, None
    return 200, method, file_path


def response_head(status, ctype, length):
    return ('HTTP/1.1 %d %s\r\n'
            'Date: %s\r\n'
            'Server: col334-server/1.0\r\n'
            'Content-Type: %s\r\n'
            'Content-Length: %d\r\n'
            'Connection: close\r\n'
            '\r\n' % (status, REASONS[status], email.utils.formatdate(usegmt=True),
                      ctype, length)).encode('latin-1')


def error_body(status):
    title = '%d %s' % (status, REASONS[status])
    return ('<!DOCTYPE html>\n<html><head><title>%s</title></head>'
            '<body><h1>%s</h1></body></html>\n' % (title, title)).encode()


def serve(conn, docroot):
    """Handle one connection: read the head, send one response, close."""
    peer = '%s:%d' % (conn.remote_ip, conn.remote_port)
    head = read_request_head(conn)
    if head is None:
        print('%s: no complete request, closing' % peer, file=sys.stderr)
        conn.close()
        return

    status, method, file_path = choose_response(head, docroot)
    body_file = None
    if status == 200:
        try:
            body_file = open(file_path, 'rb')
        except OSError:
            status = 404
    try:
        if body_file is not None:
            length = os.fstat(body_file.fileno()).st_size
            head_bytes = response_head(200, content_type(file_path), length)
        else:
            body = error_body(status)
            head_bytes = response_head(status, 'text/html', len(body))
        print('%s: %s -> %d' % (peer, head.split(b'\n', 1)[0].rstrip(b'\r')
                                .decode('latin-1'), status), file=sys.stderr)

        if method == 'HEAD':            # same headers as GET, no body
            conn.sendall(head_bytes)
        elif body_file is None:
            conn.sendall(head_bytes + body)
        else:
            pending = head_bytes        # sent together with the first chunk
            while True:
                chunk = body_file.read(FILE_CHUNK)
                if not chunk:
                    break
                conn.sendall(pending + chunk)
                pending = b''
            if pending:
                conn.sendall(pending)
    finally:
        if body_file is not None:
            body_file.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='HTTP server over raw-socket TCP')
    parser.add_argument('-p', dest='port', type=int, default=8080, metavar='PORT')
    parser.add_argument('-d', dest='docroot', default='./www', metavar='DOCROOT')
    parser.add_argument('--log', metavar='LOGFILE')
    args = parser.parse_args()
    if not 0 < args.port < 65536:
        parser.error('port must be between 1 and 65535')

    listener = rawtcp.Listener(args.port, args.log)
    print('serving %s on port %d' % (os.path.abspath(args.docroot), args.port),
          file=sys.stderr)
    while True:
        try:
            conn = listener.accept()
            serve(conn, args.docroot)
        except KeyboardInterrupt:
            return 0
        except Exception as e:          # a bad client must never stop the server
            print('connection failed: %s' % e, file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main())
