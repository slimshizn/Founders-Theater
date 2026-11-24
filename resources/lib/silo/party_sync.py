"""One member's playback kept on a Watch Party room's clock (Android RoomPlaybackBinding).

The player loop ticks ``RoomPlayback`` with the source position, whether Kodi is
paused, and whether it is stalled; the binding answers with what the player must do,
as ("pause",), ("play",), ("seek", source_seconds), ("notify", text) or
("stop", reason). It sends the room what the room needs:

- ``attach_session`` once playback started, again every 2 s until the room echoes it;
- each ``transport_command`` runs at its ``execute_at`` on the server clock, late
  commands projected to where the room is by then;
- ``ready`` every half second after a waiting command until the room counts it, and
  after a stall while the room catches this member up;
- ``state_report`` every 1.5 s, ``buffering`` after 2 s of stall while the room plays.

Kodi cannot change speed by a percent or two, so drift is corrected with a seek once
two readings in a row are more than a second off (the seek-only path in Silo's
synchronization doc). The viewer's own pause or play goes to the room as a request
when the room allows it and is undone when not; a guest's seek is undone, a host's
becomes the room's.
"""

import time

from . import room_clock

ATTACH_RETRY = 2.0
READY_INTERVAL = 0.5
REPORT_INTERVAL = 1.5
BUFFERING_AFTER = 2.0
MAX_WAIT = 5.0          # A command is never held longer than this past its arrival.
OWN_ACTION = 1.5        # Player state changes this soon after ours are ours.
REQUEST_WAIT = 3.0      # …and after asking the room, until its command arrives.
TOLERANCE = 1.0         # Off by more than this: seek (a waiting seek is counted within it too).
USER_SEEK = 5.0         # A jump this large that we did not make is the viewer's seek.
CORRECTION_GAP = 5.0
NOTICES = {
    "pause": "Only the host can pause the Watch Party.",
    "play": "Only the host can resume the Watch Party.",
    "seek": "Only the host can move the Watch Party.",
}


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class RoomPlayback:
    def __init__(self, session, mono=time.monotonic):
        self.session = session
        self.mono = mono
        self.revision = session.snapshot().get("selection_revision")
        self.session_id = None
        self.attached_at = None
        self.pending = None          # (command, arrived_at)
        self.last_command_id = None
        self.reference = None        # (position at execute_at, execute_at, playing)
        self.expected_paused = True  # A member's player never starts on its own.
        self.armed = None            # (command_id, seek target or None) awaiting readiness
        self.quiet_until = 0.0
        self.last_report = None
        self.last_ready = None
        self.stall_since = None
        self.buffering_sent = False
        self.was_stalled = False
        self.strikes = 0
        self.last_correction = None
        self.noticed = set()
        self.stopped = None  # Why the room ended this playback ("lobby", "changed" or an ending).

    # ------------------------------------------------------------ helpers
    def attach(self, session_id):
        self.session_id = session_id
        self.attached_at = self.mono()
        self.session.send({"type": "attach_session", "session_id": session_id})

    def _send(self, kind, position, paused, **extra):
        frame = {"type": kind, "session_id": self.session_id, "position_seconds": round(position, 3),
                 "is_paused": bool(paused)}
        frame.update({key: value for key, value in extra.items() if value is not None})
        self.session.send(frame)

    @staticmethod
    def _self(room):
        return next((member for member in room.get("members") or () if isinstance(member, dict)
                     and member.get("is_self")), {})

    def expected_position(self, server_now):
        if self.reference is None:
            return None
        position, executed, playing = self.reference
        return position + max(0.0, server_now - executed) if playing else position

    # ------------------------------------------------------------ the tick
    def _stop(self, reason):
        self.stopped = reason
        return [("stop", reason)]

    def tick(self, position, paused, stalled=False):
        now = self.mono()
        if self.session.ended is not None:
            return self._stop(self.session.ended)
        room = self.session.snapshot()
        if room.get("phase") != "playing":
            return self._stop("lobby" if room.get("phase") == "lobby" else "ended")
        if room.get("selection_revision") != self.revision:
            return self._stop("changed")
        if self.session_id and room.get("attached_session_id") != self.session_id \
                and now - self.attached_at >= ATTACH_RETRY:
            self.attach(self.session_id)
        command = self.session.take_command()
        if command and command.get("command_id") and command.get("command_id") != self.last_command_id:
            self.pending = (command, now)
        server_now = self.session.clock.now()
        if server_now is None:
            return []  # Without the server's time no command can run on cue.
        actions = []
        if self.pending is not None:
            command, arrived = self.pending
            due = room_clock.parse_instant(command.get("execute_at"))
            if due is None or server_now >= due or now - arrived >= MAX_WAIT:
                self.pending = None
                actions += self._execute(command, position, paused, server_now, due, room, now)
        self._readiness(room, position, paused, stalled, now)
        self._buffering(room, position, paused, stalled, now)
        if not actions and not stalled and now >= self.quiet_until and self.reference is not None:
            actions += self._viewer(room, position, paused, server_now, now)
        if not actions and not stalled and now >= self.quiet_until and self.reference is not None:
            actions += self._drift(position, paused, server_now, now)
        if (not stalled and now >= self.quiet_until and self.session_id
                and (self.last_report is None or now - self.last_report >= REPORT_INTERVAL)):
            self.last_report = now
            self._send("state_report", position, paused, command_id=self.last_command_id)
        return actions

    def _execute(self, command, position, paused, server_now, due, room, now):
        self.last_command_id = command.get("command_id")
        if command.get("selection_revision") not in (None, self.revision):
            return []
        if command.get("session_id") and command.get("session_id") != self.session_id:
            return []
        target = _seconds(command.get("position_seconds"))
        if target is None:
            return []
        action, state = command.get("action"), command.get("playback_state")
        playing = {"play": state != "waiting", "pause": False, "seek": state == "playing"}.get(action)
        if playing is None:
            return []
        executed = due if due is not None else server_now
        late = max(0.0, server_now - executed)
        goal = target + late if playing else target
        actions = []
        if action == "seek" or abs(position - goal) > TOLERANCE:
            actions.append(("seek", goal))
        if playing and paused:
            actions.append(("play",))
        elif not playing and not paused:
            actions.append(("pause",))
        self.reference = (target, executed, playing)
        self.expected_paused = not playing
        self.quiet_until = now + OWN_ACTION
        self.strikes = 0
        if state == "waiting":
            self.armed = (command.get("command_id"), goal if action == "seek" else None)
            self.last_ready = None
        return actions

    def _readiness(self, room, position, paused, stalled, now):
        me = self._self(room)
        if me.get("is_ready"):
            self.armed = None
            return
        catching_up = room.get("self_ignore_wait") or me.get("is_buffering")
        waiting = self.armed is not None and room.get("playback_state") == "waiting"
        if not waiting:
            self.armed = None
        if stalled or not (waiting or catching_up) or paused != self.expected_paused:
            return
        if waiting and self.armed[1] is not None and abs(position - self.armed[1]) > TOLERANCE:
            return
        if self.last_ready is not None and now - self.last_ready < READY_INTERVAL:
            return
        self.last_ready = now
        command_id = self.armed[0] if waiting else self.last_command_id
        self._send("ready", position, paused, command_id=command_id)

    def _buffering(self, room, position, paused, stalled, now):
        if not stalled:
            self.stall_since, self.buffering_sent = None, False
            return
        self.stall_since = self.stall_since if self.stall_since is not None else now
        if (not self.buffering_sent and room.get("playback_state") == "playing"
                and now - self.stall_since >= BUFFERING_AFTER):
            self.buffering_sent = True
            self._send("buffering", position, paused)

    def _notice(self, kind):
        if kind in self.noticed:
            return []
        self.noticed.add(kind)
        return [("notify", NOTICES[kind])]

    def _viewer(self, room, position, paused, server_now, now):
        """The viewer paused, resumed or seeked with Kodi's own controls."""
        host = room.get("self_role") == "host"
        allowed = bool(room.get("self_can_control_transport"))
        if paused != self.expected_paused:
            wanted = "pause" if paused else "play"
            if allowed:
                self.session.send({"type": "transport_request", "action": wanted,
                                   "position_seconds": round(position, 3), "is_paused": bool(paused)})
                self.expected_paused = paused
                self.quiet_until = now + REQUEST_WAIT
                return []
            self.quiet_until = now + OWN_ACTION
            return [("play",) if paused else ("pause",)] + self._notice(wanted)
        expected = self.expected_position(server_now)
        if expected is not None and abs(position - expected) > USER_SEEK:
            if host and allowed:
                self.session.send({"type": "transport_request", "action": "seek",
                                   "position_seconds": round(position, 3), "is_paused": bool(paused)})
                self.quiet_until = now + REQUEST_WAIT
                return []
            self.quiet_until = now + OWN_ACTION
            self.last_correction = now
            return [("seek", expected)] + self._notice("seek")
        return []

    def _drift(self, position, paused, server_now, now):
        if paused or not self.reference[2]:
            self.strikes = 0
            return []
        drift = position - self.expected_position(server_now)
        if abs(drift) <= TOLERANCE:
            self.strikes = 0
            return []
        self.strikes += 1
        if self.strikes < 2 or (self.last_correction is not None and now - self.last_correction < CORRECTION_GAP):
            return []
        self.strikes = 0
        self.last_correction = now
        self.quiet_until = now + OWN_ACTION
        return [("seek", self.expected_position(server_now))]
