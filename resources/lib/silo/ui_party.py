"""Watch Party screens: the hub, joining with a code or an invitation link, hosting, the
lobby with its vote, the title picker, the invitation page, and the menu Back opens during
a party (Android's Watch Party hub, lobby, picker, invite page and the player's party panel).

``party.RoomSession`` keeps the room connected, ``party_sync.RoomPlayback`` keeps the
player on the room's clock, and ``svc_party`` makes the calls. A guest joins with the
host's code or invitation link, waits in the lobby (and says Ready, or suggests and votes),
follows the host's play, pause and seek, and leaves. A host starts a party with a title or
an empty one (the host picks, or everyone votes), starts it for everyone, decides whether
guests may play and pause, returns everyone to the lobby, and ends it.

The party stays open while the viewer looks around, as on Android: Back in the lobby steps
away, the profile menu's Return to the Watch Party comes back, and a title page offers
Suggest to Party, Add to party or Play for everyone while one is open. A party that stopped
here because this profile joined it on another device, or lost its connection, offers
Rejoin; the hub offers the last party again.
"""

import glob
import hashlib
import os
import threading

import xbmcgui

from . import party, present, svc_party
from .api import ApiError
from .party_sync import RoomPlayback
from .ui_common import BACK_ACTIONS, set_properties

PRIMARY, LEAVE, POLICY, SUGGEST, MODE, INVITE = 200, 201, 202, 203, 204, 205
MEMBERS, SUGGESTIONS = 300, 301
INVITE_DONE = 100
RECENT_KEY = "watch_party_recent"
HEADING = "Watch Party"
UNSUPPORTED = "Watch Party isn't available for this profile on this Silo server."
NO_HOSTING = "This Silo server lets this profile join a Watch Party but not host one."
NOT_PLAYABLE = "Choose a movie or an episode to watch together."
JOIN_FIELD = "Party code or invite link"
BAD_CODE = "Enter a Watch Party code, such as ABCD 2345, or an invite link."
OTHER_SERVER = "This invitation is for another Silo server than the one you're signed in to."
NO_PARTY = "No Watch Party has that code."
REJOIN_FAILED = "Couldn't rejoin. The party may have ended."
LEAVE_CONFIRM = "Leave the Watch Party?"
END_CONFIRM = "End the Watch Party for everyone?"
STEPPED_AWAY = "The Watch Party stays open. Return to it from the profile menu."
STARTING_AWAY = "The Watch Party is starting. Return to it from the profile menu."
POLICY_LABELS = {"host_only": "Guests watch only", "guest_play_pause": "Guests can play and pause"}
ENDINGS = {
    "host_left": "The host ended the Watch Party.",
    "ended": "This Watch Party has ended.",
    "not_found": "This Watch Party no longer exists.",
    "replaced": "This profile joined the Watch Party on another device.",
    "unauthorized": "This profile can no longer join this Watch Party.",
    "lost": "Lost the connection to the Watch Party.",
}
# Endings after which the same party can be joined again.
REJOINABLE = ("left", "replaced", "lost")


def _log(event):
    try:
        import xbmc

        xbmc.log("[Silo] party event=" + event, xbmc.LOGINFO)
    except Exception:  # Logging must never break a party.
        pass


def _self(room):
    return next((member for member in room.get("members") or () if isinstance(member, dict)
                 and member.get("is_self")), {})


def _host_name(room):
    host = next((member for member in room.get("members") or () if isinstance(member, dict)
                 and member.get("is_host")), {})
    return str(host.get("display_name") or "The host")


def format_code(code):
    code = str(code or "")
    return "{} {}".format(code[:4], code[4:]) if len(code) == 8 else code


def _notify(application, message):
    try:
        application.dialog.notification(HEADING, message, time=4000, sound=False)
    except Exception:  # A notice must never break a party.
        pass


# ------------------------------------------------------------------ the open party

def current(application):
    """The Watch Party open on this device, or None."""
    session = getattr(application, "party_session", None)
    if session is None or getattr(session, "ended", None) is not None:
        return None
    return session


def leave_open_party(application):
    """Leave a party left open in the background (switching profile or server, or exiting)."""
    session = getattr(application, "party_session", None)
    application.party_session = None
    if session is not None:
        session.away = False
        session.leave()


