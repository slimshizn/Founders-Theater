"""Watch Party screens with Kodi stubbed: the hub, joining, hosting, the room loop, stepping
away and returning, Rejoin, the title page's entries, the lobby and its vote, and invitations."""

import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


class FakeWindow:
    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        instance.properties, instance.controls, instance.closed = {}, {}, False
        return instance

    def getControl(self, control_id):
        if control_id not in self.controls:
            self.controls[control_id] = MagicMock()
        return self.controls[control_id]

    def setProperty(self, key, value):
        self.properties[key] = value

    def setFocusId(self, control_id):
        self.focus = control_id

    def close(self):
        self.closed = True


class FakeListItem:
    def __init__(self, label="", label2=""):
        self.label, self.label2 = label, label2


KODI = {"xbmc": types.SimpleNamespace(log=MagicMock(), LOGINFO=1, LOGWARNING=2),
        "xbmcgui": types.SimpleNamespace(WindowXML=FakeWindow, WindowXMLDialog=FakeWindow, Dialog=MagicMock,
                                         ListItem=FakeListItem, INPUT_ALPHANUM=0)}
with patch.dict(sys.modules, KODI):
    sys.modules.pop("resources.lib.silo.ui_party", None)
    ui_party = importlib.import_module("resources.lib.silo.ui_party")
ApiError = ui_party.ApiError
BACK = types.SimpleNamespace(getId=lambda: 10)

ROOM = {"room_id": "r1", "phase": "lobby", "code": "ABCD2345", "self_role": "guest", "selection_revision": 1,
        "selection_mode": "host_pick", "guest_control_policy": "host_only", "members": [
            {"display_name": "Host Example", "is_host": True, "lobby_ready": False},
            {"display_name": "Me", "is_self": True, "lobby_ready": True}]}
FULL = {"guest": True, "host": True, "lobby_ready": True, "vote": True, "mode_switch": True, "picker": True,
        "fallback": True}


class MemoryStore(dict):
    def set(self, key, value):
        self[key] = value

    def delete(self, key):
        self.pop(key, None)


def application(support=None):
    app = MagicMock()
    app.party_session = None
    app.profile = {"id": "p1"}
    app.client.server_url = "https://silo.example"
    app.store = MemoryStore()
    app.guard.side_effect = lambda callback: callback()
    app.image.side_effect = lambda item, kind: "https://img.example/{}/{}".format(item.get("content_id"), kind)
    app.client.item.return_value = {"content_id": "movie:1", "type": "movie", "title": "Example Film",
                                    "year": 2001}
    supported = {"guest": True, "host": False, "lobby_ready": True, "vote": False, "mode_switch": False,
                 "picker": False, "fallback": False}
    supported.update(support or {})
    patcher = patch.object(ui_party.svc_party, "support", return_value=supported)
    patcher.start()
    return app, patcher


class FakeSession:
    def __init__(self, room=None, ended=None, suggestions=None):
        self.room = dict(ROOM, **(room or {}))
        self.ended, self.connected, self.sent = ended, True, []
        self.room_id, self.token = "r1", "eyJ.proof"
        self.accepted, self.votes = [], []
        self.declined, self.title, self.away = set(), None, False
        self.support = dict(FULL)
        self.suggestions = list(suggestions or [])
        self.left = False

    def snapshot(self):
        return dict(self.room)

    def is_host(self):
        return self.room.get("self_role") == "host"

    def send(self, frame):
        self.sent.append(frame)
        return True

    def accept_http(self, room, token):
        self.accepted.append((room, token))

    def wait(self, timeout):
        pass

    def suggestion_list(self):
        return [dict(entry) for entry in self.suggestions]

    def note_vote(self, suggestion_id, on):
        self.votes.append((suggestion_id, on))

    def leave(self):
        self.left = True
        self.ended = self.ended or "left"


def suggestion(sid, title, votes=0, voted=False, profile="p2"):
    return {"id": sid, "title": title, "subtitle": "", "vote_count": votes, "voted_by_me": voted,
            "suggester_profile_id": profile, "content_type": "movie", "content_id": "movie:" + sid}


