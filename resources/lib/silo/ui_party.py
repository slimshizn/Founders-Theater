"""Watch Party screens: join with a code, host a title, the lobby, and the menu Back opens
during a party (Android's Watch Party hub, lobby and the player's party panel).

``party.RoomSession`` keeps the room connected, ``party_sync.RoomPlayback`` keeps the
player on the room's clock, and ``svc_party`` makes the calls. A guest joins with the
host's code, waits in the lobby (and says Ready), follows the host's play, pause and
seek, and leaves. A host starts a party from a title page, starts it for everyone,
decides whether guests may play and pause, returns everyone to the lobby, and ends it.
Voting, suggestions, invitation links and source fallback are not here yet.
"""

import threading

import xbmcgui

from . import party, present, svc_party
from .api import ApiError
from .party_sync import RoomPlayback
from .ui_common import BACK_ACTIONS, set_properties

PRIMARY, LEAVE, POLICY, MEMBERS = 200, 201, 202, 300
HEADING = "Watch Party"
UNSUPPORTED = "Watch Party isn't available for this profile on this Silo server."
NO_HOSTING = "This Silo server lets this profile join a Watch Party but not host one."
NOT_PLAYABLE = "Choose a movie or an episode to watch together."
BAD_CODE = "A Watch Party code has eight letters and numbers, such as ABCD 2345."
NO_PARTY = "No Watch Party has that code."
LEAVE_CONFIRM = "Leave the Watch Party?"
END_CONFIRM = "End the Watch Party for everyone?"
POLICY_LABELS = {"host_only": "Guests watch only", "guest_play_pause": "Guests can play and pause"}
ENDINGS = {
    "host_left": "The host ended the Watch Party.",
    "ended": "This Watch Party has ended.",
    "not_found": "This Watch Party no longer exists.",
    "replaced": "You joined this Watch Party on another device, so it stopped here.",
    "unauthorized": "This profile can no longer join this Watch Party.",
    "lost": "Lost the connection to the Watch Party.",
}


def _log(event):
    try:
        import xbmc

        xbmc.log("[Silo] party event=" + event, xbmc.LOGINFO)
    except Exception:  # Logging must never break a party.
        pass


def _self(room):
    return next((member for member in room.get("members") or () if isinstance(member, dict)
                 and member.get("is_self")), {})


def format_code(code):
    code = str(code or "")
    return "{} {}".format(code[:4], code[4:]) if len(code) == 8 else code


# ------------------------------------------------------------------ entry points

def open_party(application):
    """Join a Watch Party with its code (the avatar menu's Watch Party)."""
    if not svc_party.support(application.client)["guest"]:
        application.dialog.ok(HEADING, UNSUPPORTED)
        return
    typed = application.dialog.input("Watch Party code", type=xbmcgui.INPUT_ALPHANUM)
    if not (typed or "").strip():
        return
    code = svc_party.normalize_code(typed)
    if code is None:
        application.dialog.ok(HEADING, BAD_CODE)
        return
    try:
        room, token = svc_party.join(application.client, code)
    except ApiError as error:
        if error.status == 404:
            application.dialog.ok(HEADING, NO_PARTY)
            return
        if error.status == 409:
            application.dialog.ok(HEADING, ENDINGS["ended"])
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


# ------------------------------------------------------------------ the room

def run_party(application, room, token, session_factory=None):
    """Stay in the room until it ends or the viewer leaves: the lobby while it waits,
    the player while it plays. A title the viewer stopped is offered again in the lobby."""
    factory = session_factory or (lambda client, room, token, **kwargs: party.RoomSession(client, room, token,
                                                                                         **kwargs))
    session = factory(application.client, room, token, log=_log)
    session.support = svc_party.support(application.client)
    start = getattr(session, "start", None)
    if callable(start):
        start()
    declined = set()
    try:
        while session.ended is None:
            snapshot = session.snapshot()
            if snapshot.get("phase") == "playing" and snapshot.get("selection_revision") not in declined:
                if play_room(application, session, declined) == "left":
                    break
                continue
            if lobby(application, session, declined) != "play":
                break
    finally:
        ending = session.ended
        session.leave()
    if ending not in (None, "left"):
        application.dialog.ok(HEADING, ENDINGS.get(ending, ENDINGS["ended"]))


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


