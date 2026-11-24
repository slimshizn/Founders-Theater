"""Silo Watch Party ("watch together") calls under /api/v2/watch-together."""

import base64
import json
import unittest

from resources.lib.silo import svc_party

# The class svc_party catches: other test modules import the api module again under their stubs.
ApiError = svc_party.ApiError


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
                         {"guest": True, "host": True, "lobby_ready": True, "vote": False, "mode_switch": False,
                          "picker": False, "fallback": False, "member_state": False})

    def test_member_state_follows_its_flag_for_anyone_who_can_join(self):
        self.assertTrue(svc_party.support(self.client(capabilities(member_state=True)))["member_state"])
        guest_only = svc_party.support(self.client(capabilities(member_state=True, staged_selection=False)))
        self.assertTrue(guest_only["member_state"])
        self.assertFalse(svc_party.support(self.client(capabilities(member_state=True, allowed=False)))["member_state"])

    def test_voting_mode_switching_the_picker_and_fallback_follow_their_flags(self):
        room = capabilities(vote_host_override=True, selection_mode_switch=True, picker=True)
        features = ("watch_party_coordinator_v1", "fixed_media_file_v1", "watch_party_source_fallback_v1")
        support = svc_party.support(self.client(room, features))
        self.assertEqual({key: support[key] for key in ("vote", "mode_switch", "picker", "fallback")},
                         {"vote": True, "mode_switch": True, "picker": True, "fallback": True})
        # A profile that can only join may still use the picker and fall back, but not run votes.
        guest_only = svc_party.support(self.client(dict(room, staged_selection=False), features))
        self.assertEqual({key: guest_only[key] for key in ("host", "vote", "mode_switch", "picker", "fallback")},
                         {"host": False, "vote": False, "mode_switch": False, "picker": True, "fallback": True})

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
        self.assertEqual(svc_party.support(client), {"guest": False, "host": False, "lobby_ready": False,
                                                     "vote": False, "mode_switch": False, "picker": False,
                                                     "fallback": False, "member_state": False})


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
        svc_party.create(client, "r2", "vote")
        self.assertEqual(client.calls[-1][2], {"room_id": "r2", "selection_mode": "vote"})
        self.assertEqual(svc_party.join(client, join_token="T" * 24), (ROOM, "eyJ.a"))
        self.assertEqual(client.calls[-1][2], {"join_token": "T" * 24})

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
        self.assertEqual(svc_party.socket_url("http://silo.example:8096", "r1"),
                         "ws://silo.example:8096/api/v2/watch-together/rooms/r1/ws")
        self.assertEqual(svc_party.proof_expiry(proof(1795550400)), 1795550400)
        self.assertIsNone(svc_party.proof_expiry("not-a-token"))


SUGGESTION = {"id": "s1", "room_id": "r1", "suggester_user_id": "7", "suggester_profile_id": "p7",
              "content_id": "movie:1", "content_type": "movie", "title": "Example Film", "subtitle": "2001",
              "poster_url": "", "note": "", "vote_count": 2, "voted_by_me": True,
              "created_at": "2026-11-24T20:00:00.000Z"}