class HubTests(unittest.TestCase):
    def setUp(self):
        self.app, self.patcher = application()
        self.addCleanup(lambda: self.patcher.stop())

    def support(self, **values):
        self.patcher.stop()
        self.app, self.patcher = application(values)

    def test_a_guest_only_profile_goes_straight_to_the_code(self):
        self.app.dialog.input.return_value = " abcd-2345 "
        with patch.object(ui_party.svc_party, "join", return_value=(ROOM, "eyJ.proof")) as join, \
                patch.object(ui_party, "run_party") as run:
            ui_party.open_party(self.app)
        self.app.dialog.select.assert_not_called()
        self.assertEqual(self.app.dialog.input.call_args.args[0], ui_party.JOIN_FIELD)
        join.assert_called_once_with(self.app.client, "ABCD2345")
        run.assert_called_once_with(self.app, ROOM, "eyJ.proof")

    def test_an_invitation_link_joins_only_on_this_server(self):
        token = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        self.app.dialog.input.return_value = "https://silo.example/rooms/join?token=" + token
        with patch.object(ui_party.svc_party, "join", return_value=(ROOM, "eyJ.proof")) as join, \
                patch.object(ui_party, "run_party") as run:
            ui_party.open_party(self.app)
        join.assert_called_once_with(self.app.client, join_token=token)
        run.assert_called_once()
        self.app.dialog.input.return_value = "https://elsewhere.example/rooms/join?token=" + token
        with patch.object(ui_party.svc_party, "join") as join, patch.object(ui_party, "run_party") as run:
            ui_party.open_party(self.app)
        join.assert_not_called()  # The join token never goes to another server.
        self.app.dialog.ok.assert_called_with(ui_party.HEADING, ui_party.OTHER_SERVER)

    def test_a_bad_or_unknown_code_or_an_ended_party_says_so(self):
        for typed, error, message in (("ABC", None, ui_party.BAD_CODE),
                                      ("ABCD2345", ApiError(404, "Not found"), ui_party.NO_PARTY),
                                      ("ABCD2345", ApiError(409, "Ended", "conflict"), ui_party.ENDINGS["ended"])):
            with self.subTest(typed=typed, error=error):
                self.app.reset_mock()
                self.app.dialog.input.return_value = typed
                with patch.object(ui_party.svc_party, "join", side_effect=error) as join, \
                        patch.object(ui_party, "run_party") as run:
                    ui_party.open_party(self.app)
                run.assert_not_called()
                self.app.dialog.ok.assert_called_once_with(ui_party.HEADING, message)
                if typed == "ABC":
                    join.assert_not_called()

    def test_without_server_support_nothing_is_asked(self):
        self.support(guest=False)
        ui_party.open_party(self.app)
        self.app.dialog.input.assert_not_called()
        self.app.dialog.ok.assert_called_once_with(ui_party.HEADING, ui_party.UNSUPPORTED)

    def test_a_host_starts_an_empty_party_or_a_vote(self):
        self.support(host=True, vote=True)
        for chosen, mode in ((1, "host_pick"), (2, "vote")):
            with self.subTest(mode=mode):
                self.app.dialog.select.return_value = chosen
                with patch.object(ui_party.svc_party, "create", return_value=(ROOM, "eyJ.a")) as create, \
                        patch.object(ui_party, "run_party") as run:
                    ui_party.open_party(self.app)
                self.assertEqual(self.app.dialog.select.call_args.args[1],
                                 ["Join with a code or invite link", "Start a party",
                                  "Start a party and let everyone vote"])
                self.assertEqual(len(create.call_args.args[1]), 36)
                self.assertEqual(create.call_args.args[2], mode)
                run.assert_called_once_with(self.app, ROOM, "eyJ.a")

    def test_the_last_party_can_be_rejoined_and_a_gone_one_is_forgotten(self):
        self.app.store[ui_party.RECENT_KEY] = {"key": "https://silo.example|p1", "code": "ABCD2345",
                                               "title": "Example Film"}
        self.app.dialog.select.return_value = 1
        with patch.object(ui_party.svc_party, "join", return_value=(ROOM, "eyJ.r")) as join, \
                patch.object(ui_party, "run_party") as run:
            ui_party.open_party(self.app)
        self.assertEqual(self.app.dialog.select.call_args.args[1],
                         ["Join with a code or invite link", "Rejoin Example Film"])
        join.assert_called_once_with(self.app.client, "ABCD2345")
        run.assert_called_once_with(self.app, ROOM, "eyJ.r")
        with patch.object(ui_party.svc_party, "join", side_effect=ApiError(409, "Ended", "conflict")), \
                patch.object(ui_party, "run_party") as run:
            ui_party.open_party(self.app)
        run.assert_not_called()
        self.app.dialog.ok.assert_called_with(ui_party.HEADING, ui_party.REJOIN_FAILED)
        self.assertNotIn(ui_party.RECENT_KEY, self.app.store)

    def test_another_profiles_or_servers_last_party_is_not_offered(self):
        self.app.store[ui_party.RECENT_KEY] = {"key": "https://silo.example|p9", "code": "ABCD2345"}
        self.assertIsNone(ui_party.recent_party(self.app))

    def test_an_open_party_is_returned_to(self):
        session = FakeSession()
        self.app.party_session = session
        with patch.object(ui_party, "enter") as enter:
            ui_party.open_party(self.app)
        enter.assert_called_once_with(self.app, session)
        self.app.dialog.select.assert_not_called()

    def test_hosting_creates_a_room_and_puts_the_title_in_its_lobby(self):
        self.support(host=True)
        item = {"content_id": "episode:1-2", "type": "episode", "title": "Chapter 2"}
        with patch.object(ui_party.svc_party, "create", return_value=(dict(ROOM, self_role="host"), "eyJ.a")) as create, \
                patch.object(ui_party.svc_party, "stage", return_value=(dict(ROOM, generation=2), "eyJ.b")) as stage, \
                patch.object(ui_party, "run_party") as run:
            ui_party.host_party(self.app, item, file_id="file-b")
        room_id = create.call_args.args[1]
        self.assertEqual(len(room_id), 36)  # A UUID the client chooses.
        stage.assert_called_once_with(self.app.client, room_id, "episode:1-2", file_id="file-b")
        self.assertEqual(run.call_args.args[2], "eyJ.b")

    def test_hosting_needs_the_servers_host_support(self):
        with patch.object(ui_party.svc_party, "create") as create:
            ui_party.host_party(self.app, {"content_id": "movie:1", "type": "movie"})
        create.assert_not_called()
        self.app.dialog.ok.assert_called_once_with(ui_party.HEADING, ui_party.NO_HOSTING)


