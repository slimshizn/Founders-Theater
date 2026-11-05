"""Small windows drawn over playing video: the skip button and Up next.

Up next doubles as "Are you still watching?": the same card, without a
countdown, whose buttons read Continue and Stop. While it shows over the end
credits it also carries Skip credits, so one card holds all three buttons
instead of the skip button overlapping it. NextTransition covers the screen
while Play next hands over to the following episode.
"""

import xbmc
import xbmcgui

from .ui_common import BACK_ACTIONS

# Transport keys keep reaching the player while an overlay has focus.
ACTION_PAUSE, ACTION_STOP, ACTION_PLAYER_PLAY = 12, 13, 79


def forward_transport(action_id):
    """Send play/pause/stop to the player; return True when handled."""
    try:
        if action_id in (ACTION_PAUSE, ACTION_PLAYER_PLAY):
            xbmc.Player().pause()
        elif action_id == ACTION_STOP:
            xbmc.Player().stop()
        else:
            return False
    except RuntimeError:
        pass
    return True


class _OverlayWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.owner = None

    def onAction(self, action):
        action_id = action.getId()
        if action_id in BACK_ACTIONS:
            self.owner._choose("dismiss")
        else:
            forward_transport(action_id)

    def onClick(self, control_id):
        self.owner._choose(self.owner.choice_for(control_id))


class _Overlay:
    """A modeless dialog; the playback loop polls ``take_choice``."""

    XML = ""

    def __init__(self, addon_path):
        self.addon_path = addon_path
        self.window = None
        self.choice = None

    def choice_for(self, control_id):
        return {100: "skip", 200: "play", 201: "dismiss"}.get(control_id, "dismiss")

    def _choose(self, choice):
        self.choice = choice
        self.hide()

    def _show(self, properties, focus):
        if self.window is None:
            self.window = _OverlayWindow(self.XML, self.addon_path, "Default", "1080i")
            self.window.owner = self
        for key, value in properties.items():
            self.window.setProperty(key, "" if value is None else str(value))
        self.choice = None
        self.window.show()
        try:
            self.window.setFocusId(focus)
        except RuntimeError:
            pass

    def take_choice(self):
        choice, self.choice = self.choice, None
        return choice

    def visible(self):
        return self.window is not None

    def hide(self):
        window, self.window = self.window, None
        if window is not None:
            try:
                window.close()
            except RuntimeError:
                pass


class SkipButton(_Overlay):
    """The white Skip Intro / Skip Credits capsule, bottom right."""

    XML = "SiloSkip.xml"

    def show(self, label):
        self._show({"silo.skip_label": label}, 100)


class UpNextCard(_Overlay):
    """Up next with the following episode and an optional countdown."""

    XML = "SiloUpNext.xml"

    def __init__(self, addon_path):
        super().__init__(addon_path)
        self.still = False

    def choice_for(self, control_id):
        # In the still-watching card the second button stops playback; Back
        # (handled in onAction) only hides the card.
        if control_id == 201 and self.still:
            return "stop"
        if control_id == 202:
            return "skip"
        return super().choice_for(control_id)

    def _choose(self, choice):
        if choice == "skip":
            self.choice = choice  # Skip credits keeps the card up.
            return
        super()._choose(choice)

    def set_skip(self, label):
        """Offer Skip credits in the card (``None`` removes the button)."""
        if self.window is not None:
            self.window.setProperty("silo.next_skip", "" if self.still or not label else str(label))

    def show(self, episode, seconds=None, still=False, skip=None):
        self.still = bool(still)
        self._show({"silo.next_heading": "STILL WATCHING?" if still else "UP NEXT",
                    "silo.next_skip": "" if still or not skip else skip,
                    "silo.next_eyebrow": episode.get("eyebrow", ""),
                    "silo.next_title": episode.get("title", ""),
                    "silo.next_thumb": episode.get("thumb", ""),
                    "silo.next_countdown": "Up next when you continue" if still else self.countdown_text(seconds),
                    "silo.next_play": "Continue" if still else "Play now",
                    "silo.next_hide": "Stop" if still else "Hide"}, 200)

    def update(self, seconds):
        if self.window is not None:
            self.window.setProperty("silo.next_countdown", self.countdown_text(seconds))

    @staticmethod
    def countdown_text(seconds):
        if seconds is None:
            return ""
        return "Playing in {}".format(max(0, int(seconds)))


class NextTransition(_Overlay):
    """A black card while Play next hands over to the next episode.

    It hides the add-on's own screens between the two playbacks. Back
    cancels the hand-over (``cancelled``); every other key is ignored.
    """

    XML = "SiloNextTransition.xml"

    def __init__(self, addon_path):
        super().__init__(addon_path)
        self.cancelled = False

    def choice_for(self, control_id):
        return "ignore"

    def _choose(self, choice):
        if choice == "dismiss":
            self.cancelled = True
            self.hide()

    def show(self, episode):
        self._show({"silo.next_eyebrow": episode.get("eyebrow", ""),
                    "silo.next_title": episode.get("title", "")}, 100)
