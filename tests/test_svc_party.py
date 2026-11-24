"""Silo Watch Party ("watch together") calls under /api/v2/watch-together."""

import base64
import json
import unittest

from resources.lib.silo import svc_party
from resources.lib.silo.api import ApiError


class FakeClient:
    def __init__(self, routes=None):
        self.routes, self.calls = routes or {}, []

    def request(self, method, path, body=None, params=None, headers=None):
        self.calls.append((method, path, body, headers))
        answer = self.routes.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        return answer


ROOM = {"room_id": "r1", "phase": "lobby", "code": "ABCD2345"}


def capabilities(**room):
    document = {"state": "available", "allowed": True, "socket_protocol": "silo.room.v2",
                "connection_replaced": True, "lobby_ready": True, "staged_selection": True, "stop_playback": True}
    document.update(room)
    return document


def proof(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"room_id": "r1", "exp": exp}).encode()).decode().rstrip("=")
    return "eyJhbGciOiJIUzI1NiJ9." + payload + ".sig"


class SupportTests(unittest.TestCase):
    def client(self, room, features=("watch_party_coordinator_v1", "fixed_media_file_v1")):
        return FakeClient({("GET", "/watch-together/capabilities"): room,
                           ("GET", "/playback/capabilities"): {"features": list(features)}})

    def test_a_full_server_lets_this_profile_join_and_host(self):
        self.assertEqual(svc_party.support(self.client(capabilities())),
                         {"guest": True, "host": True, "lobby_ready": True})

    def test_joining_needs_the_room_socket_and_both_playback_features(self):
        for room, features in ((capabilities(socket_protocol=""), None), (capabilities(allowed=False), None),
                               (capabilities(connection_replaced=False), None),
                               (capabilities(state="disabled"), None),
                               (capabilities(), ("fixed_media_file_v1",))):
            client = self.client(room, features) if features else self.client(room)
            self.assertFalse(svc_party.support(client)["guest"], room)

    def test_hosting_needs_staging_and_stop(self):
        support = svc_party.support(self.client(capabilities(staged_selection=False)))
        self.assertEqual((support["guest"], support["host"]), (True, False))

    def test_an_old_server_supports_nothing(self):
        client = FakeClient({("GET", "/watch-together/capabilities"): ApiError(404, "Not found")})
        self.assertEqual(svc_party.support(client), {"guest": False, "host": False, "lobby_ready": False})


class CallTests(unittest.TestCase):
    def test_codes_are_normalized_as_android_does(self):
        self.assertEqual(svc_party.normalize_code(" abcd-2345 "), "ABCD2345")
        self.assertEqual(svc_party.normalize_code("ab cd 23 45"), "ABCD2345")
        for bad in ("ABCD234", "ABCD23450", "ABCD2I45", "ABCD2O45", "", None):
            self.assertIsNone(svc_party.normalize_code(bad), bad)

    def test_join_and_create_answer_the_room_and_its_proof(self):
        client = FakeClient({("POST", "/watch-together/join"): {"room": ROOM, "room_access_token": "eyJ.a"},
                             ("POST", "/watch-together/rooms"): {"room": ROOM, "room_access_token": "eyJ.b"}})
        self.assertEqual(svc_party.join(client, "ABCD2345"), (ROOM, "eyJ.a"))
        self.assertEqual(client.calls[-1][2], {"code": "ABCD2345"})
        self.assertEqual(svc_party.create(client, "r1"), (ROOM, "eyJ.b"))
        self.assertEqual(client.calls[-1][2], {"room_id": "r1", "selection_mode": "host_pick"})

    def test_member_calls_carry_the_room_proof_and_host_calls_do_not(self):
        client = FakeClient({("GET", "/watch-together/rooms/r%201"): {"room": ROOM, "room_access_token": "eyJ.c"},
                             ("POST", "/watch-together/rooms/r%201/ws-ticket"): {"ticket": "t" * 43}})
        self.assertEqual(svc_party.read(client, "r 1", "eyJ.a"), (ROOM, "eyJ.c"))
        self.assertEqual(client.calls[-1][3], {"X-Room-Token": "eyJ.a"})
        self.assertEqual(svc_party.ticket(client, "r 1", "eyJ.a"), "t" * 43)
        self.assertEqual(client.calls[-1][3], {"X-Room-Token": "eyJ.a"})
        svc_party.stage(client, "r 1", "movie:1", file_id=42)
        self.assertEqual(client.calls[-1][:3], ("PUT", "/watch-together/rooms/r%201/staged-selection",
                                                {"content_id": "movie:1", "file_id": "42"}))
        svc_party.start(client, "r 1")
        svc_party.stop(client, "r 1")
        svc_party.set_policy(client, "r 1", "guest_play_pause")
        self.assertEqual([call[:2] for call in client.calls[-3:]],
                         [("POST", "/watch-together/rooms/r%201/playback/start"),
                          ("POST", "/watch-together/rooms/r%201/playback/stop"),
                          ("PATCH", "/watch-together/rooms/r%201/policy")])
        self.assertEqual(client.calls[-1][2], {"guest_control_policy": "guest_play_pause"})
        self.assertTrue(all(call[3] is None for call in client.calls[-4:]))

    def test_ending_an_ended_room_is_fine(self):
        client = FakeClient({("DELETE", "/watch-together/rooms/r1"): ApiError(409, "Ended", "conflict")})
        svc_party.close(client, "r1")
        client.routes[("DELETE", "/watch-together/rooms/r1")] = ApiError(403, "Not the host")
        with self.assertRaises(ApiError):
            svc_party.close(client, "r1")

    def test_the_socket_url_and_the_proofs_expiry(self):
        self.assertEqual(svc_party.socket_url("https://silo.example/prefix", "r1"),
                         "wss://silo.example/prefix/api/v2/watch-together/rooms/r1/ws")
        self.assertEqual(svc_party.socket_url("http://192.0.2.4:8096", "r1"),
                         "ws://192.0.2.4:8096/api/v2/watch-together/rooms/r1/ws")
        self.assertEqual(svc_party.proof_expiry(proof(1795550400)), 1795550400)
        self.assertIsNone(svc_party.proof_expiry("not-a-token"))


if __name__ == "__main__":
    unittest.main()
