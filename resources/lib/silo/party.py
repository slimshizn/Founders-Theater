"""One Watch Party engagement: the room proof, the room socket kept open, and its state.

A background thread mints a single-use ticket, opens the room socket and reads it;
frames update the newest room snapshot (never with an older ``generation``), queue
transport commands, and feed the room clock through JSON ping/pong. The socket lasts
at most five minutes and can drop at any time, so the thread reconnects with a fresh
ticket, renewing the proof first when it nears expiry. ``room_closed`` and
``connection_replaced`` end the engagement for good, as does leaving.

Lobby and player code read ``snapshot()``, take commands and send frames through
``send()``; nothing here touches Kodi. See the Watch Party spec in the notes and
Silo's docs/realtime-api.md for the protocol.
"""

import json
import random
import threading
import time
import uuid

from . import room_clock, svc_party, wsclient
from .api import ApiError

HEALTHY_SECONDS = 30.0
GIVE_UP_FAILURES, GIVE_UP_SECONDS = 6, 150.0
RENEW_BEFORE_SECONDS = 360.0
WARM_SAMPLES = 4
# Why an engagement ended; "lost" means the server stayed unreachable.
ENDINGS = ("left", "host_left", "ended", "not_found", "replaced", "unauthorized", "lost")


def new_room_id():
    return str(uuid.uuid4())