class RoomLoopTests(unittest.TestCase):
    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def test_playing_rooms_play_and_the_lobby_comes_back_until_leaving(self):
        session = FakeSession({"phase": "playing"})
        steps = []

        def play_room(app, room_session, declined):
            steps.append("play")
            room_session.room["phase"] = "lobby"
            return "lobby"

        def lobby(app, room_session, declined):
            steps.append("lobby")
            return "leave"

        with patch.object(ui_party, "play_room", side_effect=play_room), patch.object(ui_party, "lobby",
                                                                                   side_effect=lobby):
            ui_party.run_party(self.app, ROOM, "eyJ.proof", session_factory=lambda *args, **kwargs: session)
        self.assertEqual(steps, ["play", "lobby"])
        self.assertEqual(session.ended, "left")
        self.assertIsNone(self.app.party_session)
        self.app.dialog.ok.assert_not_called()
        self.assertEqual(self.app.store[ui_party.RECENT_KEY]["code"], "ABCD2345")  # Rejoin offers it.

    def test_a_room_that_ends_says_why_and_is_not_offered_again(self):
        self.app.store[ui_party.RECENT_KEY] = {"key": "https://silo.example|p1", "code": "ABCD2345"}
        session = FakeSession(ended="host_left")
        ui_party.run_party(self.app, ROOM, "eyJ.proof", session_factory=lambda *args, **kwargs: session)
        self.app.dialog.ok.assert_called_once_with(ui_party.HEADING, ui_party.ENDINGS["host_left"])
        self.assertNotIn(ui_party.RECENT_KEY, self.app.store)

    def test_stepping_away_keeps_the_party_open(self):
        session = FakeSession()
        with patch.object(ui_party, "lobby", return_value="away"), \
                patch.object(ui_party, "keep_open") as keep_open:
            ui_party.enter(self.app, session)
        self.assertIs(self.app.party_session, session)
        self.assertFalse(session.left)
        keep_open.assert_called_once_with(self.app, session)

    def test_away_the_party_says_when_it_starts_and_when_it_ends(self):
        session = FakeSession()
        self.app.party_session = session
        ui_party.keep_open(self.app, session, watch=False)
        self.assertTrue(session.away)
        self.app.dialog.notification.assert_called_with(ui_party.HEADING, ui_party.STEPPED_AWAY, time=4000,
                                                        sound=False)
        ticks = []

        def wait(timeout):
            ticks.append(timeout)
            if len(ticks) == 1:
                session.room.update(phase="playing", selection_revision=2)
            elif len(ticks) == 3:
                session.ended = "host_left"

        session.wait = wait
        ui_party.watch_away(self.app, session)
        notices = [call.args[1] for call in self.app.dialog.notification.call_args_list]
        self.assertEqual(notices.count(ui_party.STARTING_AWAY), 1)
        self.assertEqual(notices[-1], ui_party.ENDINGS["host_left"])
        self.assertIsNone(self.app.party_session)
        self.assertTrue(session.left)

    def test_a_watcher_from_an_earlier_time_away_stops(self):
        session = FakeSession()
        self.app.party_session = session
        ui_party.keep_open(self.app, session, watch=False)
        earlier = session.away
        ui_party.keep_open(self.app, session, watch=False)  # Returned, then stepped away again.
        session.room.update(phase="playing", selection_revision=2)
        self.app.dialog.notification.reset_mock()
        ui_party.watch_away(self.app, session, earlier)
        self.app.dialog.notification.assert_not_called()

    def test_replaced_or_lost_offers_rejoin(self):
        for ending in ("replaced", "lost"):
            with self.subTest(ending=ending):
                self.app.reset_mock()
                session = FakeSession(ended=ending)
                self.app.dialog.yesno.return_value = True
                with patch.object(ui_party, "rejoin") as rejoin:
                    ui_party.run_party(self.app, ROOM, "eyJ.proof", session_factory=lambda *args, **kwargs: session)
                self.assertEqual(self.app.dialog.yesno.call_args.args, (ui_party.HEADING, ui_party.ENDINGS[ending]))
                self.assertEqual(self.app.dialog.yesno.call_args.kwargs["yeslabel"], "Rejoin")
                rejoin.assert_called_once_with(self.app, "ABCD2345")

    def test_switching_profile_leaves_an_open_party(self):
        session = FakeSession()
        self.app.party_session = session
        ui_party.leave_open_party(self.app)
        self.assertTrue(session.left)
        self.assertIsNone(self.app.party_session)

    def test_a_member_plays_the_rooms_file_from_the_rooms_position(self):
        session = FakeSession({"phase": "playing", "selected_content_id": "movie:1", "selected_file_id": 42,
                               "anchor_position_seconds": 61.5})
        ui_party.play_room(self.app, session, set())
        item = self.app.client.item.return_value
        kwargs = self.app.play.call_args.kwargs
        self.assertIs(self.app.play.call_args.args[0], item)
        self.assertEqual((kwargs["file_id"], kwargs["start_at"], kwargs["resume"], kwargs["auto_next"]),
                         ("42", 61.5, False, False))
        self.assertIsInstance(kwargs["party"], ui_party.RoomPlayback)
        self.assertTrue(callable(kwargs["party_menu"]))
        self.assertEqual(session.title, "Example Film")