def _recent_key(application):
    profile = getattr(application, "profile", None)
    profile_id = profile.get("id") if isinstance(profile, dict) else None
    server = getattr(getattr(application, "client", None), "server_url", "")
    return "{}|{}".format(server if isinstance(server, str) else "", profile_id or "")


def recent_party(application):
    """The last party this profile was in on this server: {"code", "title"}, or None."""
    try:
        saved = application.store.get(RECENT_KEY)
    except Exception:  # Rejoin is a convenience; without the store there is none.
        return None
    if not isinstance(saved, dict) or saved.get("key") != _recent_key(application) \
            or svc_party.normalize_code(saved.get("code")) is None:
        return None
    return saved


def _remember(application, session):
    room = session.snapshot()
    code = svc_party.normalize_code(room.get("code"))
    if code is None:
        return
    try:
        application.store.set(RECENT_KEY, {"key": _recent_key(application), "code": code,
                                           "title": getattr(session, "title", None) or ""})
    except Exception:  # Rejoin is a convenience.
        pass


def _forget(application):
    try:
        application.store.delete(RECENT_KEY)
    except Exception:  # Rejoin is a convenience.
        pass


# ------------------------------------------------------------------ entry points

def open_party(application):
    """The profile menu's Watch Party: return to the open party, or the hub (join with a
    code or invite link, host an empty party, or rejoin the last one)."""
    session = current(application)
    if session is not None:
        enter(application, session)
        return
    support = svc_party.support(application.client)
    if not support["guest"]:
        application.dialog.ok(HEADING, UNSUPPORTED)
        return
    choices = [("join", "Join with a code or invite link")]
    if support.get("host"):
        choices.append(("host_pick", "Start a party"))
    if support.get("vote"):
        choices.append(("vote", "Start a party and let everyone vote"))
    recent = recent_party(application)
    if recent:
        title = recent.get("title")
        choices.append(("rejoin", "Rejoin {}".format(title) if title else "Rejoin your last party"))
    if len(choices) == 1:
        key = "join"
    else:
        chosen = application.dialog.select(HEADING, [label for _key, label in choices])
        if not isinstance(chosen, int) or not 0 <= chosen < len(choices):
            return
        key = choices[chosen][0]
    if key == "join":
        join_typed(application)
    elif key == "rejoin":
        rejoin(application, recent["code"])
    else:
        host_empty(application, key)


def join_typed(application):
    typed = application.dialog.input(JOIN_FIELD, type=xbmcgui.INPUT_ALPHANUM)
    if not (typed or "").strip():
        return
    invite = svc_party.parse_invite(typed)
    if invite is None:
        application.dialog.ok(HEADING, BAD_CODE)
        return
    if invite[0] == "link" and not svc_party.same_server(invite[1], application.client.server_url):
        application.dialog.ok(HEADING, OTHER_SERVER)  # A join token never goes to another server.
        return
    try:
        if invite[0] == "code":
            room, token = svc_party.join(application.client, invite[1])
        else:
            room, token = svc_party.join(application.client, join_token=invite[2])
    except ApiError as error:
        if error.status == 404:
            application.dialog.ok(HEADING, NO_PARTY)
            return
        if error.status == 409:
            application.dialog.ok(HEADING, ENDINGS["ended"])
            return
        raise
    run_party(application, room, token)


def rejoin(application, code):
    """Join the same party again with its code (Android's Rejoin)."""
    try:
        room, token = svc_party.join(application.client, code)
    except ApiError as error:
        if error.status in (404, 409):
            _forget(application)
            application.dialog.ok(HEADING, REJOIN_FAILED)
            return
        raise
    run_party(application, room, token)


def host_party(application, item, file_id=None):
    """Host a Watch Party with ``item`` (a movie or an episode) waiting in its lobby."""
    if not svc_party.support(application.client)["host"]:
        application.dialog.ok(HEADING, NO_HOSTING)
        return
    if not isinstance(item, dict) or item.get("type") not in ("movie", "episode") or not item.get("content_id"):
        application.dialog.ok(HEADING, NOT_PLAYABLE)
        return
    room_id = party.new_room_id()
    room, token = svc_party.create(application.client, room_id)
    staged, staged_token = svc_party.stage(application.client, room_id, item["content_id"], file_id=file_id)
    run_party(application, staged or room, staged_token or token)


def host_empty(application, mode="host_pick"):
    """Host a party with nothing chosen yet: the host picks in the lobby, or everyone votes."""
    room, token = svc_party.create(application.client, party.new_room_id(), mode)
    run_party(application, room, token)


