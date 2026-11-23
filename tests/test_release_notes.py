"""A release's GitHub notes come from addon.xml's news entry for exactly that version."""

from pathlib import Path
import tempfile
import unittest

from scripts.release_notes import release_notes


ROOT = Path(__file__).resolve().parents[1]

MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="script.founderstheater" name="Founders Theater" version="{version}" provider-name="slimshizn">
  <extension point="xbmc.addon.metadata">
    <news>{news}</news>
  </extension>
</addon>
"""


class ReleaseNotesTests(unittest.TestCase):
    def notes(self, tag, version="2.1.0", news="v2.1.0 (2027-01-02)\n- One change.\n- Another change."):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "addon.xml").write_text(MANIFEST.format(version=version, news=news), encoding="utf-8")
            return release_notes(Path(folder), tag)

    def test_the_news_entry_becomes_the_release_notes(self):
        self.assertEqual(self.notes("v2.1.0"), "- One change.\n- Another change.\n")

    def test_a_tag_for_another_version_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.notes("v2.0.9")

    def test_news_left_over_from_the_previous_version_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no news entry for 2.1.0"):
            self.notes("v2.1.0", news="v2.0.9 (2026-12-01)\n- An older change.")

    def test_news_needs_a_line_per_change(self):
        with self.assertRaisesRegex(ValueError, "one '- ' line per change"):
            self.notes("v2.1.0", news="v2.1.0\nSome prose instead of a list.")
        with self.assertRaisesRegex(ValueError, "no news entry"):
            self.notes("v2.1.0", news="")

    def test_this_release_has_its_news_entry(self):
        """A version bump without its news entry fails here, before the tag is pushed."""
        import xml.etree.ElementTree as ET

        version = ET.parse(ROOT / "addon.xml").getroot().get("version")
        notes = release_notes(ROOT, "v" + version)
        self.assertTrue(notes.startswith("- "))


if __name__ == "__main__":
    unittest.main()
