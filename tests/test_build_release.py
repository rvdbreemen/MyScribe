"""What packaging/build_release.py puts into the macOS artifact (TASK-102).

Two things the outside macOS walk of 0.8.0 found in the shipped dmg
(docs/reports/2026-10-03-macos-0.8.0-acceptance.md): a note that promised
"about 1 GB" where a Mac measured 2.7 GB plus 1.5 GB of models, and a bundle
whose Info.plist said version 0.0.0 to Finder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


# --- TASK-112.11: the tray ships as replaceable source (ADR-038) ------------------


def _frozen_args(tmp_path, monkeypatch, target="windows-x64"):
    commands = []
    monkeypatch.setattr(build_release, "BUILD", tmp_path / "build")
    monkeypatch.setattr(build_release, "run", commands.append)
    monkeypatch.setattr(build_release, "replaceable_tray", lambda frozen, target: frozen)
    monkeypatch.setattr(build_release, "stamp_bundle_version", lambda app, value: None)
    build_release.freeze(Path("pyinstaller"), tmp_path / "payload", target)
    return [str(part) for part in commands[0]]


def test_the_freeze_names_pystray_and_the_hooks_that_keep_it_source(tmp_path, monkeypatch):
    """The launcher imports pystray inside a function, and the hooks beside it
    collect it as loose files; without both flags the frozen build would carry
    no tray, or one compiled into the executable."""
    for target in ("windows-x64", "macos-arm64", "linux-x64"):
        args = _frozen_args(tmp_path, monkeypatch, target)
        hidden = args.index("--hidden-import")
        assert args[hidden + 1] == "pystray"
        hooks = args.index("--additional-hooks-dir")
        assert Path(args[hooks + 1]) == REPO / "packaging" / "launcher" / "hooks"


def test_the_freeze_checks_the_bundle_it_made(tmp_path, monkeypatch):
    monkeypatch.setattr(build_release, "BUILD", tmp_path / "build")
    monkeypatch.setattr(build_release, "run", lambda command: None)
    monkeypatch.setattr(build_release, "stamp_bundle_version", lambda app, value: None)
    for target in ("windows-x64", "macos-arm64", "linux-x64"):
        checked = []
        monkeypatch.setattr(build_release, "replaceable_tray",
                            lambda frozen, target: checked.append((frozen, target)))

        out = build_release.freeze(Path("pyinstaller"), tmp_path / "payload", target)

        assert checked == [(out, target)]


def _bundle_with(tmp_path, *files, inside="MyScribe/_internal"):
    frozen = tmp_path / "frozen"
    for name in files:
        path = frozen / inside / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    frozen.mkdir(exist_ok=True)
    return frozen


def test_a_bundle_with_pystray_as_source_and_its_licence_passes(tmp_path):
    frozen = _bundle_with(tmp_path, "pystray/__init__.py", "pystray-0.19.5.dist-info/COPYING.LGPL")
    assert build_release.replaceable_tray(frozen) == frozen / "MyScribe" / "_internal" / "pystray"


def test_a_bundle_without_pystray_as_source_fails_the_build(tmp_path):
    frozen = _bundle_with(tmp_path, "pystray-0.19.5.dist-info/COPYING.LGPL")
    with pytest.raises(SystemExit, match="loose .py files"):
        build_release.replaceable_tray(frozen)


def test_on_macos_the_app_bundle_itself_must_carry_pystray_as_source(tmp_path):
    """The dmg ships MyScribe.app, not the onedir PyInstaller leaves beside it:
    loose files in that onedir prove nothing about the .app (review of
    TASK-112.11)."""
    files = ("pystray/__init__.py", "pystray-0.19.5.dist-info/COPYING.LGPL")
    frozen = _bundle_with(tmp_path, *files)
    with pytest.raises(SystemExit, match="loose .py files"):
        build_release.replaceable_tray(frozen, "macos-arm64")

    _bundle_with(tmp_path, *files, inside="MyScribe.app/Contents/Resources")
    assert build_release.replaceable_tray(frozen, "macos-arm64") ==         frozen / "MyScribe.app" / "Contents" / "Resources" / "pystray"


def test_pystray_outside_the_shipped_folder_does_not_count(tmp_path):
    """Windows and Linux ship frozen/MyScribe (Inno's MySource, the AppImage's
    usr/bin); a copy anywhere else in the dist folder is not in the product."""
    frozen = _bundle_with(tmp_path, "pystray/__init__.py", "pystray-0.19.5.dist-info/COPYING.LGPL",
                          inside="elsewhere")
    (frozen / "MyScribe").mkdir()
    for target in ("windows-x64", "linux-x64"):
        with pytest.raises(SystemExit, match="loose .py files"):
            build_release.replaceable_tray(frozen, target)


def test_a_bundle_without_pystrays_licence_fails_the_build(tmp_path):
    frozen = _bundle_with(tmp_path, "pystray/__init__.py")
    with pytest.raises(SystemExit, match="COPYING.LGPL"):
        build_release.replaceable_tray(frozen)


def test_the_hooks_collect_the_lgpl_modules_as_source():
    import ast

    for hook, package in (("hook-pystray.py", "pystray"), ("hook-Xlib.py", "python-xlib")):
        tree = ast.parse((REPO / "packaging" / "launcher" / "hooks" / hook).read_text(encoding="utf-8"))
        values = {node.targets[0].id: node.value for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        assert ast.literal_eval(values["module_collection_mode"]) == "py", hook
        assert ast.unparse(values["datas"]) == f"copy_metadata({package!r})", hook


def test_the_build_requirements_pin_the_tray_with_hashes():
    pins = (REPO / "packaging" / "build-requirements.txt").read_text(encoding="utf-8")
    for line in ("pystray==0.19.5 ;", "pillow==13.0.0 ;", "python-xlib==0.33 ; platform_machine == 'x86_64' and sys_platform == 'linux'",
                 "pyobjc-framework-quartz==12.2.2 ; platform_machine == 'arm64' and sys_platform == 'darwin'"):
        assert line in pins
    block = pins.split("pystray==0.19.5", 1)[1].split("\n    # via", 1)[0]
    assert "--hash=sha256:" in block


# --- TASK-108: the myscribe command in the AppImage and the Mac bundle -------------

CLI_SCRIPT = REPO / "packaging" / "posix" / "myscribe"


def _frozen(tmp_path: Path) -> Path:
    frozen = tmp_path / "frozen"
    (frozen / build_release.FROZEN_NAME / "_internal").mkdir(parents=True)
    (frozen / build_release.FROZEN_NAME / build_release.FROZEN_NAME).write_bytes(b"launcher")
    return frozen


def test_the_appdir_ships_the_cli_script_beside_the_launcher(tmp_path):
    appdir = tmp_path / "MyScribe.AppDir"
    build_release.write_appdir(_frozen(tmp_path), appdir)
    script = appdir / "usr" / "bin" / "myscribe-cli"
    assert script.read_bytes() == CLI_SCRIPT.read_bytes()
    assert (appdir / "usr" / "bin" / build_release.FROZEN_NAME).read_bytes() == b"launcher"


def test_apprun_hands_cli_to_the_script_and_everything_else_to_the_launcher(tmp_path):
    appdir = tmp_path / "MyScribe.AppDir"
    build_release.write_appdir(_frozen(tmp_path), appdir)
    apprun = (appdir / "AppRun").read_text(encoding="utf-8")
    assert apprun.startswith("#!/bin/sh\n")
    assert 'if [ "${1-}" = cli ]; then shift; exec "$HERE/usr/bin/myscribe-cli" "$@"; fi' in apprun
    assert apprun.rstrip().endswith('exec "$HERE/usr/bin/MyScribe" "$@"')


def test_the_mac_bundle_gets_the_cli_script_before_it_is_signed(tmp_path):
    """Contents/Resources, not Contents/MacOS: a resource is sealed by the
    bundle's signature, while a script among the executables would have to
    be signed as code itself, and codesign --verify --strict checks that."""
    app = _bundle(tmp_path)
    seen = []

    def sign(path):
        seen.append((app / "Contents" / "Resources" / "myscribe").read_bytes())

    build_release.finish_bundle(app, "9.8.7", sign=sign)
    assert seen == [CLI_SCRIPT.read_bytes()]
