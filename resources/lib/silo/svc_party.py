"""Silo Watch Party calls. The server calls the feature "watch together".

Routes are Silo /api/v2: GET /watch-together/capabilities, POST /watch-together/join
({"code"} -> {room, room_access_token}), POST /watch-together/rooms ({"room_id",
"selection_mode"}), GET and DELETE /watch-together/rooms/{id}, POST .../ws-ticket,
PUT .../staged-selection, POST .../playback/start and .../playback/stop and
PATCH .../policy. A room's ``room_access_token`` (the room proof, a 24-hour JWT) rides
member calls as X-Room-Token; host calls check the host's account and profile instead.
"""

import base64
import json
from urllib.parse import quote, urlsplit, urlunsplit

from .api import ApiError

BASE = "/watch-together"
SOCKET_PROTOCOL = "silo.room.v2"
TICKET_PREFIX = "silo.ticket."
# Join codes are eight characters from this alphabet (no I, O, 0 or 1).
CODE_ALPHABET = frozenset("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
GUEST_POLICIES = ("host_only", "guest_play_pause")
ROOM_FEATURES = ("watch_party_coordinator_v1", "fixed_media_file_v1")


def _id(value):
    return quote(str(value), safe="")


def _room(path):
    return "{}/rooms/{}".format(BASE, path)


def _answer(response):
    response = response if isinstance(response, dict) else {}
    room = response.get("room") if isinstance(response.get("room"), dict) else {}
    return room, str(response.get("room_access_token") or "")


def normalize_code(text):
    """A typed join code as the server matches it, or None when it cannot be one."""
    if not isinstance(text, str):
        return None
    code = "".join(char for char in text if not char.isspace() and char != "-").upper()
    return code if len(code) == 8 and all(char in CODE_ALPHABET for char in code) else None


def support(client):
    """What this server lets the profile do: {"guest", "host", "lobby_ready"}.

    Joining needs the room socket (silo.room.v2, with the connection_replaced frame) and
    playback's watch_party_coordinator_v1 and fixed_media_file_v1, as Android requires;
    hosting also needs a staged lobby and stop. Any failure supports nothing.
    """
    none = {"guest": False, "host": False, "lobby_ready": False}
    try:
        room = client.request("GET", BASE + "/capabilities")
        playback = client.request("GET", "/playback/capabilities")
    except (ApiError, OSError, ValueError):
        return none
    room = room if isinstance(room, dict) else {}
    features = (playback if isinstance(playback, dict) else {}).get("features") or []
    guest = (room.get("state") == "available" and room.get("allowed") is True
             and room.get("socket_protocol") == SOCKET_PROTOCOL and room.get("connection_replaced") is True
             and all(feature in features for feature in ROOM_FEATURES))
    host = guest and room.get("staged_selection") is True and room.get("stop_playback") is True
    return {"guest": guest, "host": host, "lobby_ready": guest and room.get("lobby_ready") is True}


def join(client, code):
    """Join by code: (room, proof). Joining does not connect; the room socket does."""
    return _answer(client.request("POST", BASE + "/join", body={"code": code}))


def create(client, room_id, selection_mode="host_pick"):
    """Create a room with a client-chosen UUID; the caller hosts it. (room, proof)."""
    return _answer(client.request("POST", BASE + "/rooms", body={"room_id": room_id, "selection_mode": selection_mode}))


def read(client, room_id, token):
    """The room now, with a renewed proof: (room, proof)."""
    return _answer(client.request("GET", _room(_id(room_id)), headers={"X-Room-Token": token}))


def ticket(client, room_id, token):
    """A single-use socket ticket (about 30 seconds)."""
    response = client.request("POST", _room(_id(room_id)) + "/ws-ticket", headers={"X-Room-Token": token})
    return str((response if isinstance(response, dict) else {}).get("ticket") or "")


def stage(client, room_id, content_id, file_id=None):
    """Host: put a title in the lobby (host_pick rooms); members get it in the next snapshot."""
    body = {"content_id": str(content_id)}
    if file_id not in (None, ""):
        body["file_id"] = str(file_id)
    return _answer(client.request("PUT", _room(_id(room_id)) + "/staged-selection", body=body))


def start(client, room_id):
    """Host: start the staged title for everyone."""
    return _answer(client.request("POST", _room(_id(room_id)) + "/playback/start"))


def stop(client, room_id):
    """Host: stop playback and return everyone to the lobby."""
    return _answer(client.request("POST", _room(_id(room_id)) + "/playback/stop"))


def set_policy(client, room_id, policy):
    """Host: who may play and pause. The answer's policy is the one in force."""
    if policy not in GUEST_POLICIES:
        raise ValueError("Unknown guest control policy: {}".format(policy))
    return _answer(client.request("PATCH", _room(_id(room_id)) + "/policy", body={"guest_control_policy": policy}))


def close(client, room_id):
    """Host: end the party for everyone. A room that already ended is fine."""
    try:
        client.request("DELETE", _room(_id(room_id)))
    except ApiError as error:
        if error.status != 409:
            raise


def socket_url(server_url, room_id):
    """wss:// (ws:// for http) of the room socket on this server, keeping a path prefix."""
    parts = urlsplit(server_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = parts.path.rstrip("/") + "/api/v2" + _room(_id(room_id)) + "/ws"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def proof_expiry(token):
    """The room proof's expiry (epoch seconds) read from its claims, or None. Not a check:
    the server verifies the signature; the add-on only renews before it runs out."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8"))
        expiry = claims.get("exp")
    except (AttributeError, IndexError, ValueError, UnicodeError):
        return None
    return expiry if isinstance(expiry, (int, float)) and not isinstance(expiry, bool) else None
