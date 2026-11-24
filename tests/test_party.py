"""A Watch Party room session: ticket, socket, snapshots, commands, clock and reconnects."""

import json
import queue
import time
import unittest

from resources.lib.silo import party, wsclient

# The class party catches: other test modules import the api module again under their stubs.
ApiError = party.ApiError

ROOM = {"room_id": "r1", "phase": "lobby", "generation": 3, "self_role": "guest", "members": []}


class FakeSocket:
    def __init__(self, *incoming):
        self.incoming = queue.Queue()
        for item in incoming:
            self.incoming.put(item)
        self.sent, self.closed = [], False

    def recv(self):
        item = self.incoming.get(timeout=5)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    def send_text(self, text):
        if self.closed:
            raise wsclient.Closed(1000, "closed")
        self.sent.append(json.loads(text))

    def feed(self, frame):
        self.incoming.put(frame)

    def close(self, code=1000, reason=""):
        self.closed = True
        self.incoming.put(wsclient.Closed(code, reason))


class FakeClient:
    server_url = "https://silo.example"

    def __init__(self, tickets=None, suggestions=None):
        self.tickets = list(tickets or [])
        self.suggestions = list(suggestions or [])
        self.calls = []

    def request(self, method, path, body=None, params=None, headers=None):
        self.calls.append((method, path, headers))
        if path.endswith("/suggestions"):
            return {"items": [dict(entry) for entry in self.suggestions], "page": {"has_more": False}}
        if path.endswith("/ws-ticket"):
            answer = self.tickets.pop(0) if self.tickets else {"ticket": "t-more"}
            if isinstance(answer, Exception):
                raise answer
            return answer
        if method == "GET":
            return {"room": dict(ROOM, generation=2), "room_access_token": "eyJ.renewed"}
        return None


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class RoomSessionTests(unittest.TestCase):
    def open(self, *sockets, tickets=None, suggestions=None):
        self.client = FakeClient(tickets or [{"ticket": "t1"}, {"ticket": "t2"}, {"ticket": "t3"}], suggestions)
        self.sockets = list(sockets)
        self.connects = []

        def connect(url, protocols, **kwargs):
            self.connects.append((url, protocols))
            if not self.sockets:
                raise wsclient.HandshakeError("no more sockets", 503)
            return self.sockets.pop(0)

        session = party.RoomSession(self.client, dict(ROOM), "eyJ.proof", connect=connect,
                                    backoff=(0.01, 0.01, 0.01, 0.01), ping_fast=0.05, ping_slow=0.2)
        self.addCleanup(session.leave)
        session.start()
        return session

    def test_it_connects_with_a_fresh_ticket_and_follows_the_snapshot(self):
        sock = FakeSocket({"type": "snapshot", "room": dict(ROOM, generation=4, member_count=2)})
        session = self.open(sock)
        self.assertTrue(wait_for(lambda: session.snapshot().get("member_count") == 2))
        url, protocols = self.connects[0]
        self.assertEqual(url, "wss://silo.example/api/v2/watch-together/rooms/r1/ws")
        self.assertEqual(protocols, ["silo.room.v2", "silo.ticket.t1"])
        self.assertEqual(self.client.calls[0][2], {"X-Room-Token": "eyJ.proof"})
        self.assertTrue(session.connected)

    def test_an_older_generation_never_replaces_a_newer_one(self):
        sock = FakeSocket({"type": "snapshot", "room": dict(ROOM, generation=5, phase="playing")},
                          {"type": "snapshot", "room": dict(ROOM, generation=4, phase="lobby")},
                          {"type": "snapshot", "room": dict(ROOM, generation=6, phase="playing", member_count=3)})
        session = self.open(sock)
        self.assertTrue(wait_for(lambda: session.snapshot().get("member_count") == 3))
        session.accept_http(dict(ROOM, generation=6, phase="lobby"), "eyJ.newer")
        self.assertEqual(session.snapshot()["phase"], "playing")  # The socket's wins a tie.
        self.assertEqual(session.token, "eyJ.newer")

    def test_the_newest_pending_command_wins(self):
        sock = FakeSocket({"type": "snapshot", "room": ROOM},
                          {"type": "transport_command", "command": {"command_id": "c1", "action": "pause"}},
                          {"type": "transport_command", "command": {"command_id": "c2", "action": "play"}})
        session = self.open(sock)
        self.assertTrue(wait_for(lambda: session.pending_commands() == 2))
        self.assertEqual(session.take_command()["command_id"], "c2")
        self.assertIsNone(session.take_command())

    def test_pings_go_out_and_pongs_feed_the_clock(self):
        sock = FakeSocket({"type": "snapshot", "room": ROOM})
        session = self.open(sock)
        self.assertTrue(wait_for(lambda: any(frame["type"] == "ping" for frame in sock.sent)))
        ping = next(frame for frame in sock.sent if frame["type"] == "ping")
        now = party.room_clock.format_instant(time.time() + 2.0, digits=9)
        sock.feed({"type": "pong", "client_sent_at": ping["client_sent_at"], "server_received_at": now,
                   "server_sent_at": now})
        self.assertTrue(wait_for(lambda: session.clock.now() is not None))
        self.assertAlmostEqual(session.clock.now() - time.time(), 2.0, delta=0.3)

    def test_a_dropped_socket_reconnects_with_a_new_ticket(self):
        first = FakeSocket({"type": "snapshot", "room": ROOM}, wsclient.Closed(4003, "lifetime_ended"))
        second = FakeSocket({"type": "snapshot", "room": dict(ROOM, generation=7)})
        session = self.open(first, second)
        self.assertTrue(wait_for(lambda: session.snapshot().get("generation") == 7))
        self.assertEqual([protocols[1] for _url, protocols in self.connects], ["silo.ticket.t1", "silo.ticket.t2"])
        self.assertIsNone(session.ended)

    def test_room_closed_and_connection_replaced_end_it_for_good(self):
        for frame, reason in (({"type": "room_closed", "reason": "host_left"}, "host_left"),
                              ({"type": "connection_replaced", "reason": "Another device."}, "replaced")):
            with self.subTest(reason=reason):
                sock = FakeSocket({"type": "snapshot", "room": ROOM}, frame)
                session = self.open(sock, FakeSocket())
                self.assertTrue(wait_for(lambda: session.ended == reason))
                time.sleep(0.1)
                self.assertEqual(len(self.connects), 1)  # No reconnect.
                self.assertTrue(sock.closed)

    def test_ticket_refusals_renew_the_proof_once_or_end_the_party(self):
        session = self.open(FakeSocket({"type": "snapshot", "room": ROOM}),
                            tickets=[ApiError(403, "Room access token required"), {"ticket": "t9"}])
        self.assertTrue(wait_for(lambda: session.connected))
        self.assertEqual(session.token, "eyJ.renewed")
        self.assertEqual(self.connects[0][1][1], "silo.ticket.t9")
        ended = self.open(tickets=[ApiError(409, "Room closed", "conflict")])
        self.assertTrue(wait_for(lambda: ended.ended == "ended"))

    def test_suggestions_load_on_connect_and_follow_the_socket_keeping_this_viewers_votes(self):
        made = {"room_id": "r1", "content_type": "movie", "subtitle": "", "poster_url": "", "note": ""}
        loaded = [dict(made, id="s1", title="First", vote_count=1, voted_by_me=False),
                  dict(made, id="s2", title="Second", vote_count=1, voted_by_me=True)]
        sock = FakeSocket({"type": "snapshot", "room": ROOM})
        session = self.open(sock, suggestions=loaded)
        self.assertTrue(wait_for(lambda: len(session.suggestion_list()) == 2))
        self.assertEqual([(entry["id"], entry["voted_by_me"]) for entry in session.suggestion_list()],
                         [("s1", False), ("s2", True)])  # Equal votes keep the order they were made in.
        self.assertTrue(any(call[1].endswith("/suggestions") and call[2] == {"X-Room-Token": "eyJ.proof"}
                            for call in self.client.calls))
        # The socket's list says voted_by_me false for everyone; this viewer's own votes stay.
        sock.feed({"type": "suggestions_update", "suggestions": [
            dict(made, id=7, title="Third", vote_count=0, voted_by_me=False, suggester_user_id=3),
            dict(made, id="s1", title="First", vote_count=2, voted_by_me=False),
            dict(made, id="s2", title="Second", vote_count=1, voted_by_me=False)]})
        self.assertTrue(wait_for(lambda: len(session.suggestion_list()) == 3))
        self.assertEqual([(entry["id"], entry["voted_by_me"]) for entry in session.suggestion_list()],
                         [("s1", False), ("s2", True), ("7", False)])  # Most votes first.
        session.note_vote("s1", True)
        session.note_vote("s2", False)
        self.assertEqual([entry["voted_by_me"] for entry in session.suggestion_list()], [True, False, False])

    def test_send_needs_a_connection_and_leave_closes_it(self):
        sock = FakeSocket({"type": "snapshot", "room": ROOM})
        session = self.open(sock)
        self.assertTrue(wait_for(lambda: session.connected))
        self.assertTrue(session.send({"type": "lobby_ready", "ready": True}))
        self.assertIn({"type": "lobby_ready", "ready": True}, sock.sent)
        session.leave()
        self.assertEqual(session.ended, "left")
        self.assertTrue(sock.closed)
        self.assertFalse(session.send({"type": "lobby_ready", "ready": False}))


if __name__ == "__main__":
    unittest.main()
