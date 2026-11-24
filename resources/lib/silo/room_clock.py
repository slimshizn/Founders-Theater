"""Silo's server clock for a Watch Party room, estimated from the room socket's ping/pong.

A room runs every command at a server instant (``execute_at``), so each member needs
the server's time. This follows Android's RoomClock (and Silo's synchronization doc):
NTP-style samples from {"type":"ping","client_sent_at"} and the pong's
server_received_at and server_sent_at, the offset of the fastest of the last eight
round trips, a restart when the server clock steps, and a reset when this box's wall
clock jumps against its monotonic clock.
"""

import datetime
import re
import time

MAX_SAMPLES = 8
MAX_AGE_SECONDS = 600
MAX_RTT_MS = 5000
STEP_SLACK_MS = 100
JUMP_SECONDS = 1.0
_INSTANT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")


def parse_instant(text):
    """Epoch seconds for an RFC 3339 instant (any number of fractional digits), or None."""
    if not isinstance(text, str):
        return None
    match = _INSTANT.match(text.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        moment = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                                   tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    seconds = moment.timestamp() + (float("0." + fraction) if fraction else 0.0)
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        seconds -= sign * (int(zone[1:3]) * 3600 + int(zone[4:6]) * 60)
    return seconds


def format_instant(seconds, digits=3):
    """RFC 3339 UTC for epoch ``seconds`` with ``digits`` fractional digits (3 or 9)."""
    whole = int(seconds // 1)
    fraction = seconds - whole
    moment = datetime.datetime.fromtimestamp(whole, tz=datetime.timezone.utc)
    text = "{:0{}d}".format(int(round(fraction * 10 ** digits)) % 10 ** digits, digits)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + "." + text + "Z"


class RoomClock:
    def __init__(self, wall=time.time, mono=time.monotonic):
        self.wall, self.mono = wall, mono
        self._samples = []  # (offset_ms, rtt_ms, taken_at_mono)
        self._base = None   # wall - mono when the samples were taken

    def stamp(self):
        """The ping's client_sent_at: now, as RFC 3339 with milliseconds (the pong echoes it)."""
        return format_instant(self.wall(), digits=3)

    def add(self, client_sent_at, server_received_at, server_sent_at):
        """Take one pong; False when the sample is refused."""
        t0, t1, t2 = (parse_instant(value) for value in (client_sent_at, server_received_at, server_sent_at))
        if t0 is None or t1 is None or t2 is None:
            return False
        self._check_jump()
        t3 = self.wall()
        hold = (t2 - t1) * 1000
        rtt = (t3 - t0) * 1000 - hold
        if hold < 0 or rtt < -2 or rtt > MAX_RTT_MS:
            return False
        rtt = max(0.0, rtt)
        offset = round(((t1 - t0) + (t2 - t3)) * 1000 / 2)
        best = self._best()
        if best is not None and abs(offset - best[0]) * 2 > rtt + best[1] + STEP_SLACK_MS:
            self._samples = []  # The server's clock stepped: older samples describe another clock.
        now = self.mono()
        self._samples = [sample for sample in self._samples if now - sample[2] <= MAX_AGE_SECONDS]
        self._samples.append((offset, rtt, now))
        del self._samples[:-MAX_SAMPLES]
        self._base = self.wall() - now
        return True

    def _best(self):
        if not self._samples:
            return None
        # Lowest round trip; on a tie the newest sample.
        return min(reversed(self._samples), key=lambda sample: sample[1])

    def _check_jump(self):
        if self._base is not None and abs((self.wall() - self.mono()) - self._base) > JUMP_SECONDS:
            self._samples, self._base = [], None

    def samples(self):
        return len(self._samples)

    def offset(self):
        """Milliseconds to add to this box's wall clock for the server's, or None."""
        self._check_jump()
        best = self._best()
        return best[0] if best is not None else None

    def now(self):
        """The server's time in epoch seconds, or None without an estimate."""
        offset = self.offset()
        return None if offset is None else self.wall() + offset / 1000.0
