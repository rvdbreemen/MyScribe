"""What packaging/build_release.py puts into the macOS artifact (TASK-102).

Two things the outside macOS walk of 0.8.0 found in the shipped dmg
(docs/reports/2026-10-03-macos-0.8.0-acceptance.md): a note that promised
"about 1 GB" where a Mac measured 2.7 GB plus 1.5 GB of models, and a bundle
whose Info.plist said version 0.0.0 to Finder.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packaging"))
try:
    import build_release
finally:
    sys.path.pop(0)


def test_the_dmg_note_gives_the_sizes_a_mac_measured():
    note = build_release.DMG_NOTE
    assert "about 1 GB" not in note
    assert "2.7 GB" in note and "1.5 GB" in note
    assert "2026-10-03" in note, "a measured number says when it was measured"


def test_the_dmg_note_keeps_its_signing_instructions():
    note = build_release.DMG_NOTE
    assert "Open Anyway" in note and "Applications" in note
