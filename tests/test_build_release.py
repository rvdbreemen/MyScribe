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


def test_the_dmg_note_leads_with_the_route_macos_26_takes():
    """TASK-107.02: on macOS 26 the refusal has no Open button and a
    right-click > Open is refused too (the outside walk of 0.8.3); the way in
    is Privacy & Security after one attempt. The right-click stays, labelled
    for the older systems it still works on."""
    note = build_release.DMG_NOTE
    assert "Privacy & Security" in note
    assert note.index("Privacy & Security") < note.index("right-click")
    assert "macOS 14 and earlier" in note


# --- TASK-102.01: the version Finder shows ----------------------------------------

import plistlib  # noqa: E402


def _bundle(tmp_path: Path) -> Path:
    """A MyScribe.app as PyInstaller leaves it: version 0.0.0."""
    app = tmp_path / "MyScribe.app"
    (app / "Contents").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleIdentifier": build_release.BUNDLE_ID,
                       "CFBundleShortVersionString": "0.0.0", "CFBundleName": "MyScribe"}, handle)
    return app


def test_the_bundle_carries_the_app_version(tmp_path):
    app = _bundle(tmp_path)

    build_release.stamp_bundle_version(app, "9.8.7", sign=lambda path: None)

    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleShortVersionString"] == "9.8.7"
    assert info["CFBundleVersion"] == "9.8.7"
    assert info["CFBundleIdentifier"] == build_release.BUNDLE_ID, "the other keys stay"


def test_the_stamped_bundle_is_signed_again(tmp_path):
    """Editing Info.plist breaks the ad-hoc signature PyInstaller made, and a
    bundle whose signature does not verify is refused outright on Apple
    Silicon - so the stamp is always followed by a fresh signature."""
    app = _bundle(tmp_path)
    signed = []

    build_release.stamp_bundle_version(app, "9.8.7", sign=signed.append)

    assert signed == [app]


def test_the_ad_hoc_signature_is_deep_forced_and_verified():
    commands = []
    build_release.sign_ad_hoc(Path("MyScribe.app"), run=commands.append)
    assert commands == [["codesign", "--force", "--deep", "--sign", "-", Path("MyScribe.app")],
                        ["codesign", "--verify", "--deep", "--strict", Path("MyScribe.app")]]
