"""The Watch Party room clock: Silo's server time from ping/pong (Android RoomClock)."""

import unittest

from resources.lib.silo import room_clock


def instant(seconds):
    """RFC 3339 for epoch ``seconds``, with nanoseconds as Silo sends them."""
    return room_clock.format_instant(seconds, digits=9)


class Clock:
    def __init__(self, wall=1_800_000_000.0, mono=100.0):
        self.wall, self.mono = wall, mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class RoomClockTests(unittest.TestCase):
    def setUp(self):
        self.time = Clock()
        self.clock = room_clock.RoomClock(wall=lambda: self.time.wall, mono=lambda: self.time.mono)

    def exchange(self, server_ahead, rtt, hold=0.002):
        """One ping/pong with the server ``server_ahead`` seconds ahead and ``rtt`` on the wire."""
        sent = self.clock.stamp()
        t0 = self.time.wall
        self.time.advance(rtt / 2)
        received = t0 + rtt / 2 + server_ahead
        self.time.advance(hold)
        self.time.advance(rtt / 2)
        return self.clock.add(sent, instant(received), instant(received + hold))

    def test_instants_parse_with_nanoseconds_and_offsets(self):
        self.assertAlmostEqual(room_clock.parse_instant("2026-11-24T20:00:00.123456789Z"),
                               1795550400.123456, places=5)
        self.assertAlmostEqual(room_clock.parse_instant("2026-11-24T21:00:00+01:00"), 1795550400.0)
        self.assertIsNone(room_clock.parse_instant("yesterday"))
        self.assertIsNone(room_clock.parse_instant(None))

    def test_no_estimate_until_a_sample(self):
        self.assertIsNone(self.clock.now())
        self.assertTrue(self.exchange(server_ahead=3.0, rtt=0.08))
        self.assertAlmostEqual(self.clock.now() - self.time.wall, 3.0, places=2)

    def test_the_fastest_round_trip_wins(self):
        self.exchange(server_ahead=3.0, rtt=0.40)
        self.exchange(server_ahead=3.0, rtt=0.02)
        self.exchange(server_ahead=3.0, rtt=0.30)
        self.assertAlmostEqual(self.clock.offset(), 3000, delta=2)
        self.assertEqual(self.clock.samples(), 3)

    def test_bad_samples_are_refused_and_a_stepped_clock_starts_over(self):
        self.assertFalse(self.exchange(server_ahead=3.0, rtt=6.0))  # Over 5 s.
        self.exchange(server_ahead=3.0, rtt=0.05)
        self.exchange(server_ahead=3.0, rtt=0.05)
        self.assertTrue(self.exchange(server_ahead=9.0, rtt=0.05))  # The server clock stepped.
        self.assertEqual(self.clock.samples(), 1)
        self.assertAlmostEqual(self.clock.offset(), 9000, delta=2)

    def test_a_local_wall_clock_jump_resets_the_estimate(self):
        self.exchange(server_ahead=3.0, rtt=0.05)
        self.time.wall += 30  # NTP stepped the box's clock; the monotonic clock did not move.
        self.assertIsNone(self.clock.now())

    def test_only_the_last_eight_samples_are_kept(self):
        for _ in range(10):
            self.exchange(server_ahead=3.0, rtt=0.05)
        self.assertEqual(self.clock.samples(), 8)


if __name__ == "__main__":
    unittest.main()