# ------------------------------------------------------------------ the room

def run_party(application, room, token, session_factory=None):
    """Connect to the room and stay in it (see ``enter``)."""
    factory = session_factory or (lambda client, room, token, **kwargs: party.RoomSession(client, room, token,
                                                                                         **kwargs))
    session = factory(application.client, room, token, log=_log)
    session.support = svc_party.support(application.client)
    session.declined, session.title, session.away = set(), None, False
    start = getattr(session, "start", None)
    if callable(start):
        start()
    enter(application, session)


def enter(application, session):
    """Stay in the room until it ends, the viewer leaves, or steps away (Back in the lobby):
    the lobby while it waits, the player while it plays. A title the viewer stopped is
    offered again in the lobby."""
    application.party_session = session
    session.away = False
    stepped_away = False
    try:
        while session.ended is None:
            snapshot = session.snapshot()
            if snapshot.get("phase") == "playing" and snapshot.get("selection_revision") not in session.declined:
                if play_room(application, session, session.declined) == "left":
                    break
                continue
            result = lobby(application, session, session.declined)
            if result == "away" and session.ended is None:
                stepped_away = True
                break
            if result != "play":
                break
    finally:
        if not stepped_away:
            ending = session.ended or "left"  # None: the viewer chose to leave.
            session.leave()
            if application.party_session is session:
                application.party_session = None
            _finish(application, session, ending)
    if stepped_away:
        keep_open(application, session)


def _finish(application, session, ending):
    if ending in REJOINABLE:
        _remember(application, session)
    else:
        _forget(application)
    if ending in ("replaced", "lost"):
        code = svc_party.normalize_code(session.snapshot().get("code"))
        if code and application.dialog.yesno(HEADING, ENDINGS[ending], nolabel="Close", yeslabel="Rejoin"):
            rejoin(application, code)
        return
    if ending not in (None, "left"):
        application.dialog.ok(HEADING, ENDINGS.get(ending, ENDINGS["ended"]))


def keep_open(application, session, watch=True):
    """The viewer stepped away: the party stays connected and says when it starts or ends."""
    token = object()  # One watcher per time away: returning and stepping away again starts another.
    session.away = token
    _notify(application, STEPPED_AWAY)
    if watch:
        threading.Thread(target=watch_away, args=(application, session, token), name="silo-party-away",
                         daemon=True).start()


def watch_away(application, session, token=None):
    token = session.away if token is None else token
    told = None
    while token and session.away is token and application.party_session is session:
        if session.ended is not None:
            application.party_session = None
            session.away = False
            ending = session.ended
            session.leave()
            if ending in REJOINABLE:
                _remember(application, session)
            else:
                _forget(application)
            _notify(application, ENDINGS.get(ending, ENDINGS["ended"]))
            return
        room = session.snapshot()
        revision = room.get("selection_revision")
        if room.get("phase") == "playing" and revision not in session.declined and revision != told:
            told = revision
            _notify(application, STARTING_AWAY)
        session.wait(1.0)


def play_room(application, session, declined):
    """Play the room's file from the room's position; why playback ended, or None when the
    viewer stopped it (or it failed), which the lobby then offers again."""
    room = session.snapshot()
    revision = room.get("selection_revision")
    content_id, file_id = room.get("selected_content_id"), room.get("selected_file_id")
    item = application.guard(lambda: application.client.item(content_id)) if content_id else None
    if not isinstance(item, dict) or file_id in (None, ""):
        declined.add(revision)
        return None
    session.title = present.card_title(item) if item.get("title") else getattr(session, "title", None)
    binding = RoomPlayback(session)
    left = []

    def menu():
        choice = party_menu(application, session)
        if choice == "leave":
            left.append(True)
        return choice

    position = room.get("anchor_position_seconds")
    position = float(position) if isinstance(position, (int, float)) and not isinstance(position, bool) else 0.0
    application.play(item, resume=False, file_id=str(file_id), start_at=position, auto_next=False,
                     party=binding, party_menu=menu)
    if left:
        return "left"
    if binding.stopped is None:
        declined.add(revision)
    return binding.stopped


