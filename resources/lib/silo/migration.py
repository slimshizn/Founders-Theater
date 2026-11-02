"""Bring this device's data over from the add-on's old id, script.silo.

Founders Theater was published as script.silo up to 0.8.3. Kodi cannot rename
an add-on, so script.silo 0.9.0 is a small bridge that requires this add-on
(Kodi installs it with that update) and passes RunScript calls on to it.

On the first run under the new id, the old saved data is copied across once:
the sign-ins, the device id Silo knows this Kodi by, saved servers, subtitles
and the device settings. Nothing is moved or deleted, so the old folder stays
as a fallback. Files already present here are never overwritten, and a device
that signed in under the new id before migrating keeps its own data.
"""

import os
import shutil
import time
import xml.etree.ElementTree as ET

OLD_ID = "script.silo"
DONE_MARKER = ".migrated-from-script.silo"
# Present while a copy is under way; a stale one (a crash) is taken over.
BUSY_MARKER = ".migrating-from-script.silo"
BUSY_SECONDS = 120
SETTINGS_FILE = "settings.xml"


def setting_ids(definition_path):
    """The setting ids this add-on defines in resources/settings.xml."""
    try:
        root = ET.parse(definition_path).getroot()
    except (ET.ParseError, OSError):
        return set()
    return {node.get("id") for node in root.iter("setting") if node.get("id")}


def read_settings(path):
    """Values the user changed in Kodi's saved settings.xml (formats 1 and 2).

    Values Kodi marks as defaults are left out: the new add-on has the same
    defaults already.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    values = {}
    for node in root.iter("setting"):
        setting_id = node.get("id")
        if not setting_id or node.get("default") == "true":
            continue
        values[setting_id] = node.get("value") if "value" in node.attrib else (node.text or "")
    return values


def _has_sign_in(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return "device.json" in names or any(name.startswith("session") and name.endswith(".json") for name in names)


def _write_marker(path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(int(time.time())) + "\n")


def _copy_tree(source, target):
    """Copy files from source into target, keeping any file target already has."""
    for folder, folders, files in os.walk(source):
        folders[:] = [name for name in folders if not os.path.islink(os.path.join(folder, name))]
        relative = os.path.relpath(folder, source)
        destination = target if relative == "." else os.path.join(target, relative)
        os.makedirs(destination, exist_ok=True)
        for name in files:
            if relative == "." and name in (SETTINGS_FILE, DONE_MARKER, BUSY_MARKER):
                continue
            origin = os.path.join(folder, name)
            copy = os.path.join(destination, name)
            if os.path.islink(origin) or os.path.exists(copy):
                continue
            shutil.copy2(origin, copy)


def migrate(old_folder, new_folder, known_settings, set_setting, clock=time.time):
    """Copy the old add-on's data once; return what happened.

    ``done`` copied data, ``already`` ran before, ``nothing`` had nothing to
    copy, ``kept`` found a sign-in under the new id and left it alone,
    ``busy`` found another run copying, ``unavailable`` had no real paths.
    """
    old_folder, new_folder = str(old_folder or ""), str(new_folder or "")
    if not (os.path.isabs(old_folder) and os.path.isabs(new_folder)):
        return "unavailable"
    done = os.path.join(new_folder, DONE_MARKER)
    busy = os.path.join(new_folder, BUSY_MARKER)
    if os.path.exists(done):
        return "already"
    os.makedirs(new_folder, exist_ok=True)
    resuming = os.path.exists(busy)
    if resuming and clock() - os.path.getmtime(busy) < BUSY_SECONDS:
        return "busy"
    if not os.path.isdir(old_folder):
        _write_marker(done)
        return "nothing"
    if not resuming and _has_sign_in(new_folder):
        _write_marker(done)
        return "kept"
    _write_marker(busy)
    _copy_tree(old_folder, new_folder)
    old_settings = read_settings(os.path.join(old_folder, SETTINGS_FILE))
    for setting_id in sorted(set(old_settings) & set(known_settings)):
        set_setting(setting_id, old_settings[setting_id])
    _write_marker(done)
    os.unlink(busy)
    return "done"


def run(xbmc, xbmcvfs, addon, wait_seconds=0):
    """Migrate in Kodi; with ``wait_seconds``, wait for a copy already under way."""
    old = xbmcvfs.translatePath("special://profile/addon_data/{}/".format(OLD_ID))
    new = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    definitions = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo("path")), "resources", "settings.xml")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            result = migrate(old, new, setting_ids(definitions), addon.setSetting)
        except OSError as error:
            # The error type only: messages carry local paths and add nothing here.
            xbmc.log("[Silo] migration from {} failed: {}".format(OLD_ID, type(error).__name__), xbmc.LOGWARNING)
            return "failed"
        if result != "busy" or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if result not in ("already", "unavailable"):
        xbmc.log("[Silo] migration from {}: {}".format(OLD_ID, result), xbmc.LOGINFO)
    return result