class PartyMenuTests(unittest.TestCase):
    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def test_a_guest_can_go_back_to_the_video_or_leave(self):
        session = FakeSession({"phase": "playing"})
        self.app.dialog.select.return_value = 1
        self.assertEqual(ui_party.party_menu(self.app, session), "leave")
        self.assertEqual(self.app.dialog.select.call_args.args[1], ["Back to the video", "Leave the Watch Party"])

    def test_the_host_can_return_everyone_to_the_lobby_or_end_the_party(self):
        session = FakeSession({"phase": "playing", "self_role": "host"})
        self.app.dialog.select.return_value = 1
        with patch.object(ui_party.svc_party, "stop", return_value=(ROOM, "eyJ.c")) as stop:
            self.assertEqual(ui_party.party_menu(self.app, session), "watch")
        stop.assert_called_once_with(self.app.client, "r1")
        self.assertEqual(session.accepted, [(ROOM, "eyJ.c")])
        self.app.dialog.select.return_value = 2
        self.app.dialog.yesno.return_value = True
        with patch.object(ui_party.svc_party, "close") as close:
            ui_party.party_menu(self.app, session)
        close.assert_called_once_with(self.app.client, "r1")


class TitleEntryTests(unittest.TestCase):
    MOVIE = {"content_id": "movie:9", "type": "movie", "title": "Other Film", "year": 1999}

    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def entries(self, session=None, support=None, item=None):
        self.app.party_session = session
        return ui_party.title_entries(self.app, item or self.MOVIE, file_id="f9", support=support)

    def test_without_a_party_a_host_can_start_one(self):
        entries = self.entries(support={"host": True})
        self.assertEqual([label for label, _action in entries], ["Watch Party"])
        entries[0][1]()
        self.app.open_screen.assert_called_once_with("host_party", item=self.MOVIE, file_id="f9")
        self.assertEqual(self.entries(support={"host": False}), [])
        self.assertEqual(self.entries(support={"host": True}, item={"content_id": "series:1", "type": "series"}), [])

    def test_the_host_of_a_lobby_adds_the_title(self):
        session = FakeSession({"self_role": "host"})
        entries = self.entries(session)
        self.assertEqual([label for label, _action in entries], ["Add to party"])
        with patch.object(ui_party.svc_party, "stage", return_value=(ROOM, "eyJ.s")) as stage:
            entries[0][1]()
        stage.assert_called_once_with(self.app.client, "r1", "movie:9", file_id="f9")
        self.assertEqual(session.accepted, [(ROOM, "eyJ.s")])

    def test_the_host_of_a_playing_party_plays_the_title_for_everyone(self):
        session = FakeSession({"self_role": "host", "phase": "playing"})
        entries = self.entries(session)
        self.assertEqual([label for label, _action in entries], ["Play for everyone"])
        self.app.dialog.yesno.return_value = True
        with patch.object(ui_party.svc_party, "select", return_value=(ROOM, "eyJ.p")) as select:
            entries[0][1]()
        select.assert_called_once_with(self.app.client, "r1", "movie:9", file_id="f9")

    def test_anyone_else_suggests_and_returns_to_a_party_they_stepped_away_from(self):
        session = FakeSession({"selection_mode": "vote", "self_role": "host"})
        session.away = True
        entries = self.entries(session)
        self.assertEqual([label for label, _action in entries], ["Suggest to Party"])
        with patch.object(ui_party.svc_party, "suggest", return_value="s1") as suggest, \
                patch.object(ui_party, "enter") as enter:
            entries[0][1]()
        suggest.assert_called_once_with(self.app.client, "r1", "eyJ.proof", "movie:9", "movie", "Other Film",
                                        subtitle="1999")
        enter.assert_called_once_with(self.app, session)
        guest = self.entries(FakeSession())
        self.assertEqual([label for label, _action in guest], ["Suggest to Party"])