def party_menu(application, session):
    """What Back opens during the party: "leave", or "watch" to go back to the video."""
    host = session.is_host()
    choices = [("watch", "Back to the video")]
    if host:
        choices += [("lobby", "Return everyone to the lobby"), ("end", "End the Watch Party")]
    else:
        choices.append(("leave", "Leave the Watch Party"))
    chosen = application.dialog.select(HEADING, [label for _key, label in choices])
    if not isinstance(chosen, int) or not 0 <= chosen < len(choices):
        return "watch"
    key = choices[chosen][0]
    if key == "lobby":
        application.guard(lambda: session.accept_http(*svc_party.stop(application.client, session.room_id)))
        return "watch"  # Everyone's playback stops when the room returns to the lobby.
    if key == "end":
        if application.dialog.yesno(HEADING, END_CONFIRM):
            application.guard(lambda: svc_party.close(application.client, session.room_id))
        return "watch"
    return key


# ------------------------------------------------------------------ choosing titles

def party_choice(item, subtitle=None):
    """A movie or episode card as what the room needs: {"content_id", "content_type",
    "title", "subtitle"}; None for anything else."""
    if not isinstance(item, dict) or item.get("type") not in svc_party.SUGGESTION_TYPES or not item.get("content_id"):
        return None
    if subtitle is None:
        if item.get("type") == "episode":
            subtitle = " · ".join(part for part in (str(item.get("series_title") or ""),
                                                    present.episode_code(item, "")) if part)
        else:
            year = item.get("year")
            subtitle = str(year) if isinstance(year, int) and not isinstance(year, bool) and year > 0 else ""
    return {"content_id": str(item["content_id"]), "content_type": item["type"],
            "title": str(item.get("title") or item["content_id"]), "subtitle": subtitle}


def _picker_entries(entries, shelf):
    picked = []
    for entry in entries or ():
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue
        next_up = entry.get("next_up") if isinstance(entry.get("next_up"), dict) else None
        if item.get("type") == "series" and next_up and next_up.get("content_id"):
            season, episode = next_up.get("season_number"), next_up.get("episode_number")
            title = str(next_up.get("title") or "Episode {}".format(episode))
            subtitle = "{} · S{}E{}".format(item.get("title") or "", season, episode)
            choice = {"content_id": str(next_up["content_id"]), "content_type": "episode", "title": title,
                      "subtitle": subtitle}
            picked.append(("{}: {}".format(item.get("title") or "", title), shelf, choice))
        elif item.get("type") == "series":
            picked.append((str(item.get("title") or ""), shelf, {"open": item}))
        else:
            choice = party_choice(item)
            if choice:
                label = choice["title"] + (" ({})".format(choice["subtitle"]) if choice["subtitle"] else "")
                picked.append((label, shelf, choice))
    return picked


def pick_title(application, session, heading):
    """A title for the party: one the room's members share (Continue Together, their
    watchlists) or one found by searching. A series without a next episode opens its page,
    where More offers the party entries; that returns None."""
    rows = []
    if (getattr(session, "support", None) or {}).get("picker"):
        try:
            shared = svc_party.picker(application.client, session.room_id, session.token)
        except (ApiError, OSError, ValueError):
            shared = {}
        rows = (_picker_entries(shared.get("continue_together"), "Continue Together")
                + _picker_entries(shared.get("watchlist_union"), "On a watchlist"))
        seen, unique = set(), []
        for label, shelf, choice in rows:
            key = choice.get("content_id") or (choice.get("open") or {}).get("content_id")
            if key not in seen:
                seen.add(key)
                unique.append((label, shelf, choice))
        rows = unique
    entries = [xbmcgui.ListItem(label="Search for a title…")]
    entries += [xbmcgui.ListItem(label=label, label2=shelf) for label, shelf, _choice in rows]
    chosen = application.dialog.select(heading, entries, useDetails=True)
    if not isinstance(chosen, int) or chosen < 0 or chosen > len(rows):
        return None
    if chosen == 0:
        return search_title(application, heading)
    return _resolve(application, rows[chosen - 1][2])


