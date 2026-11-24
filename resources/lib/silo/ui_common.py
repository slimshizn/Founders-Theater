"""Kodi helpers shared by the Silo windows: actions, debouncing, card items."""

import threading

import xbmcgui

from . import kodi_settings, present, svc_boxsets

BACK_ACTIONS = frozenset({9, 10, 92, 216, 247, 257, 61448})
ACTION_LEFT, ACTION_RIGHT, ACTION_UP, ACTION_DOWN = 1, 2, 3, 4
ACTION_SELECT = 7
ACTION_MOUSE_MOVE = 107
# Android waits about 150 ms after focus settles before swapping the hero.
SETTLE_SECONDS = 0.15


class Debouncer:
    """Run ``callback`` once, ``delay`` seconds after the last ``trigger``."""

    def __init__(self, callback, delay=SETTLE_SECONDS):
        self.callback = callback
        self.delay = delay
        self._timer = None
        self._lock = threading.Lock()

    def trigger(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        with self._lock:
            self._timer = None
        try:
            self.callback()
        except Exception:  # A late timer must never crash Kodi's script thread.
            pass

    def cancel(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def voice_hint(condition=None, build=None, setting=None):
    """How to start speech in Kodi's keyboard on this device, or "" where it has none.

    Kodi 20+ takes speech in its own keyboard on Android and macOS (xbmc PRs 21330
    and 21384), started by a long press of Enter or Return, Menu, Play/Pause or the
    search key. No add-on API starts it, so the search headings say which key does.
    iOS shows the system keyboard; tvOS, Windows and Linux have no speech.
    """
    try:
        if condition is None or build is None:
            import xbmc

            condition = condition or xbmc.getCondVisibility
            build = xbmc.getInfoLabel("System.BuildVersion") if build is None else build
        if int(str(build).split(".")[0]) < 20:
            return ""
        if condition("System.Platform.Android"):
            # Kodi 23 shows Android's own keyboard unless "Use Kodi virtual keyboard" is on.
            kodi_keyboard = (setting or kodi_settings.get_bool)(kodi_settings.APPLICATION_KEYBOARD)
            return "" if kodi_keyboard is False else "hold OK to speak"
        return "hold Return to speak" if condition("System.Platform.OSX") else ""
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def search_heading(text, note=""):
    """A keyboard heading with its note and, where Kodi takes speech, how to start it."""
    notes = [part for part in (note, voice_hint()) if part]
    return "{} ({})".format(text, "; ".join(notes)) if notes else text


def set_properties(window, values):
    for key, value in values.items():
        window.setProperty(key, "" if value is None else str(value))


def card_list_item(item, image, wide=False):
    """A ListItem for a row or grid card.

    Properties: silo.badge (``S2 · E8`` on wide cards), silo.progress
    (whole percent, "" when none), silo.watched ("true"/""), silo.plot.
    ``ListItem.PercentPlayed`` also reports the progress for progress bars.
    """
    data = present.card(item, image, wide)
    result = xbmcgui.ListItem(label=data["label"], label2=data["label2"])
    result.setArt({key: value for key, value in data["art"].items() if value})
    result.setProperty("silo.badge", data["badge"])
    result.setProperty("silo.progress", str(data["progress"]) if data["progress"] else "")
    result.setProperty("silo.watched", "true" if data["watched"] else "")
    result.setProperty("silo.plot", str(item.get("overview") or ""))
    if data["progress"]:
        try:
            result.getVideoInfoTag().setResumePoint(float(data["progress"]), 100.0)
        except (AttributeError, TypeError):
            pass
    return result


def collection_list_item(collection, image):
    """A ListItem for a collection card: its title over its size, with its poster."""
    result = xbmcgui.ListItem(label=collection.get("title") or "Collection",
                              label2=present.count_text(collection.get("count")))
    poster, backdrop = image(collection, "poster"), image(collection, "backdrop")
    result.setArt({key: value for key, value in {"poster": poster or backdrop, "fanart": backdrop,
                                                 "thumb": backdrop or poster}.items() if value})
    result.setProperty("silo.plot", collection.get("overview") or "")
    return result


def box_set_list_item(found, image):
    """A search result's box set card: its poster under two sleeves, a Box set badge, the film
    count in the corner and the watched share as the progress bar (Android TvCatalogGrid).
    Property silo.box_set ("true") switches the card's skin to that look."""
    result = xbmcgui.ListItem(label=found["title"], label2=svc_boxsets.card_caption(found))
    poster = image(found, "poster")
    if poster:
        result.setArt({"poster": poster, "thumb": poster})
    percent = svc_boxsets.watched_percent(found)
    result.setProperty("silo.box_set", "true")
    result.setProperty("silo.corner", svc_boxsets.film_count(found["movie_count"]))
    result.setProperty("silo.progress", str(percent) if percent else "")
    result.setProperty("silo.watched", "")
    result.setProperty("silo.plot", found["overview"])
    if percent:
        try:
            result.getVideoInfoTag().setResumePoint(float(percent), 100.0)
        except (AttributeError, TypeError):
            pass
    return result
