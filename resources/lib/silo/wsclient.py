"""A small WebSocket client (RFC 6455) for Silo's Watch Party room socket.

Kodi's Python has no WebSocket module, so this is one on ``socket`` and ``ssl``:
text frames, fragments, ping and pong, close, and nothing else (no extensions).
One thread reads with ``recv()``, which answers the server's pings as they come;
``send_text()`` and ``close()`` may be called from any thread.

The upgrade carries no Origin, cookie or account headers: Silo's room socket takes
its single-use ticket as a subprotocol, and native clients send no Origin.
"""

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
TEXT, BINARY, CONTINUATION, CLOSE, PING, PONG = 0x1, 0x2, 0x0, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    pass


class HandshakeError(WebSocketError):
    """The server refused the upgrade (``status``, e.g. 401) or answered it wrongly (0)."""

    def __init__(self, message, status=0, body=""):
        super().__init__(message)
        self.status, self.body = status, body


class Closed(WebSocketError):
    """The connection ended: ``code`` from the close frame, or 1006 when it simply dropped."""

    def __init__(self, code=1006, reason=""):
        super().__init__("WebSocket closed ({})".format(code))
        self.code, self.reason = code, reason


def _accept(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def connect(url, subprotocols, timeout=10.0, sock=None, context=None, key=None, read_timeout=60.0):
    """Open ``url`` (ws:// or wss://) offering ``subprotocols``; the server must pick the first.

    ``read_timeout`` bounds a silent connection: Silo pings every 20 seconds.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise WebSocketError("Not a WebSocket URL.")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    if sock is None:
        sock = socket.create_connection((host, port), timeout=timeout)
        if parts.scheme == "wss":
            sock = (context or ssl.create_default_context()).wrap_socket(sock, server_hostname=host)
    key = key or base64.b64encode(os.urandom(16)).decode("ascii")
    authority = host if parts.port is None else "{}:{}".format(host, parts.port)
    if ":" in host and not host.startswith("["):
        authority = "[{}]".format(host) + ("" if parts.port is None else ":{}".format(parts.port))
    request = ["GET {} HTTP/1.1".format(parts.path or "/"), "Host: " + authority, "Upgrade: websocket",
               "Connection: Upgrade", "Sec-WebSocket-Key: " + key, "Sec-WebSocket-Version: 13",
               "Sec-WebSocket-Protocol: " + ", ".join(subprotocols), "", ""]
    sock.sendall("\r\n".join(request).encode("ascii"))
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise HandshakeError("The server closed the connection during the upgrade.")
        data += chunk
        if len(data) > MAX_HEADER_BYTES:
            raise HandshakeError("The upgrade answer is too large.")
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status_parts = lines[0].split(" ", 2)
    status = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    if status != 101:
        body = rest[:512].decode("utf-8", "replace").strip()
        raise HandshakeError("The server refused the WebSocket upgrade ({}).".format(status), status, body)
    if headers.get("sec-websocket-accept") != _accept(key):
        raise HandshakeError("The server's upgrade answer does not match this request.")
    if subprotocols and headers.get("sec-websocket-protocol") != subprotocols[0]:
        raise HandshakeError("The server did not accept the room protocol.")
    sock.settimeout(read_timeout)
    return WebSocket(sock, rest)


class WebSocket:
    def __init__(self, sock, buffered=b""):
        self.sock = sock
        self.buffer = bytearray(buffered)
        self.send_lock = threading.Lock()
        self.close_sent = False

    # ------------------------------------------------------------ sending
    def send_text(self, text):
        self._send(TEXT, text.encode("utf-8"))

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        with self.send_lock:
            if self.close_sent and opcode != CLOSE:
                raise Closed(1000, "closed")
            try:
                self.sock.sendall(head + mask + masked)
            except OSError as error:
                raise Closed(1006, "") from error

    def close(self, code=1000, reason=""):
        """Send a close frame once and end the connection; the reader then sees Closed."""
        with self.send_lock:
            if self.close_sent:
                return
            self.close_sent = True
        try:
            self._send(CLOSE, struct.pack("!H", code) + reason.encode("utf-8")[:120])
        except Closed:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    # ------------------------------------------------------------ receiving
    def _read(self, count):
        while len(self.buffer) < count:
            try:
                chunk = self.sock.recv(max(4096, count - len(self.buffer)))
            except (OSError, ValueError) as error:  # socket.timeout is an OSError.
                raise Closed(1006, "") from error
            if not chunk:
                raise Closed(1006, "")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def _frame(self):
        first, second = self._read(2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read(8))[0]
        if length > MAX_MESSAGE_BYTES:
            raise Closed(1009, "message too big")
        mask = self._read(4) if second & 0x80 else None
        payload = self._read(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bool(first & 0x80), first & 0x0F, payload

    def recv(self):
        """The next whole text message; pings are answered here. Raises Closed at the end."""
        parts, size = [], 0
        while True:
            fin, opcode, payload = self._frame()
            if opcode == PING:
                self._send(PONG, payload)
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                reason = payload[2:].decode("utf-8", "replace")
                self.close(code if code not in (1005, 1006) else 1000)
                raise Closed(code, reason)
            if opcode in (TEXT, BINARY, CONTINUATION):
                parts.append(payload)
                size += len(payload)
                if size > MAX_MESSAGE_BYTES:
                    raise Closed(1009, "message too big")
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")
