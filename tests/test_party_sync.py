"""One member's playback kept on the room's clock (Android RoomPlaybackBinding)."""

import unittest

from resources.lib.silo import party_sync

SERVER_AHEAD = 1000.0  # Server epoch seconds minus the monotonic clock, for readable numbers.


class Time:
    def __init__(self):
        self.mono = 50.0

    def server(self):
        return self.mono + SERVER_AHEAD


class FakeClock:
    def __init__(self, time):
        self.time, self.known = time, True

    def now(self):
        return self.time.server() if self.known else None


class FakeSession:
    def __init__(self, time, **room):
        self.ended = None
        self.room = {"phase": "playing", "playback_state": "waiting", "selection_revision": 2,
                     "self_role": "guest", "self_can_control_transport": False, "attached_session_id": "",
                     "members": [{"is_self": True, "display_name": "Me"}]}
        self.room.update(room)
        self.commands, self.sent = [], []
        self.clock = FakeClock(time)

    def snapshot(self):
        return dict(self.room)

    def take_command(self):
        command = self.commands[-1] if self.commands else None
        self.commands = []
        return command

    def send(self, frame):
        self.sent.append(frame)
        return True

    def kinds(self):
        return [frame["type"] for frame in self.sent]


def command(time, action, position, after=0.5, state="playing", command_id="c1", revision=2, session_id=None):
    return {"command_id": command_id, "action": action, "position_seconds": position,
            "execute_at": party_sync.room_clock.format_instant(time.server() + after, digits=9),
            "playback_state": state, "selection_revision": revision, "session_id": session_id}


class RoomPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.time = Time()
        self.session = FakeSession(self.time)
        self.binding = party_sync.RoomPlayback(self.session, mono=lambda: self.time.mono)
        self.binding.attach("s1")
        self.session.room["attached_session_id"] = "s1"

    def tick(self, position, paused, stalled=False, seconds=0.25):
        self.time.mono += seconds
        return self.binding.tick(position, paused, stalled)

    def test_attaching_sends_the_session_and_repeats_until_the_room_echoes_it(self):
        self.assertEqual(self.session.sent[0], {"type": "attach_session", "session_id": "s1"})
        self.session.room["attached_session_id"] = ""
        self.tick(0.0, True, seconds=1.0)
        self.assertEqual(self.session.kinds().count("attach_session"), 1)
        self.tick(0.0, True, seconds=1.5)
        self.assertEqual(self.session.kinds().count("attach_session"), 2)

    def test_the_start_barrier_pauses_at_the_anchor_then_says_ready_until_counted(self):
        self.session.commands.append(command(self.time, "pause", 0.0, state="waiting"))
        self.assertEqual(self.tick(12.0, False), [])  # Not yet: execute_at is ahead.
        self.assertEqual(self.tick(12.0, False, seconds=0.5), [("seek", 0.0), ("pause",)])
        self.tick(0.0, True, seconds=0.5)
        ready = [frame for frame in self.session.sent if frame["type"] == "ready"]
        self.assertEqual(ready[-1], {"type": "ready", "session_id": "s1", "position_seconds": 0.0,
                                     "is_paused": True, "command_id": "c1"})
        self.tick(0.0, True, seconds=0.5)
        self.assertEqual(self.session.kinds().count("ready"), 2)  # Every half second until counted.
        self.session.room["members"] = [{"is_self": True, "is_ready": True}]
        self.tick(0.0, True, seconds=0.5)
        self.assertEqual(self.session.kinds().count("ready"), 2)

    def test_play_runs_at_its_instant_from_where_the_room_is_by_then(self):
        self.session.room["playback_state"] = "playing"
        self.session.commands.append(command(self.time, "play", 30.0, after=0.0))
        self.assertEqual(self.tick(30.0, True, seconds=0.25), [("play",)])  # 0.25 s late: within tolerance.
        self.session.commands.append(command(self.time, "seek", 300.0, after=0.0, command_id="c2"))
        actions = self.tick(30.5, False, seconds=1.0)
        self.assertEqual(actions[0][0], "seek")
        self.assertAlmostEqual(actions[0][1], 301.0, places=2)  # Projected: one second late.

    def test_commands_for_another_selection_or_member_are_dropped(self):
        self.session.commands.append(command(self.time, "pause", 0.0, after=0.0, revision=1))
        self.assertEqual(self.tick(5.0, False), [])
        self.session.commands.append(command(self.time, "pause", 0.0, after=0.0, command_id="c3", session_id="s9"))
        self.assertEqual(self.tick(5.0, False), [])

    def test_leaving_playing_or_a_new_selection_stops_playback(self):
        self.session.room["phase"] = "lobby"
        self.assertEqual(self.tick(5.0, False), [("stop", "lobby")])
        self.session.room.update(phase="playing", selection_revision=3)
        self.assertEqual(self.tick(5.0, False), [("stop", "changed")])
        self.session.ended = "host_left"
        self.assertEqual(self.tick(5.0, False), [("stop", "host_left")])

    def test_state_reports_go_every_one_and_a_half_seconds_but_not_while_stalled(self):
        self.session.room["playback_state"] = "playing"
        self.session.commands.append(command(self.time, "play", 10.0, after=0.0))
        self.tick(10.0, True)
        for _ in range(8):
            self.tick(11.0, False)
        reports = [frame for frame in self.session.sent if frame["type"] == "state_report"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["command_id"], "c1")
        self.assertEqual(reports[0]["session_id"], "s1")
        for _ in range(12):
            self.tick(11.0, False, stalled=True)
        self.assertEqual(self.session.kinds().count("state_report"), 1)

    def test_a_stall_of_two_seconds_while_playing_reports_buffering_once(self):
        self.session.room["playback_state"] = "playing"
        for _ in range(7):
            self.tick(40.0, False, stalled=True)
        self.assertEqual(self.session.kinds().count("buffering"), 0)
        for _ in range(8):
            self.tick(40.0, False, stalled=True)
        self.assertEqual(self.session.kinds().count("buffering"), 1)

    def test_a_guests_own_pause_is_undone_unless_the_host_allows_it(self):
        self.session.room["playback_state"] = "playing"
        self.session.commands.append(command(self.time, "play", 10.0, after=0.0))
        self.tick(10.0, True)
        actions = self.tick(12.0, True, seconds=2.0)  # The viewer paused with the remote.
        self.assertIn(("play",), actions)
        self.assertTrue(any(action[0] == "notify" for action in actions))
        self.session.room["self_can_control_transport"] = True
        actions = self.tick(14.0, True, seconds=2.0)
        self.assertEqual(self.session.sent[-1]["type"], "transport_request")
        self.assertEqual((self.session.sent[-1]["action"], self.session.sent[-1]["is_paused"]), ("pause", True))
        self.assertNotIn(("play",), actions)

    def test_a_guests_seek_goes_back_and_a_hosts_seek_goes_to_the_room(self):
        self.session.room["playback_state"] = "playing"
        self.session.commands.append(command(self.time, "play", 10.0, after=0.0))
        self.tick(10.0, True)
        actions = self.tick(500.0, False, seconds=2.0)  # Jumped far ahead.
        self.assertEqual(actions[0][0], "seek")
        self.assertAlmostEqual(actions[0][1], 12.0, delta=0.3)
        self.session.room.update(self_role="host", self_can_control_transport=True)
        self.tick(12.0, False, seconds=2.0)
        self.tick(800.0, False, seconds=0.25)
        request = self.session.sent[-1]
        self.assertEqual((request["type"], request["action"], request["position_seconds"]),
                         ("transport_request", "seek", 800.0))

    def test_drift_is_corrected_by_a_seek_after_two_readings(self):
        self.session.room["playback_state"] = "playing"
        self.session.commands.append(command(self.time, "play", 100.0, after=0.0))
        self.tick(100.0, True)  # Plays at 100.25 (a quarter second late).
        self.assertEqual(self.tick(100.5, False, seconds=2.0), [])  # The room is at 102.25: 1.75 s behind…
        actions = self.tick(101.0, False, seconds=0.5)  # …and again at 102.75.
        self.assertEqual(actions[0][0], "seek")
        self.assertAlmostEqual(actions[0][1], 102.75, delta=0.05)

    def test_without_a_clock_nothing_runs(self):
        self.session.clock.known = False
        self.session.commands.append(command(self.time, "play", 10.0, after=0.0))
        self.assertEqual(self.tick(10.0, True), [])


if __name__ == "__main__":
    unittest.main()
