#!/usr/bin/env python3
"""Print a version tag's GitHub release notes: addon.xml's news entry for that version.

Run: python3 scripts/release_notes.py v1.0.0 > notes.md

It fails unless the tag names addon.xml's version and the news entry is for that
version, so a release can't go out with another version's notes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def release_notes(root: Path, tag: str) -> str:
    """The news entry's change lines ("- ...") for ``tag``, as Markdown."""
    manifest = ET.parse(Path(root) / "addon.xml").getroot()
    version = manifest.get("version", "")
    if tag != "v" + version:
        raise ValueError("The tag {} does not match addon.xml's version {}".format(tag, version))
    news = (manifest.findtext("extension[@point='xbmc.addon.metadata']/news") or "").strip()
    lines = news.splitlines()
    heading = lines[0].split() if lines else []
    if not heading or heading[0] != tag:
        raise ValueError("addon.xml has no news entry for {}".format(version))
    changes = [line.strip() for line in lines[1:] if line.strip()]
    if not changes or not all(line.startswith("- ") for line in changes):
        raise ValueError("The news entry for {} needs one '- ' line per change".format(version))
    return "\n".join(changes) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="the version tag, such as v1.0.0")
    args = parser.parse_args()
    try:
        sys.stdout.write(release_notes(ROOT, args.tag))
    except (ValueError, OSError, ET.ParseError) as error:
        print("release_notes: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