def lobby(application, session, declined):
    """The lobby until the party plays ("play"), the viewer leaves ("leave") or it ends."""
    window = LobbyWindow("SiloParty.xml", application.addon.getAddonInfo("path"), "Default", "1080i")
    window.application, window.session, window.declined = application, session, declined
    window.support = getattr(session, "support", None) or window.support
    window.doModal()
    result = window.result
    del window
    return result


class LobbyWindow(xbmcgui.WindowXML):
    """The title, the join code, who is here and what they are waiting for, and the buttons:
    Ready (guests) or Start (host), who may play and pause (host), and Leave or End."""

    def __init__(self, *args, **kwargs):
        self.application = None
        self.session = None
        self.declined = set()
        self.support = {"lobby_ready": True}
        self.result = None
        self.closing = False
        self.titles = {}
        self.shown_members = None
        self.lock = threading.Lock()

    def onInit(self):
        self.update()
        self.setFocusId(PRIMARY if self.getProperty("silo.primary_label") else LEAVE)
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
            self.finish("ended")
            return
        room = session.snapshot()
        host = room.get("self_role") == "host"
        phase = room.get("phase")
        if phase == "playing" and room.get("selection_revision") not in self.declined:
            self.finish("play")
            return
        members = [member for member in room.get("members") or () if isinstance(member, dict)]
        me = _self(room)
        item = self.title(room.get("selected_content_id"))
        if not session.connected:
            status = "Connecting…"
        elif phase == "playing":
            status = "The party is watching now"
        elif host:
            guests = [member for member in members if not member.get("is_host")]
            ready = sum(1 for member in guests if member.get("lobby_ready"))
            status = ("Share the code to invite people" if not guests
                      else "{} of {} ready".format(ready, len(guests)))
        else:
            status = "Waiting for the host to start"
        if phase == "playing":
            primary = "Watch now"
        elif host:
            primary = "Start the Watch Party" if room.get("selected_content_id") else ""
        elif self.support.get("lobby_ready"):
            primary = "Not ready" if me.get("lobby_ready") else "I'm ready"
        else:
            primary = ""
        policy = room.get("guest_control_policy") if room.get("guest_control_policy") in POLICY_LABELS else "host_only"
        set_properties(self, {
            "silo.title": present.card_title(item) if item and item.get("title") else "Waiting for a title",
            "silo.meta": self.meta(item),
            "silo.backdrop": self.application.image(item, "backdrop") if item and item.get("title") else "",
            "silo.code": format_code(room.get("code")),
            "silo.status": status,
            "silo.primary_label": primary,
            "silo.policy_label": POLICY_LABELS[policy] if host and phase != "playing" else "",
            "silo.leave_label": "End the Watch Party" if host else "Leave the Watch Party",
            "silo.members_heading": "In the party ({})".format(len(members)),
        })
        self.show_members(members, phase)

    @staticmethod
    def meta(item):
        if not item or not item.get("title"):
            return ""
        if item.get("type") == "episode":
            return " · ".join(part for part in (present.episode_code(item, ":"), str(item.get("title") or "")) if part)
        return present.detail_meta(item)

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

    # ------------------------------------------------------------ input
    def onClick(self, control_id):
        self.application.guard(lambda: self._click(control_id))

    def _click(self, control_id):
        session, client = self.session, self.application.client
        room = session.snapshot()
        host = room.get("self_role") == "host"
        if control_id == PRIMARY:
            if room.get("phase") == "playing":
                self.declined.discard(room.get("selection_revision"))
                self.finish("play")
                return
            if host and room.get("selected_content_id"):
                session.accept_http(*svc_party.start(client, session.room_id))
            elif not host:
                session.send({"type": "lobby_ready", "ready": not bool(_self(room).get("lobby_ready"))})
        elif control_id == POLICY and host:
            current = room.get("guest_control_policy")
            session.accept_http(*svc_party.set_policy(
                client, session.room_id, "host_only" if current == "guest_play_pause" else "guest_play_pause"))
        elif control_id == LEAVE:
            self.leave()
            return
        self.update()

    def onAction(self, action):
        if action.getId() in BACK_ACTIONS:
            self.application.guard(self.leave)

    def leave(self):
        host = self.session.is_host()
        if not self.application.dialog.yesno(HEADING, END_CONFIRM if host else LEAVE_CONFIRM):
            return
        if host:
            self.application.guard(lambda: svc_party.close(self.application.client, self.session.room_id))
        self.finish("leave")
