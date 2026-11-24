"""Watch Party screens with Kodi stubbed: join by code, host, the room loop, lobby and menu."""

import importlib
import sys
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
        "xbmcgui": types.SimpleNamespace(WindowXML=FakeWindow, Dialog=MagicMock, ListItem=FakeListItem,
                                         INPUT_ALPHANUM=0)}
with patch.dict(sys.modules, KODI):
    sys.modules.pop("resources.lib.silo.ui_party", None)
    ui_party = importlib.import_module("resources.lib.silo.ui_party")
ApiError = ui_party.ApiError

ROOM = {"room_id": "r1", "phase": "lobby", "code": "ABCD2345", "self_role": "guest", "selection_revision": 1,
        "guest_control_policy": "host_only", "members": [
            {"display_name": "Host Example", "is_host": True, "lobby_ready": False},
            {"display_name": "Me", "is_self": True, "lobby_ready": True}]}


def application(support=None):
    app = MagicMock()
    app.guard.side_effect = lambda callback: callback()
    app.image.side_effect = lambda item, kind: "https://img.example/{}/{}".format(item.get("content_id"), kind)
    app.client.item.return_value = {"content_id": "movie:1", "type": "movie", "title": "Example Film",
                                    "year": 2001}
    supported = {"guest": True, "host": True, "lobby_ready": True}
    supported.update(support or {})
    patcher = patch.object(ui_party.svc_party, "support", return_value=supported)
    patcher.start()
    return app, patcher


class FakeSession:
    def __init__(self, room=None, ended=None):
        self.room = dict(ROOM, **(room or {}))
        self.ended, self.connected, self.sent = ended, True, []
        self.room_id, self.token = "r1", "eyJ.proof"
        self.accepted = []

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

    def leave(self):
        self.ended = self.ended or "left"


class JoinAndHostTests(unittest.TestCase):
    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def test_a_good_code_joins_and_opens_the_room(self):
        self.app.dialog.input.return_value = " abcd-2345 "
        with patch.object(ui_party.svc_party, "join", return_value=(ROOM, "eyJ.proof")) as join, \
                patch.object(ui_party, "run_party") as run:
            ui_party.open_party(self.app)
        join.assert_called_once_with(self.app.client, "ABCD2345")
        run.assert_called_once_with(self.app, ROOM, "eyJ.proof")

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
        with patch.object(ui_party.svc_party, "support", return_value={"guest": False, "host": False,
                                                                       "lobby_ready": False}):
            ui_party.open_party(self.app)
        self.app.dialog.input.assert_not_called()
        self.app.dialog.ok.assert_called_once_with(ui_party.HEADING, ui_party.UNSUPPORTED)

    def test_hosting_creates_a_room_and_puts_the_title_in_its_lobby(self):
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
        with patch.object(ui_party.svc_party, "support", return_value={"guest": True, "host": False,
                                                                       "lobby_ready": True}), \
                patch.object(ui_party.svc_party, "create") as create:
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
        self.app.dialog.ok.assert_not_called()

    def test_a_room_that_ends_says_why(self):
        session = FakeSession(ended="host_left")
        ui_party.run_party(self.app, ROOM, "eyJ.proof", session_factory=lambda *args, **kwargs: session)
        self.app.dialog.ok.assert_called_once_with(ui_party.HEADING, ui_party.ENDINGS["host_left"])

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


class LobbyTests(unittest.TestCase):
    def setUp(self):
        self.app, patcher = application()
        self.addCleanup(patcher.stop)

    def window(self, session, declined=()):
        window = ui_party.LobbyWindow("SiloParty.xml", ".")
        window.application, window.session, window.declined = self.app, session, set(declined)
        window.support = {"guest": True, "host": True, "lobby_ready": True}
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
        self.assertEqual(window.properties["silo.policy_label"], "")
        members = window.getControl(ui_party.MEMBERS).addItems.call_args.args[0]
        self.assertEqual([(member.label, member.label2) for member in members],
                         [("Host Example", "Host"), ("Me (you)", "Ready")])
        window.onClick(ui_party.PRIMARY)
        self.assertEqual(session.sent[-1], {"type": "lobby_ready", "ready": False})

    def test_the_host_starts_the_party_and_sets_who_may_play_and_pause(self):
        session = FakeSession({"self_role": "host", "selected_content_id": "movie:1"})
        window = self.window(session)
        window.update()
        self.assertEqual(window.properties["silo.primary_label"], "Start the Watch Party")
        self.assertEqual(window.properties["silo.policy_label"], "Guests watch only")
        self.assertEqual(window.properties["silo.leave_label"], "End the Watch Party")
        with patch.object(ui_party.svc_party, "start", return_value=(ROOM, "eyJ.s")) as start:
            window.onClick(ui_party.PRIMARY)
        start.assert_called_once_with(self.app.client, "r1")
        with patch.object(ui_party.svc_party, "set_policy", return_value=(ROOM, "eyJ.p")) as policy:
            window.onClick(ui_party.POLICY)
        policy.assert_called_once_with(self.app.client, "r1", "guest_play_pause")

    def test_the_lobby_closes_to_play_or_on_the_end_of_the_party(self):
        session = FakeSession({"phase": "playing", "selection_revision": 4})
        window = self.window(session)
        window.update()
        self.assertEqual((window.result, window.closed), ("play", True))
        declined = self.window(session, declined={4})  # The viewer stopped this one: offer it.
        declined.update()
        self.assertFalse(declined.closed)
        self.assertEqual(declined.properties["silo.primary_label"], "Watch now")
        session.ended = "host_left"
        declined.update()
        self.assertEqual(declined.result, "ended")

    def test_back_asks_before_leaving_and_the_host_ends_the_party(self):
        session = FakeSession({"self_role": "host"})
        window = self.window(session)
        self.app.dialog.yesno.return_value = False
        window.onAction(types.SimpleNamespace(getId=lambda: 10))
        self.assertFalse(window.closed)
        self.app.dialog.yesno.return_value = True
        with patch.object(ui_party.svc_party, "close") as close:
            window.onAction(types.SimpleNamespace(getId=lambda: 10))
        close.assert_called_once_with(self.app.client, "r1")
        self.assertEqual(window.result, "leave")


class LobbySkinTests(unittest.TestCase):
    def test_the_lobby_has_the_controls_python_uses_and_valid_navigation(self):
        import xml.etree.ElementTree as ET
        from pathlib import Path

        skin = Path(__file__).resolve().parents[1] / "resources/skins/Default/1080i/SiloParty.xml"
        tree = ET.parse(skin)
        ids = {int(node.attrib["id"]) for node in tree.iter("control") if "id" in node.attrib}
        for control_id in (ui_party.PRIMARY, ui_party.LEAVE, ui_party.POLICY, ui_party.MEMBERS):
            self.assertIn(control_id, ids)
        for direction in ("onleft", "onright", "onup", "ondown"):
            for node in tree.iter(direction):
                self.assertIn(int(node.text), ids)


if __name__ == "__main__":
    unittest.main()