class LobbyTests(unittest.TestCase):
    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def window(self, session, declined=(), support=None):
        window = ui_party.LobbyWindow("SiloParty.xml", ".")
        window.application, window.session, window.declined = self.app, session, set(declined)
        window.support = dict(FULL, **(support or {}))
        return window

    def test_a_guest_sees_the_title_the_code_who_is_here_and_can_say_ready(self):
        session = FakeSession({"selected_content_id": "movie:1"})
        window = self.window(session)
        window.update()
        self.assertEqual(window.properties["silo.title"], "Example Film")
        self.assertEqual(window.properties["silo.code"], "ABCD 2345")
        self.assertEqual(window.properties["silo.status"], "Waiting for the host to start")
        self.assertEqual(window.properties["silo.primary_label"], "Not ready")  # Already ready: the toggle undoes it.
        self.assertEqual(window.properties["silo.leave_label"], "Leave the Watch Party")
        for key in ("silo.policy_label", "silo.suggest_label", "silo.mode_label", "silo.invite_label",
                    "silo.voting"):
            self.assertEqual(window.properties[key], "", key)
        members = window.getControl(ui_party.MEMBERS).addItems.call_args.args[0]
        self.assertEqual([(member.label, member.label2) for member in members],
                         [("Host Example", "Host"), ("Me (you)", "Ready")])
        window.onClick(ui_party.PRIMARY)
        self.assertEqual(session.sent[-1], {"type": "lobby_ready", "ready": False})

    def test_a_guest_waits_for_the_hosts_pick(self):
        window = self.window(FakeSession())
        window.update()
        self.assertEqual(window.properties["silo.title"], "Waiting for Host Example")

    def test_the_host_starts_the_party_and_sets_who_may_play_and_pause(self):
        session = FakeSession({"self_role": "host", "selected_content_id": "movie:1"})
        window = self.window(session)
        window.update()
        self.assertEqual(window.properties["silo.primary_label"], "Start the Watch Party")
        self.assertEqual(window.properties["silo.suggest_label"], "Change the title")
        self.assertEqual(window.properties["silo.mode_label"], "Let everyone vote")
        self.assertEqual(window.properties["silo.invite_label"], "Invite friends")
        self.assertEqual(window.properties["silo.policy_label"], "Guests watch only")
        self.assertEqual(window.properties["silo.leave_label"], "End the Watch Party")
        with patch.object(ui_party.svc_party, "start", return_value=(ROOM, "eyJ.s")) as start:
            window.onClick(ui_party.PRIMARY)
        start.assert_called_once_with(self.app.client, "r1")
        with patch.object(ui_party.svc_party, "set_policy", return_value=(ROOM, "eyJ.p")) as policy:
            window.onClick(ui_party.POLICY)
        policy.assert_called_once_with(self.app.client, "r1", "guest_play_pause")
        with patch.object(ui_party.svc_party, "set_mode", return_value=(ROOM, "eyJ.m")) as mode:
            window.onClick(ui_party.MODE)
        mode.assert_called_once_with(self.app.client, "r1", "vote")

    def test_the_host_chooses_the_title_from_the_picker(self):
        session = FakeSession({"self_role": "host"})
        window = self.window(session)
        window.update()
        self.assertEqual(window.properties["silo.title"], "Pick something to watch")
        self.assertEqual(window.properties["silo.suggest_label"], "Choose a title")
        choice = {"content_id": "movie:7", "content_type": "movie", "title": "Picked", "subtitle": ""}
        with patch.object(ui_party, "pick_title", return_value=choice) as pick, \
                patch.object(ui_party.svc_party, "stage", return_value=(ROOM, "eyJ.t")) as stage:
            window.onClick(ui_party.SUGGEST)
        pick.assert_called_once_with(self.app, session, "Choose a title")
        stage.assert_called_once_with(self.app.client, "r1", "movie:7", file_id=None)
        self.assertFalse(window.nested)

    def test_a_vote_lists_suggestions_by_votes_and_the_host_starts_the_leader(self):
        session = FakeSession({"self_role": "host", "selection_mode": "vote"},
                              suggestions=[suggestion("s2", "Leader", votes=2, voted=True),
                                           suggestion("s1", "Runner up", votes=1)])
        window = self.window(session)
        window.update()
        self.assertEqual(window.properties["silo.voting"], "true")
        self.assertEqual(window.properties["silo.title"], "What are we watching?")
        self.assertEqual(window.properties["silo.primary_label"], "Start Leader")
        self.assertEqual(window.properties["silo.suggest_label"], "Suggest a title")
        self.assertEqual(window.properties["silo.mode_label"], "Pick the title yourself")
        self.assertEqual(window.properties["silo.status"], "Leader leads with 2 votes.")
        self.assertEqual(window.properties["silo.suggestions_heading"], "Suggestions (2)")
        self.assertEqual(window.properties["silo.member_line"],
                         "In the party: Host Example (host), Me (you, ready)")
        rows = window.getControl(ui_party.SUGGESTIONS).addItems.call_args.args[0]
        self.assertEqual([(row.label, row.label2) for row in rows],
                         [("Leader", "2 votes · your vote"), ("Runner up", "1 vote")])
        with patch.object(ui_party.svc_party, "promote", return_value=(ROOM, "eyJ.w")) as promote:
            window.onClick(ui_party.PRIMARY)
        promote.assert_called_once_with(self.app.client, "r1", "eyJ.proof", "s2")

    def test_a_suggestion_takes_a_vote_and_its_suggester_or_the_host_removes_it(self):
        session = FakeSession({"selection_mode": "vote"},
                              suggestions=[suggestion("s1", "Mine", profile="p1"), suggestion("s2", "Theirs")])
        window = self.window(session)
        window.update()
        window.getControl(ui_party.SUGGESTIONS).getSelectedPosition.return_value = 0
        self.app.dialog.select.return_value = 0
        with patch.object(ui_party.svc_party, "vote") as vote:
            window.onClick(ui_party.SUGGESTIONS)
        vote.assert_called_once_with(self.app.client, "r1", "eyJ.proof", "s1", on=True)
        self.assertEqual(session.votes, [("s1", True)])
        self.assertEqual(self.app.dialog.select.call_args.args[1], ["Vote for this", "Remove suggestion"])
        self.app.dialog.select.return_value = 1
        with patch.object(ui_party.svc_party, "unsuggest") as unsuggest:
            window.onClick(ui_party.SUGGESTIONS)
        unsuggest.assert_called_once_with(self.app.client, "r1", "eyJ.proof", "s1")
        window.getControl(ui_party.SUGGESTIONS).getSelectedPosition.return_value = 1
        window.onClick(ui_party.SUGGESTIONS)
        self.assertEqual(self.app.dialog.select.call_args.args[1], ["Vote for this"])  # Not theirs to remove.

    def test_a_guest_suggests_a_title_in_a_vote(self):
        session = FakeSession({"selection_mode": "vote"})
        window = self.window(session)
        window.update()
        self.assertEqual(window.properties["silo.status"], "Suggest a title to get the vote going.")
        choice = {"content_id": "episode:3", "content_type": "episode", "title": "Pilot",
                  "subtitle": "Example Show · S1E1"}
        with patch.object(ui_party, "pick_title", return_value=choice), \
                patch.object(ui_party.svc_party, "suggest", return_value="s9") as suggest:
            window.onClick(ui_party.SUGGEST)
        suggest.assert_called_once_with(self.app.client, "r1", "eyJ.proof", "episode:3", "episode", "Pilot",
                                        subtitle="Example Show · S1E1")

    def test_the_lobby_closes_to_play_or_on_the_end_of_the_party_but_not_under_a_picker(self):
        session = FakeSession({"phase": "playing", "selection_revision": 4})
        window = self.window(session)
        window.nested = True
        window.update()
        self.assertFalse(window.closed)  # The picker or title page closes first.
        window.nested = False
        window.update()
        self.assertEqual((window.result, window.closed), ("play", True))
        declined = self.window(session, declined={4})  # The viewer stopped this one: offer it.
        declined.update()
        self.assertFalse(declined.closed)
        self.assertEqual(declined.properties["silo.primary_label"], "Watch now")
        session.ended = "host_left"
        declined.update()
        self.assertEqual(declined.result, "ended")

    def test_back_steps_away_and_leave_asks_first(self):
        session = FakeSession({"self_role": "host"})
        window = self.window(session)
        window.onAction(BACK)
        self.assertEqual((window.result, window.closed), ("away", True))
        window = self.window(session)
        self.app.dialog.yesno.return_value = False
        window.onClick(ui_party.LEAVE)
        self.assertFalse(window.closed)
        self.app.dialog.yesno.return_value = True
        with patch.object(ui_party.svc_party, "close") as close:
            window.onClick(ui_party.LEAVE)
        close.assert_called_once_with(self.app.client, "r1")
        self.assertEqual(window.result, "leave")

    def test_invite_friends_shows_the_code_and_a_qr_code_of_the_link(self):
        session = FakeSession({"self_role": "host", "invite_path": "/rooms/join?token=ABCDEFGHJKLMNPQRSTUVWXYZ"})
        window = self.window(session)
        with patch.object(ui_party, "show_invite") as show:
            window.onClick(ui_party.INVITE)
        show.assert_called_once_with(self.app, session)
        self.assertFalse(window.nested)


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def test_shared_titles_become_party_choices_and_a_series_opens_its_page(self):
        shared = {"continue_together": [
            {"item": {"content_id": "series:1", "type": "series", "title": "Example Show"}, "members": [],
             "next_up": {"content_id": "episode:12", "season_number": 1, "episode_number": 2, "title": "Second",
                         "member_count": 2}}],
            "watchlist_union": [
                {"item": {"content_id": "movie:5", "type": "movie", "title": "Watchlisted", "year": 2010},
                 "members": []},
                {"item": {"content_id": "series:2", "type": "series", "title": "Unstarted Show"}, "members": []}]}
        session = FakeSession()
        with patch.object(ui_party.svc_party, "picker", return_value=shared):
            self.app.dialog.select.return_value = 1
            choice = ui_party.pick_title(self.app, session, "Suggest a title")
            entries = self.app.dialog.select.call_args.args[1]
            self.assertEqual([(entry.label, entry.label2) for entry in entries],
                             [("Search for a title…", ""), ("Example Show: Second", "Continue Together"),
                              ("Watchlisted (2010)", "On a watchlist"), ("Unstarted Show", "On a watchlist")])
            self.assertEqual(choice, {"content_id": "episode:12", "content_type": "episode", "title": "Second",
                                      "subtitle": "Example Show · S1E2"})
            self.app.dialog.select.return_value = 3
            self.assertIsNone(ui_party.pick_title(self.app, session, "Suggest a title"))
        self.app.open_item.assert_called_once_with(shared["watchlist_union"][1]["item"])

    def test_searching_finds_a_title(self):
        self.app.client.search.return_value = {"items": [
            {"content_id": "movie:8", "type": "movie", "title": "Found", "year": 2020},
            {"content_id": "person:1", "type": "person", "name": "Someone"}]}
        self.app.dialog.input.return_value = "found"
        self.app.dialog.select.side_effect = [0, 0]
        choice = ui_party.pick_title(self.app, FakeSession(), "Choose a title")
        self.app.client.search.assert_called_once_with("found", limit=40)
        self.assertEqual([(entry.label, entry.label2) for entry in self.app.dialog.select.call_args.args[1]],
                         [("Found (2020)", "")])
        self.assertEqual(choice, {"content_id": "movie:8", "content_type": "movie", "title": "Found",
                                  "subtitle": "2020"})