def search_title(application, heading):
    typed = application.dialog.input("Search for a title", type=xbmcgui.INPUT_ALPHANUM)
    if not (typed or "").strip():
        return None
    response = application.client.search(typed.strip(), limit=40)
    items = [item for item in (response or {}).get("items") or () if isinstance(item, dict)
             and item.get("type") in ("movie", "series", "episode") and item.get("content_id")]
    if not items:
        application.dialog.ok(heading, "No titles match “{}”.".format(typed.strip()))
        return None
    labels = []
    for item in items:
        year = item.get("year")
        labels.append(present.card_title(item) + (" ({})".format(year) if isinstance(year, int)
                                                  and not isinstance(year, bool) and year > 0 else ""))
    chosen = application.dialog.select(heading, labels)
    if not isinstance(chosen, int) or not 0 <= chosen < len(items):
        return None
    item = items[chosen]
    return _resolve(application, {"open": item} if item.get("type") == "series" else party_choice(item))


def _resolve(application, choice):
    if choice and choice.get("open"):
        application.open_item(choice["open"])  # Its page's More offers the party entries.
        return None
    return choice


def suggest_choice(application, session, choice):
    """Suggest a chosen title; True once the room has it."""
    svc_party.suggest(application.client, session.room_id, session.token, choice["content_id"],
                      choice["content_type"], choice["title"], subtitle=choice.get("subtitle") or "")
    _notify(application, "Suggested {}.".format(choice["title"]))
    return True


def stage_choice(application, session, choice, file_id=None):
    """Host of a host_pick lobby: make the chosen title the party's next title."""
    session.accept_http(*svc_party.stage(application.client, session.room_id, choice["content_id"], file_id=file_id))
    session.title = choice["title"]
    return True


# ------------------------------------------------------------------ title page entries

def title_entries(application, item, file_id=None, support=None):
    """The title page's Watch Party entries for ``item`` (a movie or an episode), as
    Android's: Watch Party (host one) when no party is open; while one is, Add to party
    (host of a host_pick lobby), Play for everyone (host of a host_pick party that plays)
    or Suggest to Party (anyone else). ``support`` is svc_party.support's answer."""
    choice = party_choice(item)
    if choice is None:
        return []
    session = current(application)
    if session is None:
        if (support or {}).get("host"):
            return [("Watch Party", lambda: application.open_screen("host_party", item=item, file_id=file_id))]
        return []
    room = session.snapshot()
    host, phase, mode = room.get("self_role") == "host", room.get("phase"), room.get("selection_mode")
    if phase not in ("lobby", "playing") or mode not in svc_party.SELECTION_MODES:
        return []
    if host and mode == "host_pick" and phase == "lobby":
        return [("Add to party", lambda: _from_title(application, session, lambda: stage_choice(
            application, session, choice, file_id=file_id), "Added {} to the Watch Party.".format(choice["title"])))]
    if host and mode == "host_pick":
        return [("Play for everyone", lambda: play_for_everyone(application, session, choice, file_id))]
    return [("Suggest to Party", lambda: _from_title(application, session, lambda: suggest_choice(
        application, session, choice), None))]


def _from_title(application, session, action, notice):
    if not application.guard(action):
        return
    if notice:
        _notify(application, notice)
    if getattr(session, "away", False) and current(application) is session:
        enter(application, session)  # Back to the party the viewer stepped away from.


def play_for_everyone(application, session, choice, file_id=None):
    if not application.dialog.yesno(HEADING, "Play {} for everyone now?".format(choice["title"])):
        return
    answer = application.guard(lambda: svc_party.select(application.client, session.room_id, choice["content_id"],
                                                        file_id=file_id))
    if answer:
        session.accept_http(*answer)
        session.title = choice["title"]
        if getattr(session, "away", False) and current(application) is session:
            enter(application, session)


# ------------------------------------------------------------------ the lobby

def lobby(application, session, declined):
    """The lobby until the party plays ("play"), the viewer steps away ("away"), leaves
    ("leave") or it ends."""
    window = LobbyWindow("SiloParty.xml", application.addon.getAddonInfo("path"), "Default", "1080i")
    window.application, window.session, window.declined = application, session, declined
    window.support = getattr(session, "support", None) or window.support
    window.doModal()
    result = window.result
    del window
    return result