class VotingTests(unittest.TestCase):
    PATH = "/watch-together/rooms/r1"

    def test_suggestions_follow_every_page_with_the_room_proof(self):
        pages = iter([{"items": [SUGGESTION], "page": {"has_more": True, "next_cursor": "c2"}},
                      {"items": [dict(SUGGESTION, id="s2")], "page": {"has_more": False}}])
        client = FakeClient()
        params_seen = []

        def request(method, path, body=None, params=None, headers=None):
            client.calls.append((method, path, body, headers))
            params_seen.append(params)
            return next(pages)

        client.request = request
        self.assertEqual([entry["id"] for entry in svc_party.suggestions(client, "r1", "eyJ.a")], ["s1", "s2"])
        self.assertEqual(params_seen, [{"limit": 200}, {"limit": 200, "cursor": "c2"}])
        self.assertTrue(all(call[3] == {"X-Room-Token": "eyJ.a"} for call in client.calls))

    def test_suggest_sends_a_fresh_identity_and_the_title(self):
        client = FakeClient({("POST", self.PATH + "/suggestions"): {"suggestion_id": "s9"}})
        self.assertEqual(svc_party.suggest(client, "r1", "eyJ.a", "episode:5", "episode", "Pilot",
                                           subtitle="Example Show · S1E1"), "s9")
        method, path, body, headers = client.calls[-1]
        self.assertEqual(headers, {"X-Room-Token": "eyJ.a"})
        self.assertEqual(len(body.pop("suggestion_id")), 36)
        self.assertEqual(body, {"content_id": "episode:5", "content_type": "episode", "title": "Pilot",
                                "subtitle": "Example Show · S1E1"})
        with self.assertRaises(ValueError):
            svc_party.suggest(client, "r1", "eyJ.a", "series:5", "series", "Example Show")

    def test_vote_unvote_and_remove(self):
        client = FakeClient({("DELETE", self.PATH + "/suggestions/s1"): ApiError(404, "Gone")})
        svc_party.vote(client, "r1", "eyJ.a", "s1")
        svc_party.vote(client, "r1", "eyJ.a", "s1", on=False)
        svc_party.unsuggest(client, "r1", "eyJ.a", "s1")  # Already gone is fine.
        self.assertEqual([call[:2] for call in client.calls],
                         [("POST", self.PATH + "/suggestions/s1/vote"),
                          ("DELETE", self.PATH + "/suggestions/s1/vote"),
                          ("DELETE", self.PATH + "/suggestions/s1")])
        self.assertTrue(all(call[3] == {"X-Room-Token": "eyJ.a"} for call in client.calls))
        client.routes[("DELETE", self.PATH + "/suggestions/s1")] = ApiError(403, "Not yours")
        with self.assertRaises(ApiError):
            svc_party.unsuggest(client, "r1", "eyJ.a", "s1")

    def test_promote_mode_and_play_for_everyone_answer_the_room(self):
        answer = {"room": ROOM, "room_access_token": "eyJ.n"}
        client = FakeClient({("POST", self.PATH + "/suggestions/promote"): answer,
                             ("PATCH", self.PATH + "/selection-mode"): answer,
                             ("PUT", self.PATH + "/selection"): answer})
        self.assertEqual(svc_party.promote(client, "r1", "eyJ.a", "s1"), (ROOM, "eyJ.n"))
        self.assertEqual(client.calls[-1][2:], ({"suggestion_id": "s1"}, {"X-Room-Token": "eyJ.a"}))
        self.assertEqual(svc_party.set_mode(client, "r1", "vote"), (ROOM, "eyJ.n"))
        self.assertEqual(client.calls[-1][2:], ({"selection_mode": "vote"}, None))
        self.assertEqual(svc_party.select(client, "r1", "movie:2", file_id=8), (ROOM, "eyJ.n"))
        self.assertEqual(client.calls[-1][2:], ({"content_id": "movie:2", "file_id": "8"}, None))
        with self.assertRaises(ValueError):
            svc_party.set_mode(client, "r1", "chaos")

    def test_the_picker_reads_with_the_room_proof(self):
        rows = {"members": [], "continue_together": [], "watchlist_union": []}
        client = FakeClient({("GET", self.PATH + "/picker"): rows})
        self.assertEqual(svc_party.picker(client, "r1", "eyJ.a"), rows)
        self.assertEqual(client.calls[-1][3], {"X-Room-Token": "eyJ.a"})

    def test_member_state_reads_the_named_titles_with_the_room_proof(self):
        answer = {"members": [], "items": []}
        client = FakeClient({("POST", self.PATH + "/member-state"): answer})
        self.assertEqual(svc_party.member_state(client, "r1", "eyJ.a", ["movie:1", "", "movie:1", 7, None]), answer)
        self.assertEqual(client.calls[-1][1:], (self.PATH + "/member-state", {"content_ids": ["movie:1", "7"]},
                                                {"X-Room-Token": "eyJ.a"}))
        svc_party.member_state(client, "r1", "eyJ.a", ["movie:{}".format(n) for n in range(250)])
        self.assertEqual(len(client.calls[-1][2]["content_ids"]), 200)  # The most one read takes.
        calls = len(client.calls)
        self.assertEqual(svc_party.member_state(client, "r1", "eyJ.a", ["", None]), {"members": [], "items": []})
        self.assertEqual(len(client.calls), calls)  # Nothing to ask about.

    def test_source_fallback_names_the_revision_file_and_reason(self):
        answer = {"room": dict(ROOM, selection_revision=4), "room_access_token": "eyJ.f"}
        client = FakeClient({("POST", self.PATH + "/source-fallback"): answer})
        self.assertEqual(svc_party.fallback(client, "r1", "eyJ.a", 3, 42, "no_alternate_version"),
                         (dict(ROOM, selection_revision=4), "eyJ.f"))
        self.assertEqual(client.calls[-1][2:], ({"selection_revision": 3, "failed_file_id": "42",
                                                 "reason": "no_alternate_version"}, {"X-Room-Token": "eyJ.a"}))
        with self.assertRaises(ValueError):
            svc_party.fallback(client, "r1", "eyJ.a", 3, 42, "transcode_start_failed")