ANA = {"user_id": "7", "profile_id": "p7", "display_name": "Ana"}
BEN = {"user_id": "7", "profile_id": "p8", "display_name": "Ben"}
KIM = {"user_id": "9", "profile_id": "p9", "display_name": " "}


def watch(member, state, position=None):
    entry = {"user_id": member["user_id"], "profile_id": member["profile_id"], "state": state,
             "on_watchlist": False}
    if position is not None:
        entry["position_seconds"] = position
    return entry


class MemberStateTests(unittest.TestCase):
    """Who in the party has seen a title, from the room's member-state read."""

    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def window(self, session, support):
        window = ui_party.LobbyWindow("SiloParty.xml", ".")
        window.application, window.session, window.declined = self.app, session, set()
        window.support = dict(FULL, **support)
        return window

    def test_names_are_listed_as_android_words_them(self):
        self.assertEqual(ui_party.name_list([]), "")
        self.assertEqual(ui_party.name_list(["Ana"]), "Ana")
        self.assertEqual(ui_party.name_list(["Ana", "Ben"]), "Ana and Ben")
        self.assertEqual(ui_party.name_list(["Ana", "Ben", "Kim"]), "Ana, Ben, and Kim")
        self.assertEqual(ui_party.name_list(["Ana", "Ben", "Kim", "Lee"]), "Ana, Ben, and 2 others")
        self.assertEqual(ui_party.name_list(["Ana", "Ben", "Kim", "Lee", "Max"]), "Ana, Ben, and 3 others")

    def test_a_title_says_who_has_seen_it_and_who_is_part_way(self):
        state = {"members": [ANA, BEN, KIM], "items": [
            {"content_id": "movie:1", "members": [watch(ANA, "watched"), watch(BEN, "in_progress", 1200.0),
                                                  watch(KIM, "watched")]},
            {"content_id": "series:2", "members": [watch(BEN, "in_progress")]},  # A series has no position.
            {"content_id": "movie:3", "members": [watch(ANA, "unseen"), watch(BEN, "unseen", 0)]},
            {"content_id": "movie:4", "members": [watch({"user_id": "1", "profile_id": "gone"}, "watched")]}]}
        # Unseen titles say nothing, a member the answer doesn't name is left out, and a blank
        # name reads Someone.
        self.assertEqual(ui_party.seen_lines(state), {"movie:1": "Seen by Ana and Someone · Part way: Ben",
                                                      "series:2": "Part way: Ben"})
        self.assertEqual(ui_party.seen_lines({}), {})

    def test_the_lobby_adds_who_has_seen_the_staged_title_and_asks_again_when_someone_joins(self):
        session = FakeSession({"selected_content_id": "movie:1", "members": [dict(ANA, is_host=True),
                                                                             dict(BEN, is_self=True)]})
        window = self.window(session, {"member_state": True})
        state = {"members": [ANA, BEN], "items": [{"content_id": "movie:1", "members": [watch(ANA, "watched")]}]}
        with patch.object(ui_party.svc_party, "member_state", return_value=state) as read:
            window.update()
            window.update()
            meta = ui_party.LobbyWindow.meta(self.app.client.item.return_value)
            self.assertEqual(window.properties["silo.meta"], meta + " · Seen by Ana")
            read.assert_called_once_with(self.app.client, "r1", "eyJ.proof", ["movie:1"])
            session.room["members"] = session.room["members"] + [KIM]
            window.update()
            self.assertEqual(read.call_count, 2)

    def test_the_lobby_line_needs_the_servers_member_state(self):
        session = FakeSession({"selected_content_id": "movie:1"})
        window = self.window(session, {"member_state": False})
        with patch.object(ui_party.svc_party, "member_state") as read:
            window.update()
        read.assert_not_called()
        self.assertEqual(window.properties["silo.meta"], ui_party.LobbyWindow.meta(self.app.client.item.return_value))
        failing = self.window(session, {"member_state": True})
        with patch.object(ui_party.svc_party, "member_state", side_effect=ApiError(503, "Unavailable")):
            failing.update()
        self.assertEqual(failing.properties["silo.meta"], window.properties["silo.meta"])

    def test_the_picker_and_search_rows_say_who_has_seen_each_title_in_one_read(self):
        shared = {"continue_together": [
            {"item": {"content_id": "series:1", "type": "series", "title": "Example Show"}, "members": [],
             "next_up": {"content_id": "episode:12", "season_number": 1, "episode_number": 2, "title": "Second"}}],
            "watchlist_union": [{"item": {"content_id": "movie:5", "type": "movie", "title": "Watchlisted"},
                                 "members": []}]}
        state = {"members": [ANA, BEN], "items": [
            {"content_id": "episode:12", "members": [watch(BEN, "in_progress", 60.0)]},
            {"content_id": "movie:5", "members": [watch(ANA, "watched"), watch(BEN, "watched")]}]}
        session = FakeSession()
        session.support = dict(FULL, member_state=True)
        self.app.dialog.select.return_value = -1
        with patch.object(ui_party.svc_party, "picker", return_value=shared), \
                patch.object(ui_party.svc_party, "member_state", return_value=state) as read:
            ui_party.pick_title(self.app, session, "Suggest a title")
        read.assert_called_once_with(self.app.client, "r1", "eyJ.proof", ["episode:12", "movie:5"])
        self.assertEqual([entry.label2 for entry in self.app.dialog.select.call_args.args[1]],
                         ["", "Continue Together · Part way: Ben", "On a watchlist · Seen by Ana and Ben"])
        self.app.client.search.return_value = {"items": [
            {"content_id": "movie:5", "type": "movie", "title": "Watchlisted"},
            {"content_id": "movie:6", "type": "movie", "title": "Fresh"}]}
        self.app.dialog.input.return_value = "watch"
        self.app.dialog.select.side_effect = [0, -1]
        with patch.object(ui_party.svc_party, "picker", return_value={}), \
                patch.object(ui_party.svc_party, "member_state", return_value=state) as read:
            ui_party.pick_title(self.app, session, "Suggest a title")
        read.assert_called_once_with(self.app.client, "r1", "eyJ.proof", ["movie:5", "movie:6"])
        self.assertEqual([(entry.label, entry.label2) for entry in self.app.dialog.select.call_args.args[1]],
                         [("Watchlisted", "Seen by Ana and Ben"), ("Fresh", "")])
        self.assertTrue(self.app.dialog.select.call_args.kwargs.get("useDetails"))

    def test_the_picker_keeps_its_rows_when_member_state_fails(self):
        shared = {"watchlist_union": [{"item": {"content_id": "movie:5", "type": "movie", "title": "Watchlisted"},
                                       "members": []}]}
        session = FakeSession()
        session.support = dict(FULL, member_state=True)
        self.app.dialog.select.return_value = -1
        with patch.object(ui_party.svc_party, "picker", return_value=shared), \
                patch.object(ui_party.svc_party, "member_state", side_effect=OSError("timed out")):
            ui_party.pick_title(self.app, session, "Suggest a title")
        self.assertEqual([entry.label2 for entry in self.app.dialog.select.call_args.args[1]],
                         ["", "On a watchlist"])