class LobbyWindow(xbmcgui.WindowXML):
    """The title (or the vote), the join code, who is here, and the buttons: Ready (guests)
    or Start (host), Suggest or Choose a title, the selection mode, Invite friends, who may
    play and pause (host), and Leave or End. Back steps away and keeps the party."""

    def __init__(self, *args, **kwargs):
        self.application = None
        self.session = None
        self.declined = set()
        self.support = {"lobby_ready": True}
        self.result = None
        self.closing = False
        self.nested = False      # A picker or title page is open over the lobby.
        self.titles = {}
        self.shown_members = None
        self.shown_suggestions = None
        self.suggestions = []
        self.lock = threading.Lock()

    def onInit(self):
        self.update()
        self.setFocusId(PRIMARY if self.getProperty("silo.primary_label") else
                        SUGGEST if self.getProperty("silo.suggest_label") else LEAVE)
        threading.Thread(target=self.follow, name="silo-party-lobby", daemon=True).start()

    def getProperty(self, key):
        try:
            return super().getProperty(key)
        except (AttributeError, RuntimeError):
            return getattr(self, "properties", {}).get(key, "")

    def follow(self):
        while not self.closing:
            self.session.wait(0.5)
            if self.closing:
                break
            try:
                self.update()
            except Exception:  # The lobby keeps going; the next change redraws it.
                _log("lobby_update_failed")

    def finish(self, result):
        if self.closing:
            return
        self.closing = True
        self.result = result
        self.close()

    def title(self, content_id):
        if not content_id:
            return None
        if content_id not in self.titles:
            try:
                item = self.application.client.item(content_id)
            except Exception:  # The lobby works without the title's details.
                item = None
            self.titles[content_id] = item if isinstance(item, dict) else {"content_id": content_id}
        return self.titles[content_id]

    def update(self):
        with self.lock:
            self._update()

    def _update(self):
        session = self.session
        if session.ended is not None:
            if not self.nested:
                self.finish("ended")
            return
        room = session.snapshot()
        host = room.get("self_role") == "host"
        phase = room.get("phase")
        voting = room.get("selection_mode") == "vote"
        if phase == "playing" and room.get("selection_revision") not in self.declined:
            if self.nested:
                return  # The picker or title page closes first; then the party plays.
            self.finish("play")
            return
        members = [member for member in room.get("members") or () if isinstance(member, dict)]
        me = _self(room)
        suggestions = session.suggestion_list() if voting and hasattr(session, "suggestion_list") else []
        self.suggestions = suggestions
        item = None if voting else self.title(room.get("selected_content_id"))
        if item and item.get("title"):
            session.title = present.card_title(item)
        title, meta = self.heading(room, item, host, voting)
        lobby_open = phase == "lobby"
        if voting:
            primary = ("Start {}".format(suggestions[0]["title"]) if host and suggestions and lobby_open else
                       "Watch now" if phase == "playing" else
                       ("Not ready" if me.get("lobby_ready") else "I'm ready")
                       if not host and self.support.get("lobby_ready") else "")
        elif phase == "playing":
            primary = "Watch now"
        elif host:
            primary = "Start the Watch Party" if room.get("selected_content_id") else ""
        elif self.support.get("lobby_ready"):
            primary = "Not ready" if me.get("lobby_ready") else "I'm ready"
        else:
            primary = ""
        if lobby_open and voting:
            suggest = "Suggest a title"
        elif lobby_open and host:
            suggest = "Change the title" if room.get("selected_content_id") else "Choose a title"
        else:
            suggest = ""
        mode = ""
        if host and lobby_open and self.support.get("mode_switch"):
            mode = "Pick the title yourself" if voting else "Let everyone vote" if self.support.get("vote") else ""
        policy = room.get("guest_control_policy") if room.get("guest_control_policy") in POLICY_LABELS else "host_only"
        set_properties(self, {
            "silo.title": title,
            "silo.meta": meta,
            "silo.backdrop": self.application.image(item, "backdrop") if item and item.get("title") else "",
            "silo.code": format_code(room.get("code")),
            "silo.status": self.status(room, members, host, voting, suggestions),
            "silo.primary_label": primary,
            "silo.suggest_label": suggest,
            "silo.mode_label": mode,
            "silo.invite_label": "Invite friends" if host and room.get("code") else "",
            "silo.policy_label": POLICY_LABELS[policy] if host and phase != "playing" else "",
            "silo.leave_label": "End the Watch Party" if host else "Leave the Watch Party",
            "silo.voting": "true" if voting else "",
            "silo.members_heading": "In the party ({})".format(len(members)),
            "silo.member_line": self.member_line(members) if voting else "",
            "silo.suggestions_heading": "Suggestions ({})".format(len(suggestions)) if voting else "",
        })
        self.show_members(members, phase)
        if voting:
            self.show_suggestions(suggestions)

    def heading(self, room, item, host, voting):
        if voting:
            return ("What are we watching?", "Everyone votes. You start the leader." if host else
                    "Choose a title to vote. {} starts the winner.".format(_host_name(room)))
        if item and item.get("title"):
            return present.card_title(item), self.meta(item)
        if room.get("selected_content_id"):
            return "Loading title…", ""
        if host:
            return "Pick something to watch", "Friends can join now with the code. They'll see your pick the moment you choose."
        return ("Waiting for {}".format(_host_name(room)),
                "{} is choosing what to watch. You'll see it here the moment they do.".format(_host_name(room)))

    def status(self, room, members, host, voting, suggestions):
        if not self.session.connected:
            return "Connecting…"
        if room.get("phase") == "playing":
            return "The party is watching now"
        if voting:
            if not suggestions:
                return "Suggest a title to get the vote going."
            leader = suggestions[0]
            if not leader["vote_count"]:
                return "Nobody has voted yet."
            return "{} leads with {}.".format(leader["title"], self.votes(leader["vote_count"]))
        if host:
            guests = [member for member in members if not member.get("is_host")]
            ready = sum(1 for member in guests if member.get("lobby_ready"))
            return "Share the code to invite people" if not guests else "{} of {} ready".format(ready, len(guests))
        return "Waiting for the host to start"

    @staticmethod
    def votes(count):
        return "1 vote" if count == 1 else "{} votes".format(count)

    @staticmethod
    def meta(item):
        if not item or not item.get("title"):
            return ""
        if item.get("type") == "episode":
            return " · ".join(part for part in (present.episode_code(item, ":"), str(item.get("title") or "")) if part)
        return present.detail_meta(item)

    @staticmethod
    def member_line(members):
        names = []
        for member in members:
            notes = [note for note, on in (("host", member.get("is_host")), ("you", member.get("is_self")),
                                           ("ready", member.get("lobby_ready"))) if on]
            names.append(str(member.get("display_name") or "Guest") + (" ({})".format(", ".join(notes)) if notes else ""))
        return "In the party: " + ", ".join(names) if names else ""

    def show_members(self, members, phase):
        rows = []
        for member in members:
            name = str(member.get("display_name") or "Guest") + (" (you)" if member.get("is_self") else "")
            if member.get("is_host"):
                state = "Host"
            elif phase == "playing":
                state = ("Buffering" if member.get("is_buffering") else "Catching up" if member.get("is_syncing")
                         else "Ready" if member.get("is_ready") else "")
            else:
                state = "Ready" if member.get("lobby_ready") else ""
            rows.append((name, state))
        if rows == self.shown_members:
            return
        self.shown_members = rows
        control = self.getControl(MEMBERS)
        control.reset()
        control.addItems([xbmcgui.ListItem(label=name, label2=state) for name, state in rows])

    def show_suggestions(self, suggestions):
        rows = []
        for entry in suggestions:
            title = str(entry.get("title") or "")
            subtitle = str(entry.get("subtitle") or "")
            votes = self.votes(entry["vote_count"]) + (" · your vote" if entry.get("voted_by_me") else "")
            rows.append((title + (" · " + subtitle if subtitle else ""), votes))
        if rows == self.shown_suggestions:
            return
        self.shown_suggestions = rows
        control = self.getControl(SUGGESTIONS)
        control.reset()
        control.addItems([xbmcgui.ListItem(label=label, label2=votes) for label, votes in rows])

    # ------------------------------------------------------------ input
    def onClick(self, control_id):
        self.application.guard(lambda: self._click(control_id))

    def _click(self, control_id):
        session, client = self.session, self.application.client
        room = session.snapshot()
        host = room.get("self_role") == "host"
        voting = room.get("selection_mode") == "vote"
        if control_id == PRIMARY:
            if room.get("phase") == "playing":
                self.declined.discard(room.get("selection_revision"))
                self.finish("play")
                return
            if host and voting and self.suggestions:
                session.accept_http(*svc_party.promote(client, session.room_id, session.token,
                                                       self.suggestions[0]["id"]))
            elif host and room.get("selected_content_id"):
                session.accept_http(*svc_party.start(client, session.room_id))
            elif not host:
                session.send({"type": "lobby_ready", "ready": not bool(_self(room).get("lobby_ready"))})
        elif control_id == SUGGEST:
            self.choose(voting, host)
        elif control_id == SUGGESTIONS:
            self.suggestion_menu(self.getControl(SUGGESTIONS).getSelectedPosition(), host)
        elif control_id == MODE and host:
            session.accept_http(*svc_party.set_mode(client, session.room_id, "host_pick" if voting else "vote"))
        elif control_id == INVITE and host:
            self.nested = True
            try:
                show_invite(self.application, session)
            finally:
                self.nested = False
        elif control_id == POLICY and host:
            current_policy = room.get("guest_control_policy")
            session.accept_http(*svc_party.set_policy(
                client, session.room_id, "host_only" if current_policy == "guest_play_pause" else "guest_play_pause"))
        elif control_id == LEAVE:
            self.leave()
            return
        self.update()

    def choose(self, voting, host):
        """Suggest a title (a vote) or choose the party's next title (the host picks)."""
        self.nested = True
        try:
            heading = "Suggest a title" if voting else "Choose a title"
            choice = pick_title(self.application, self.session, heading)
            if choice:
                if voting:
                    suggest_choice(self.application, self.session, choice)
                elif host:
                    stage_choice(self.application, self.session, choice)
        finally:
            self.nested = False

    def suggestion_menu(self, position, host):
        if not isinstance(position, int) or not 0 <= position < len(self.suggestions):
            return
        entry, session, client = self.suggestions[position], self.session, self.application.client
        profile = getattr(self.application, "profile", None)
        mine = isinstance(profile, dict) and str(entry.get("suggester_profile_id")) == str(profile.get("id"))
        choices = [("unvote", "Remove your vote") if entry.get("voted_by_me") else ("vote", "Vote for this")]
        if host and session.snapshot().get("phase") == "lobby":
            choices.append(("start", "Start this one"))
        if host or mine:
            choices.append(("remove", "Remove suggestion"))
        chosen = self.application.dialog.select(str(entry.get("title") or HEADING), [label for _key, label in choices])
        if not isinstance(chosen, int) or not 0 <= chosen < len(choices):
            return
        key = choices[chosen][0]
        if key in ("vote", "unvote"):
            svc_party.vote(client, session.room_id, session.token, entry["id"], on=key == "vote")
            session.note_vote(entry["id"], key == "vote")
        elif key == "start":
            session.accept_http(*svc_party.promote(client, session.room_id, session.token, entry["id"]))
        elif key == "remove":
            svc_party.unsuggest(client, session.room_id, session.token, entry["id"])

    def onAction(self, action):
        if action.getId() in BACK_ACTIONS:
            self.finish("away")  # The party stays open; the profile menu returns to it.

    def leave(self):
        host = self.session.is_host()
        if not self.application.dialog.yesno(HEADING, END_CONFIRM if host else LEAVE_CONFIRM):
            return
        if host:
            self.application.guard(lambda: svc_party.close(self.application.client, self.session.room_id))
        self.finish("leave")


