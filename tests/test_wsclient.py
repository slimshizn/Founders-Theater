"""The Watch Party WebSocket client against a scripted peer on a socket pair."""

import base64
import hashlib
import socket
import struct
import unittest

from resources.lib.silo import wsclient

KEY = base64.b64encode(b"0123456789abcdef").decode()
ACCEPT = base64.b64encode(hashlib.sha1((KEY + wsclient.GUID).encode()).digest()).decode()


def upgrade(protocol="silo.room.v2", accept=ACCEPT, status="101 Switching Protocols"):
    return ("HTTP/1.1 {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {}\r\n"
            "Sec-WebSocket-Protocol: {}\r\n\r\n").format(status, accept, protocol).encode()


def frame(opcode, payload=b"", fin=True):
    head = bytes([(0x80 if fin else 0) | opcode])
    if len(payload) < 126:
        head += bytes([len(payload)])
    elif len(payload) < 65536:
        head += bytes([126]) + struct.pack("!H", len(payload))
    else:
        head += bytes([127]) + struct.pack("!Q", len(payload))
    return head + payload


def read_frame(peer):
    """One client frame as (fin, opcode, unmasked payload); client frames must be masked."""
    first, second = peer.recv(2)
    assert second & 0x80, "client frames must be masked"
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", peer.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", peer.recv(8))[0]
    mask = peer.recv(4)
    data = b""
    while len(data) < length:
        data += peer.recv(length - len(data))
    return bool(first & 0x80), first & 0x0F, bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))


class HandshakeTests(unittest.TestCase):
    def setUp(self):
        self.client, self.peer = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.peer.close)

    def connect(self, response):
        self.peer.sendall(response)
        return wsclient.connect("wss://silo.example/api/v2/watch-together/rooms/r1/ws",
                                ["silo.room.v2", "silo.ticket.abc"], sock=self.client, key=KEY)

    def test_the_upgrade_offers_both_protocols_and_no_credentials(self):
        connection = self.connect(upgrade())
        request = self.peer.recv(4096).decode()
        lines = request.split("\r\n")
        self.assertEqual(lines[0], "GET /api/v2/watch-together/rooms/r1/ws HTTP/1.1")
        headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
        self.assertEqual(headers["Host"], "silo.example")
        self.assertEqual(headers["Sec-WebSocket-Version"], "13")
        self.assertEqual(headers["Sec-WebSocket-Key"], KEY)
        self.assertEqual(headers["Sec-WebSocket-Protocol"], "silo.room.v2, silo.ticket.abc")
        for name in ("Origin", "Authorization", "X-Profile-Id", "Cookie"):
            self.assertNotIn(name, headers)
        self.assertIsInstance(connection, wsclient.WebSocket)

    def test_a_refusal_or_a_bad_answer_raises(self):
        refusal = b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 26\r\n\r\ninvalid room credential\r\n"
        with self.assertRaises(wsclient.HandshakeError) as caught:
            self.connect(refusal)
        self.assertEqual(caught.exception.status, 401)
        for response in (upgrade(accept="wrong"), upgrade(protocol="other")):
            client, peer = socket.socketpair()
            peer.sendall(response)
            with self.assertRaises(wsclient.HandshakeError):
                wsclient.connect("ws://silo.example/ws", ["silo.room.v2"], sock=client, key=KEY)
            client.close()
            peer.close()


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.client, self.peer = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.peer.close)
        self.peer.sendall(upgrade())
        self.ws = wsclient.connect("ws://silo.example/ws", ["silo.room.v2"], sock=self.client, key=KEY)
        self.peer.recv(4096)  # The upgrade request.

    def test_text_goes_out_masked_and_comes_in_whole(self):
        self.ws.send_text('{"type":"ping"}')
        self.assertEqual(read_frame(self.peer), (True, 0x1, b'{"type":"ping"}'))
        long_text = "x" * 300
        self.ws.send_text(long_text)
        self.assertEqual(read_frame(self.peer)[2], long_text.encode())
        self.peer.sendall(frame(0x1, b'{"type":"snapshot"}') + frame(0x1, b"a" * 70000))
        self.assertEqual(self.ws.recv(), '{"type":"snapshot"}')
        self.assertEqual(self.ws.recv(), "a" * 70000)

    def test_fragments_are_joined_and_pings_answered_on_the_way(self):
        self.peer.sendall(frame(0x1, b'{"ty', fin=False) + frame(0x9, b"hb") + frame(0x0, b'pe":1}'))
        self.assertEqual(self.ws.recv(), '{"type":1}')
        self.assertEqual(read_frame(self.peer), (True, 0xA, b"hb"))

    def test_a_close_frame_reports_its_code_and_is_answered(self):
        self.peer.sendall(frame(0x8, struct.pack("!H", 4003) + b"lifetime_ended"))
        with self.assertRaises(wsclient.Closed) as caught:
            self.ws.recv()
        self.assertEqual((caught.exception.code, caught.exception.reason), (4003, "lifetime_ended"))
        fin, opcode, payload = read_frame(self.peer)
        self.assertEqual((opcode, struct.unpack("!H", payload[:2])[0]), (0x8, 4003))

    def test_the_peer_going_away_is_an_abnormal_close(self):
        self.peer.shutdown(socket.SHUT_WR)
        with self.assertRaises(wsclient.Closed) as caught:
            self.ws.recv()
        self.assertEqual(caught.exception.code, 1006)

    def test_close_sends_a_close_frame_once(self):
        self.ws.close(1000)
        self.ws.close(1000)
        fin, opcode, payload = read_frame(self.peer)
        self.assertEqual((opcode, struct.unpack("!H", payload[:2])[0]), (0x8, 1000))
        with self.assertRaises(wsclient.Closed):
            self.ws.send_text("late")


if __name__ == "__main__":
    unittest.main()