class InvitationTests(unittest.TestCase):
    def test_the_qr_code_is_a_png_named_for_its_link(self):
        app, patcher = application()
        self.addCleanup(patcher.stop)
        folder = tempfile.mkdtemp()
        app._profile_dir.return_value = folder
        stale = os.path.join(folder, "party-invite-0000000000000000.png")
        with open(stale, "wb") as handle:
            handle.write(b"old")
        path = ui_party.invite_image(app, "https://silo.example/rooms/join?token=ABCDEFGHJKLMNPQRSTUVWXYZ")
        self.assertTrue(path.startswith(os.path.join(folder, "party-invite-")))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertFalse(os.path.exists(stale))  # Only the current invitation is kept.
        self.assertEqual(ui_party.invite_image(app, "https://silo.example/rooms/join?token=ABCDEFGHJKLMNPQRSTUVWXYZ"),
                         path)


class LobbySkinTests(unittest.TestCase):
    def tree(self, name):
        import xml.etree.ElementTree as ET
        from pathlib import Path

        return ET.parse(Path(__file__).resolve().parents[1] / "resources/skins/Default/1080i" / name)

    def test_the_lobby_has_the_controls_python_uses_and_valid_navigation(self):
        tree = self.tree("SiloParty.xml")
        ids = {int(node.attrib["id"]) for node in tree.iter("control") if "id" in node.attrib}
        for control_id in (ui_party.PRIMARY, ui_party.LEAVE, ui_party.POLICY, ui_party.SUGGEST, ui_party.MODE,
                           ui_party.INVITE, ui_party.MEMBERS, ui_party.SUGGESTIONS):
            self.assertIn(control_id, ids)
        for direction in ("onleft", "onright", "onup", "ondown"):
            for node in tree.iter(direction):
                self.assertIn(int(node.text), ids)
        column = next(node for node in tree.iter("control") if node.get("type") == "grouplist")
        self.assertEqual([int(node.get("id")) for node in column.findall("control")],
                         [ui_party.PRIMARY, ui_party.SUGGEST, ui_party.MODE, ui_party.INVITE, ui_party.POLICY,
                          ui_party.LEAVE])

    def test_the_invitation_has_done_the_code_and_the_qr_code(self):
        tree = self.tree("SiloPartyInvite.xml")
        ids = {int(node.attrib["id"]) for node in tree.iter("control") if "id" in node.attrib}
        self.assertIn(ui_party.INVITE_DONE, ids)
        text = "".join(node.text or "" for node in tree.iter("texture")) + "".join(
            node.text or "" for node in tree.iter("label"))
        self.assertIn("Window.Property(silo.qr)", text)
        self.assertIn("Window.Property(silo.code)", text)


if __name__ == "__main__":
    unittest.main()