# ------------------------------------------------------------------ invitations

def invite_image(application, url):
    """A QR code of ``url`` as a PNG in the add-on's profile folder; its path, or "".
    Each link gets its own file name, so Kodi never shows an older code from its cache."""
    from . import qrcode

    try:
        folder = application._profile_dir()
        name = "party-invite-{}.png".format(hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
        path = os.path.join(folder, name)
        for old in glob.glob(os.path.join(folder, "party-invite-*.png")):
            if old != path:
                os.remove(old)
        if not os.path.exists(path):
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(qrcode.png(qrcode.matrix(url, ecc="M"), scale=10, border=4))
        return path
    except (OSError, ValueError):
        return ""


def show_invite(application, session):
    """The invitation: the code in large type and, for the host, a QR code of the link."""
    room = session.snapshot()
    url = svc_party.invite_url(application.client.server_url, room.get("invite_path"))
    window = InviteWindow("SiloPartyInvite.xml", application.addon.getAddonInfo("path"), "Default", "1080i")
    window.values = {"silo.code": format_code(room.get("code")),
                     "silo.qr": invite_image(application, url) if url else ""}
    window.doModal()
    del window


class InviteWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.values = {}

    def onInit(self):
        set_properties(self, self.values)
        self.setFocusId(INVITE_DONE)

    def onClick(self, control_id):
        if control_id == INVITE_DONE:
            self.close()

    def onAction(self, action):
        if action.getId() in BACK_ACTIONS:
            self.close()
