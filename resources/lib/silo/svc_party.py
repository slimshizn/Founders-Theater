"""Silo Watch Party calls. The server calls the feature "watch together".

Routes are Silo /api/v2: GET /watch-together/capabilities, POST /watch-together/join
({"code"} or {"join_token"} -> {room, room_access_token}), POST /watch-together/rooms
({"room_id", "selection_mode"}), GET and DELETE /watch-together/rooms/{id}, POST
.../ws-ticket, PUT .../staged-selection and .../selection, POST .../playback/start and
.../playback/stop, PATCH .../policy and .../selection-mode, GET .../picker, POST
.../member-state, the
suggestions (GET and POST .../suggestions, DELETE .../suggestions/{sid}, POST and
DELETE .../suggestions/{sid}/vote, POST .../suggestions/promote) and POST
.../source-fallback. A room's ``room_access_token`` (the room proof, a 24-hour JWT)
rides member calls as X-Room-Token; host calls check the host's account and profile
instead. Invitation links are ``<server>/rooms/join?token=<join token>``.
"""

import base64
import json
import uuid
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from .api import ApiError

BASE = "/watch-together"
SOCKET_PROTOCOL = "silo.room.v2"
TICKET_PREFIX = "silo.ticket."
# Join codes are eight characters from this alphabet (no I, O, 0 or 1).
CODE_ALPHABET = frozenset("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
GUEST_POLICIES = ("host_only", "guest_play_pause")
SELECTION_MODES = ("host_pick", "vote")
ROOM_FEATURES = ("watch_party_coordinator_v1", "fixed_media_file_v1")
FALLBACK_FEATURE = "watch_party_source_fallback_v1"
# The playback refusals a room may answer by moving everyone to another file.
FALLBACK_REASONS = frozenset({"no_alternate_version", "hdr_transcode_unsupported",
                              "subtitle_conversion_unsupported", "transcoding_disabled"})
SUGGESTION_TYPES = ("movie", "episode")
MAX_MEMBER_STATE_IDS = 200  # The most titles one member-state read takes.
JOIN_PATH = "/rooms/join"
APP_LINK_HOST = "watch-party"


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
    """What this server lets the profile do: {"guest", "host", "lobby_ready", "vote",
    "mode_switch", "picker", "fallback", "member_state"}.

    Joining needs the room socket (silo.room.v2, with the connection_replaced frame) and
    playback's watch_party_coordinator_v1 and fixed_media_file_v1, as Android requires;
    hosting also needs a staged lobby and stop. A host runs a vote only where it may start
    any suggestion (vote_host_override) and switches modes only where selection_mode_switch
    says so. Any failure supports nothing.
    """
    none = {"guest": False, "host": False, "lobby_ready": False, "vote": False, "mode_switch": False,
            "picker": False, "fallback": False, "member_state": False}
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
    return {"guest": guest, "host": host, "lobby_ready": guest and room.get("lobby_ready") is True,
            "vote": host and room.get("vote_host_override") is True,
            "mode_switch": host and room.get("selection_mode_switch") is True,
            "picker": guest and room.get("picker") is True,
            "fallback": guest and FALLBACK_FEATURE in features,
            "member_state": guest and room.get("member_state") is True}


def join(client, code=None, join_token=None):
    """Join by code or by an invitation's join token: (room, proof). Joining does not
    connect; the room socket does."""
    body = {"join_token": join_token} if join_token else {"code": code}
    return _answer(client.request("POST", BASE + "/join", body=body))


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


def set_mode(client, room_id, mode):
    """Host, in the lobby: "host_pick" or "vote". It drops the staged title and everyone's
    Ready; suggestions and votes stay."""
    if mode not in SELECTION_MODES:
        raise ValueError("Unknown selection mode: {}".format(mode))
    return _answer(client.request("PATCH", _room(_id(room_id)) + "/selection-mode", body={"selection_mode": mode}))


def select(client, room_id, content_id, file_id=None):
    """Host of a host_pick room that is playing: switch everyone to this title now."""
    body = {"content_id": str(content_id)}
    if file_id not in (None, ""):
        body["file_id"] = str(file_id)
    return _answer(client.request("PUT", _room(_id(room_id)) + "/selection", body=body))


def picker(client, room_id, token):
    """{"members", "continue_together", "watchlist_union"}: titles the room's members share."""
    response = client.request("GET", _room(_id(room_id)) + "/picker", headers={"X-Room-Token": token})
    return response if isinstance(response, dict) else {}


def member_state(client, room_id, token, content_ids):
    """{"members", "items"}: what each connected member has watched of these titles (the
    first 200 distinct ids), one item per title the caller may see. Each item's members
    carry "state" (unseen, in_progress or watched), "position_seconds" and "on_watchlist"."""
    ids = []
    for content_id in content_ids or ():
        content_id = "" if content_id is None else str(content_id).strip()
        if content_id and content_id not in ids:
            ids.append(content_id)
    if not ids:
        return {"members": [], "items": []}
    response = client.request("POST", _room(_id(room_id)) + "/member-state",
                              body={"content_ids": ids[:MAX_MEMBER_STATE_IDS]}, headers={"X-Room-Token": token})
    return response if isinstance(response, dict) else {}


def suggestions(client, room_id, token, pages=5):
    """Every suggestion in the room (at most 200), in the order they were made."""
    items, cursor = [], None
    for _page in range(pages):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = client.request("GET", _room(_id(room_id)) + "/suggestions", params=params,
                                  headers={"X-Room-Token": token})
        response = response if isinstance(response, dict) else {}
        items += [entry for entry in response.get("items") or () if isinstance(entry, dict)]
        page = response.get("page") if isinstance(response.get("page"), dict) else {}
        cursor = page.get("next_cursor") if page.get("has_more") else None
        if not cursor:
            break
    return items


def suggest(client, room_id, token, content_id, content_type, title, subtitle="", poster_url="",
            suggestion_id=None):
    """Suggest a movie or an episode; the suggestion's id. Repeating the same id is safe."""
    if content_type not in SUGGESTION_TYPES:
        raise ValueError("Only a movie or an episode can be suggested.")
    body = {"suggestion_id": suggestion_id or str(uuid.uuid4()), "content_id": str(content_id),
            "content_type": content_type, "title": str(title or content_id)}
    for key, value in (("subtitle", subtitle), ("poster_url", poster_url)):
        if value:
            body[key] = str(value)
    response = client.request("POST", _room(_id(room_id)) + "/suggestions", body=body,
                              headers={"X-Room-Token": token})
    return str((response if isinstance(response, dict) else {}).get("suggestion_id") or body["suggestion_id"])


def unsuggest(client, room_id, token, suggestion_id):
    """Remove a suggestion (the host's or one's own). One already gone is fine."""
    try:
        client.request("DELETE", _room(_id(room_id)) + "/suggestions/" + _id(suggestion_id),
                       headers={"X-Room-Token": token})
    except ApiError as error:
        if error.status != 404:
            raise


def vote(client, room_id, token, suggestion_id, on=True):
    """Vote for a suggestion, or take the vote back."""
    client.request("POST" if on else "DELETE", _room(_id(room_id)) + "/suggestions/" + _id(suggestion_id) + "/vote",
                   headers={"X-Room-Token": token})


def promote(client, room_id, token, suggestion_id):
    """Host: start a suggestion for everyone now (the tally is advice)."""
    return _answer(client.request("POST", _room(_id(room_id)) + "/suggestions/promote",
                                  body={"suggestion_id": str(suggestion_id)}, headers={"X-Room-Token": token}))


def fallback(client, room_id, token, revision, failed_file_id, reason):
    """Ask the room to move everyone to another file of the same title after this device's
    playback was refused for ``reason``. A stale request answers the room unchanged."""
    if reason not in FALLBACK_REASONS:
        raise ValueError("The room only falls back for its own refusal reasons.")
    body = {"selection_revision": int(revision), "failed_file_id": str(failed_file_id), "reason": reason}
    return _answer(client.request("POST", _room(_id(room_id)) + "/source-fallback", body=body,
                                  headers={"X-Room-Token": token}))


# ------------------------------------------------------------------ invitations

def invite_url(server_url, invite_path):
    """The web invitation link for a host's ``invite_path``, on this server and its path."""
    if not isinstance(invite_path, str) or not invite_path.startswith(JOIN_PATH + "?"):
        return None
    return str(server_url or "").rstrip("/") + invite_path


def _valid_server(url):
    parts = urlsplit(url)
    return (parts.scheme in ("http", "https") and bool(parts.netloc) and "@" not in parts.netloc
            and not parts.query and not parts.fragment and not any(char.isspace() for char in parts.netloc))


def _single(query, name):
    """The one non-empty value of ``name``; None when it is missing, empty or repeated."""
    values = [value for key, value in parse_qsl(query, keep_blank_values=True) if key == name]
    return values[0] if len(values) == 1 and values[0].strip() else None


def parse_invite(text):
    """("code", code), ("link", server, join_token) or None, for what a viewer typed: a code,
    a web link ``<server>/rooms/join?token=…`` or an app link
    ``silo://watch-party?server=<server>&token=…``, checked as strictly as Android does."""
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        return None
    code = normalize_code(text)
    if code:
        return ("code", code)
    scheme = text.split("://", 1)[0].lower() if "://" in text else ""
    if scheme in ("http", "https"):
        base, _, query = text.split("#", 1)[0].partition("?")
        if not base.endswith(JOIN_PATH):
            return None
        server = base[:-len(JOIN_PATH)]
        names = {key for key, _value in parse_qsl(query, keep_blank_values=True)}
        token = _single(query, "token")
        if not _valid_server(server) or token is None or names != {"token"}:
            return None
        return ("link", server, token)
    if scheme == "silo" and "#" not in text:
        rest = text.split("://", 1)[1]
        host, _, query = rest.partition("?")
        if host.rstrip("/").lower() != APP_LINK_HOST:
            return None
        names = {key for key, _value in parse_qsl(query, keep_blank_values=True)}
        server, token = _single(query, "server"), _single(query, "token")
        if names - {"server", "token"} or server is None or token is None:
            return None
        server = server.rstrip("/")  # parse_qsl has already decoded it.
        return ("link", server, token) if _valid_server(server) else None
    return None


def _comparable(url):
    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.query \
            or parts.fragment:
        return None
    scheme = parts.scheme.lower()
    return scheme, parts.hostname.lower(), port or (443 if scheme == "https" else 80), parts.path.rstrip("/")


def same_server(link_server, active_server):
    """Whether an invitation names the server this profile is signed in to: the same scheme,
    host, port and path. A join token is never sent to another server."""
    link, active = _comparable(link_server), _comparable(active_server)
    return link is not None and link == active


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