class InvitationTests(unittest.TestCase):
    TOKEN = "ABCDEFGHJKLMNPQRSTUVWXYZ"

    def test_the_invitation_link_keeps_the_servers_path(self):
        path = "/rooms/join?token=" + self.TOKEN
        self.assertEqual(svc_party.invite_url("https://silo.example/media/", path),
                         "https://silo.example/media/rooms/join?token=" + self.TOKEN)
        self.assertIsNone(svc_party.invite_url("https://silo.example", "/elsewhere?token=x"))
        self.assertIsNone(svc_party.invite_url("https://silo.example", None))

    def test_codes_web_links_and_app_links_parse_as_android_does(self):
        self.assertEqual(svc_party.parse_invite(" abcd-2345 "), ("code", "ABCD2345"))
        self.assertEqual(svc_party.parse_invite("https://silo.example/rooms/join?token=" + self.TOKEN),
                         ("link", "https://silo.example", self.TOKEN))
        self.assertEqual(svc_party.parse_invite("http://silo.example:8096/media/rooms/join?token=a%2Bb#x"),
                         ("link", "http://silo.example:8096/media", "a+b"))
        self.assertEqual(svc_party.parse_invite(
            "silo://watch-party?server=https%3A%2F%2Fsilo.example%2F&token=" + self.TOKEN),
            ("link", "https://silo.example", self.TOKEN))
        for bad in ("", "hello", "https://silo.example/rooms/join", "https://silo.example/rooms/join?token=",
                    "https://silo.example/rooms/join?token=a&token=b", "https://silo.example/rooms/join?token=a&x=1",
                    "https://user@silo.example/rooms/join?token=a", "ftp://silo.example/rooms/join?token=a",
                    "silo://invite?server=https://silo.example&token=a"):
            self.assertIsNone(svc_party.parse_invite(bad), bad)

    def test_a_link_must_name_the_server_this_profile_is_signed_in_to(self):
        self.assertTrue(svc_party.same_server("https://Silo.Example:443/", "https://silo.example"))
        self.assertTrue(svc_party.same_server("http://silo.example/media", "http://silo.example:80/media/"))
        self.assertFalse(svc_party.same_server("https://silo.example", "http://silo.example"))
        self.assertFalse(svc_party.same_server("https://silo.example/a", "https://silo.example/b"))
        self.assertFalse(svc_party.same_server("https://silo.example:8443", "https://silo.example"))
        self.assertFalse(svc_party.same_server("https://silo.example", ""))


if __name__ == "__main__":
    unittest.main()
