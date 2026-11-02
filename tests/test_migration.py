"""Bringing a device's data over from script.silo (resources/lib/silo/migration.py)."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "resources/lib"))

from silo import keymap, migration  # noqa: E402

SETTINGS_V2 = """<settings version="2">
    <setting id="server_url">https://example.invalid</setting>
    <setting id="auto_next">false</setting>
    <setting id="open_on_start">true</setting>
    <setting id="page_size" default="true">50</setting>
    <setting id="not_a_setting_any_more">x</setting>
</settings>
"""
KNOWN = migration.setting_ids(ROOT / "resources/settings.xml")


def write(folder, name, text):
    path = os.path.join(folder, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def read(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as handle:
        return handle.read()


def files(folder):
    return sorted(os.path.relpath(os.path.join(path, name), folder)
                  for path, _, names in os.walk(folder) for name in names)


class MigrateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.old = os.path.join(temporary.name, "script.silo")
        self.new = os.path.join(temporary.name, "script.founderstheater")
        self.settings = {}
        write(self.old, "device.json", '{"device_id": "kodi-test"}')
        write(self.old, "session-abc.json", '{"access_token": "test"}')
        write(self.old, "servers.json", "[]")
        write(self.old, "subtitles/one.srt", "1")
        write(self.old, "settings.xml", SETTINGS_V2)

    def migrate(self, **kwargs):
        return migration.migrate(self.old, self.new, KNOWN, self.settings.__setitem__, **kwargs)

    def test_copies_sign_in_device_and_subtitles_and_changed_settings(self):
        self.assertEqual(self.migrate(), "done")
        for name in ("device.json", "session-abc.json", "servers.json", "subtitles/one.srt"):
            self.assertEqual(read(self.new, name), read(self.old, name), name)
        # Only settings this add-on defines, and only ones the user changed.
        self.assertEqual(self.settings, {"server_url": "https://example.invalid", "auto_next": "false",
                                         "open_on_start": "true"})
        # Kodi owns the new settings file; values go through setSetting, never a file copy.
        self.assertFalse(os.path.exists(os.path.join(self.new, "settings.xml")))
        self.assertTrue(os.path.exists(os.path.join(self.new, migration.DONE_MARKER)))
        self.assertFalse(os.path.exists(os.path.join(self.new, migration.BUSY_MARKER)))

    def test_leaves_the_old_folder_untouched(self):
        before = files(self.old)
        self.migrate()
        self.assertEqual(files(self.old), before)

    def test_runs_only_once(self):
        self.migrate()
        write(self.old, "servers.json", '["changed later"]')
        self.settings.clear()
        self.assertEqual(self.migrate(), "already")
        self.assertEqual(read(self.new, "servers.json"), "[]")
        self.assertEqual(self.settings, {})

    def test_a_sign_in_under_the_new_id_is_kept(self):
        write(self.new, "device.json", '{"device_id": "kodi-new"}')
        self.assertEqual(self.migrate(), "kept")
        self.assertEqual(read(self.new, "device.json"), '{"device_id": "kodi-new"}')
        self.assertFalse(os.path.exists(os.path.join(self.new, "session-abc.json")))
        self.assertEqual(self.settings, {})

    def test_never_overwrites_a_file_already_here(self):
        write(self.new, "subtitles/one.srt", "mine")
        self.assertEqual(self.migrate(), "done")
        self.assertEqual(read(self.new, "subtitles/one.srt"), "mine")

    def test_fresh_install_has_nothing_to_copy(self):
        missing = self.old + "-missing"
        self.assertEqual(migration.migrate(missing, self.new, KNOWN, self.settings.__setitem__), "nothing")
        self.assertEqual(migration.migrate(missing, self.new, KNOWN, self.settings.__setitem__), "already")

    def test_another_run_copying_is_left_alone(self):
        write(self.new, migration.BUSY_MARKER, "0")
        self.assertEqual(self.migrate(), "busy")

    def test_an_interrupted_copy_is_finished_later(self):
        # A crash left the busy marker and a partial copy (device.json only).
        write(self.new, migration.BUSY_MARKER, "0")
        write(self.new, "device.json", '{"device_id": "kodi-test"}')
        stale = os.path.getmtime(os.path.join(self.new, migration.BUSY_MARKER)) + migration.BUSY_SECONDS + 1
        self.assertEqual(self.migrate(clock=lambda: stale), "done")
        self.assertEqual(read(self.new, "session-abc.json"), '{"access_token": "test"}')
        self.assertEqual(self.settings["server_url"], "https://example.invalid")

    def test_reads_the_older_settings_format(self):
        write(self.old, "settings.xml", '<settings><setting id="server_url" value="https://example.invalid" />'
                                        '<setting id="auto_next" value="false" /></settings>')
        self.migrate()
        self.assertEqual(self.settings, {"server_url": "https://example.invalid", "auto_next": "false"})

    def test_unreadable_settings_still_migrate_the_files(self):
        write(self.old, "settings.xml", "<settings")
        self.assertEqual(self.migrate(), "done")
        self.assertEqual(self.settings, {})
        self.assertTrue(os.path.exists(os.path.join(self.new, "device.json")))

    def test_needs_real_paths(self):
        self.assertEqual(migration.migrate("special://x", self.new, KNOWN, self.settings.__setitem__), "unavailable")


class KodiRunTests(unittest.TestCase):
    def test_run_maps_kodi_paths_and_saves_through_the_addon(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = os.path.join(folder, "old"), os.path.join(folder, "new")
            write(old, "device.json", "{}")
            write(old, "settings.xml", SETTINGS_V2)
            paths = {"special://profile/addon_data/script.silo/": old, "special://profile/addon_data/new/": new,
                     "special://home/addons/new/": str(ROOT)}
            saved, logs = {}, []
            addon = SimpleNamespace(getAddonInfo={"profile": "special://profile/addon_data/new/",
                                                  "path": "special://home/addons/new/"}.get,
                                    setSetting=saved.__setitem__)
            xbmc = SimpleNamespace(log=lambda text, level: logs.append(text), LOGINFO=1, LOGWARNING=2)
            xbmcvfs = SimpleNamespace(translatePath=paths.get)
            self.assertEqual(migration.run(xbmc, xbmcvfs, addon), "done")
            self.assertTrue(os.path.exists(os.path.join(new, "device.json")))
            self.assertEqual(saved["server_url"], "https://example.invalid")
            self.assertEqual(logs, ["[Silo] migration from script.silo: done"])
            self.assertEqual(migration.run(xbmc, xbmcvfs, addon), "already")
            self.assertEqual(len(logs), 1)


class KeymapApplyTests(unittest.TestCase):
    def test_points_an_old_keymap_at_the_new_add_on(self):
        with tempfile.TemporaryDirectory() as folder:
            keymaps = os.path.join(folder, "keymaps")
            self.assertTrue(keymap.sync(keymaps, True, "/addons/script.silo"))
            builtins = []
            xbmc = SimpleNamespace(executebuiltin=builtins.append, log=lambda *args: None, LOGWARNING=2)
            xbmcvfs = SimpleNamespace(translatePath=lambda path: keymaps + "/")
            keymap.apply(xbmc, xbmcvfs, True, "/addons/script.founderstheater")
            self.assertIn("/addons/script.founderstheater/resources/player_menu.py", read(keymaps, keymap.FILE_NAME))
            self.assertEqual(builtins, ["Action(reloadkeymaps)"])


if __name__ == "__main__":
    unittest.main()