class RoomSession:
    def __init__(self, client, room, token, connect=wsclient.connect, clock=None, backoff=(0.5, 1.0, 2.0, 5.0),
                 ping_fast=2.0, ping_slow=15.0, log=None, wall=time.time, mono=time.monotonic):
        self.client = client
        self.room_id = str(room.get("room_id") or "")
        self.token = token
        self.clock = clock or room_clock.RoomClock(wall=wall, mono=mono)
        self.connect = connect
        self.backoff, self.ping_fast, self.ping_slow = tuple(backoff), ping_fast, ping_slow
        self.log = log or (lambda event: None)
        self.wall, self.mono = wall, mono
        self.ended = None
        self.connected = False
        self.last_error = ""
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._snapshot = dict(room)
        self._commands = []
        self._socket = None
        self._stop = threading.Event()
        self._threads = []
        self._connection = 0       # Counts sockets, so pings follow the one now open.
        self._samples = 0          # Clock samples taken on this socket.
        self._last_ping = 0.0

    # ------------------------------------------------------------ state
    def start(self):
        for target, name in ((self._run, "silo-party-socket"), (self._ping_loop, "silo-party-ping")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)

    def is_host(self):
        return self.snapshot().get("self_role") == "host"

    def wait(self, timeout):
        """Block until the state changes (or ``timeout`` passes)."""
        with self._changed:
            self._changed.wait(timeout)

    def pending_commands(self):
        with self._lock:
            return len(self._commands)

    def take_command(self):
        """The newest transport command not yet taken; older pending ones are dropped."""
        with self._lock:
            command = self._commands[-1] if self._commands else None
            self._commands = []
            return command

    def accept_http(self, room, token):
        """A room snapshot from an HTTP answer (stage, start, stop, read): its proof is the
        newest, but the snapshot only replaces an older generation (the socket wins a tie)."""
        with self._changed:
            if token:
                self.token = token
            if isinstance(room, dict) and room and self._generation(room) > self._generation(self._snapshot):
                self._snapshot = dict(room)
            self._changed.notify_all()

    @staticmethod
    def _generation(room):
        value = room.get("generation")
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    def send(self, frame):
        """Send one frame on the open socket; False when there is none."""
        with self._lock:
            sock = self._socket if self.connected else None
        if sock is None:
            return False
        try:
            sock.send_text(json.dumps(frame, separators=(",", ":")))
            return True
        except wsclient.WebSocketError:
            return False

    def leave(self):
        """Stop for good: close the socket (a guest leaves by closing it)."""
        with self._changed:
            if self.ended is None:
                self.ended = "left"
            sock, self._socket, self.connected = self._socket, None, False
            self._changed.notify_all()
        self._stop.set()
        if sock is not None:
            try:
                sock.close(1000)
            except wsclient.WebSocketError:
                pass

    def _end(self, reason):
        with self._changed:
            if self.ended is None:
                self.ended = reason
            sock, self._socket, self.connected = self._socket, None, False
            self._changed.notify_all()
        self._stop.set()
        self.log("party_ended_" + reason)
        if sock is not None:
            try:
                sock.close(1000)
            except wsclient.WebSocketError:
                pass

    # ------------------------------------------------------------ the socket
    def _renew_if_due(self, force=False):
        expiry = svc_party.proof_expiry(self.token)
        if force or (expiry is not None and expiry - self.wall() < RENEW_BEFORE_SECONDS):
            room, token = svc_party.read(self.client, self.room_id, self.token)
            self.accept_http(room, token)

    def _mint(self):
        """A ticket, or None after ending the engagement. Network trouble raises ApiError."""
        renewed = False
        while True:
            try:
                self._renew_if_due()
                return svc_party.ticket(self.client, self.room_id, self.token)
            except ApiError as error:
                if error.status == 403 and not renewed:
                    renewed = True
                    self._renew_if_due(force=True)
                    continue
                if error.status in (403, 422):
                    self._end("unauthorized")
                elif error.status == 404:
                    self._end("not_found")
                elif error.status == 409:
                    self._end("ended")
                else:
                    raise
                return None

    def _run(self):
        failures, last_healthy = 0, self.mono()
        while not self._stop.is_set():
            opened_at = None
            try:
                ticket = self._mint()
                if ticket is None or self._stop.is_set():
                    return
                sock = self.connect(svc_party.socket_url(self.client.server_url, self.room_id),
                                    [svc_party.SOCKET_PROTOCOL, svc_party.TICKET_PREFIX + ticket])
                with self._changed:
                    if self._stop.is_set():
                        sock.close(1000)
                        return
                    self._socket, self.connected = sock, True
                    self._connection += 1
                    self._samples, self._last_ping = 0, 0.0
                    self._changed.notify_all()
                opened_at = self.mono()
                self.log("party_connected")
                while not self._stop.is_set():
                    self._dispatch(sock.recv())
            except wsclient.Closed as closed:
                self.log("party_socket_closed_{}".format(closed.code))
            except (wsclient.WebSocketError, ApiError, OSError, ValueError) as error:
                self.last_error = type(error).__name__
                self.log("party_connect_failed")
            finally:
                with self._changed:
                    self.connected = False
                    self._socket = None
                    self._changed.notify_all()
            if self._stop.is_set() or self.ended is not None:
                return
            now = self.mono()
            if opened_at is not None and now - opened_at >= HEALTHY_SECONDS:
                failures, last_healthy = 0, now
                continue  # A socket that lasted (the five-minute renewal) reconnects at once.
            if opened_at is not None:
                last_healthy = now
            failures += 1
            if failures >= GIVE_UP_FAILURES and now - last_healthy >= GIVE_UP_SECONDS:
                self._end("lost")
                return
            delay = self.backoff[min(failures - 1, len(self.backoff) - 1)]
            self._stop.wait(delay * (1 + random.random() * 0.25))

    def _dispatch(self, text):
        try:
            frame = json.loads(text)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        kind = frame.get("type")
        if kind == "snapshot" and isinstance(frame.get("room"), dict):
            with self._changed:
                if self._generation(frame["room"]) >= self._generation(self._snapshot):
                    self._snapshot = dict(frame["room"])
                self._changed.notify_all()
        elif kind == "transport_command" and isinstance(frame.get("command"), dict):
            with self._changed:
                self._commands.append(dict(frame["command"]))
                self._changed.notify_all()
        elif kind == "pong":
            if self.clock.add(frame.get("client_sent_at"), frame.get("server_received_at"),
                              frame.get("server_sent_at")):
                with self._lock:
                    self._samples += 1
        elif kind == "room_closed":
            self._end("host_left" if frame.get("reason") in (None, "", "host_left") else
                      "not_found" if frame.get("reason") == "not_found" else "ended")
        elif kind == "connection_replaced":
            self._end("replaced")
        elif kind == "error":
            self.last_error = str(frame.get("message") or frame.get("code") or "")[:200]
            self.log("party_error_frame")

    def _ping_loop(self):
        """JSON pings for the clock: at once, every ``ping_fast`` until four samples, then
        every ``ping_slow`` (Silo's synchronization doc)."""
        while not self._stop.wait(min(0.25, self.ping_fast)):
            with self._lock:
                if not self.connected:
                    continue
                interval = self.ping_fast if self._samples < WARM_SAMPLES else self.ping_slow
                due = self._last_ping == 0.0 or self.mono() - self._last_ping >= interval
                if due:
                    self._last_ping = self.mono()
            if due:
                self.send({"type": "ping", "client_sent_at": self.clock.stamp()})
