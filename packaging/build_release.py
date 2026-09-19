"""Build one platform's MyScribe artifact, end to end (ADR-011).

    python packaging/build_release.py --platform macos-arm64 [--ffmpeg-dir DIR]

Four steps, all from a clean checkout:

1. the payload (`build_payload.py`): the app source, `uv.lock`, uv and
   ffmpeg, each download checked against its pinned sha256;
2. a build environment: the payload's own verified uv makes a venv and
   installs PyInstaller from `build-requirements.txt` with `--require-hashes`;
3. the frozen launcher: one windowed onedir build, the payload inside it;
4. the native artifact: an Inno Setup installer (Windows), a dmg (macOS), an
   AppImage (Linux).

Everything lands in `dist/`. Stdlib only, so CI runs it with any Python.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_payload  # noqa: E402  - the same directory, by design

REPO = build_payload.REPO
TOOLS = build_payload.TOOLS
DIST = REPO / "dist"
BUILD = REPO / "build"
BUNDLE_ID = "io.github.rvdbreemen.myscribe"

FROZEN_NAME = "MyScribe"
"""What PyInstaller calls the executable, and therefore what the smoke test
has to look for inside the bundle. One spelling, because a rename that moved
only one of them would produce an artifact that builds and cannot be run."""

NATIVE = {"windows-x64": "win32", "macos-arm64": "darwin", "linux-x64": "linux"}


def run(command: list[str], **kwargs) -> None:
    printable = " ".join(str(c) for c in command)
    print(f"$ {printable}", flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


def version() -> str:
    text = (REPO / "scribe" / "__init__.py").read_text(encoding="utf-8")
    return text.split('__version__ = "', 1)[1].split('"', 1)[0]


def build_environment(payload: Path) -> Path:
    """A venv with PyInstaller, made by the uv we just verified."""
    uv = payload / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    venv = BUILD / "venv-launcher"
    if venv.exists():
        shutil.rmtree(venv)
    env = {**os.environ, "UV_CACHE_DIR": str(BUILD / "uv-cache"), "UV_NO_CONFIG": "1"}
    run([uv, "venv", venv, "--python", "3.12", "--python-preference", "only-managed"], env=env)
    run([uv, "pip", "install", "--python", venv, "--require-hashes",
         "-r", REPO / "packaging" / "build-requirements.txt"], env=env)
    return venv / ("Scripts" if sys.platform == "win32" else "bin") / (
        "pyinstaller.exe" if sys.platform == "win32" else "pyinstaller")


def freeze(pyinstaller: Path, payload: Path, target: str) -> Path:
    """The frozen launcher, with the payload inside it. Returns dist/frozen."""
    out = BUILD / "frozen"
    if out.exists():
        shutil.rmtree(out)
    args = [
        pyinstaller, "--noconfirm", "--clean", "--log-level", "WARN",
        "--name", FROZEN_NAME, "--onedir", "--windowed",
        "--distpath", out, "--workpath", BUILD / "pyinstaller", "--specpath", BUILD,
        "--add-data", f"{payload}{os.pathsep}payload",
    ]
    if target == "macos-arm64":
        args += ["--osx-bundle-identifier", BUNDLE_ID, "--target-architecture", "arm64"]
    if target == "windows-x64":
        args += ["--icon", "NONE"]
    args.append(REPO / "packaging" / "launcher" / "myscribe_launcher.py")
    run(args)
    return out


# --- the native artifacts ---------------------------------------------------------


def dmg(frozen: Path, out: Path) -> Path:
    app = frozen / "MyScribe.app"
    staging = BUILD / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    run(["cp", "-R", app, staging / "MyScribe.app"])
    (staging / "Applications").symlink_to("/Applications")
    (staging / "Open me first.txt").write_text(
        "MyScribe is not signed with an Apple certificate yet.\n\n"
        "Drag MyScribe to Applications, then start it once with a right-click\n"
        "> Open, or open System Settings > Privacy & Security and press\n"
        "\"Open Anyway\" after the first attempt.\n\n"
        "The first start downloads the speech engine (about 1 GB on a Mac) and\n"
        "shows its progress. Later starts skip that.\n",
        encoding="utf-8",
    )
    run(["hdiutil", "create", "-volname", f"MyScribe {version()}", "-srcfolder", staging,
         "-ov", "-format", "UDZO", "-quiet", out])
    return out


def inno(frozen: Path, out: Path) -> Path:
    iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    run([iscc, f"/DMyAppVersion={version()}", f"/DMySource={frozen / 'MyScribe'}",
         f"/DMyOutputDir={out.parent}", f"/DMyOutputName={out.stem}",
         REPO / "packaging" / "windows" / "myscribe.iss"])
    return out


def _placeholder_png(path: Path, size: int = 256) -> None:
    """A plain square, written with zlib: an AppImage needs an icon and a real
    one is a design decision, not a build step."""
    pixel = bytes((28, 36, 54))
    row = b"\x00" + pixel * size
    raw = row * size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def appimage(frozen: Path, out: Path) -> Path:
    appdir = BUILD / "MyScribe.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(frozen / "MyScribe", appdir / "usr" / "bin")
    (appdir / "AppRun").write_text(
        '#!/bin/sh\nHERE=$(dirname "$(readlink -f "$0")")\nexec "$HERE/usr/bin/MyScribe" "$@"\n',
        encoding="utf-8",
    )
    (appdir / "AppRun").chmod(0o755)
    (appdir / "myscribe.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=MyScribe\n"
        "Comment=Local transcription with speakers\nExec=MyScribe\nIcon=myscribe\n"
        "Categories=AudioVideo;Audio;\nTerminal=false\n",
        encoding="utf-8",
    )
    _placeholder_png(appdir / "myscribe.png")

    tools = TOOLS["appimage"]
    tool = BUILD / "appimagetool"
    tool.write_bytes(build_payload.fetch(tools["appimagetool"]["url"], tools["appimagetool"]["sha256"]))
    tool.chmod(0o755)
    runtime = BUILD / "appimage-runtime"
    runtime.write_bytes(build_payload.fetch(tools["runtime"]["url"], tools["runtime"]["sha256"]))

    run([tool, "--runtime-file", runtime, appdir, out],
        env={**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1", "ARCH": "x86_64"})
    out.chmod(0o755)
    return out


ARTIFACTS = {
    "windows-x64": ("exe", inno),
    "macos-arm64": ("dmg", dmg),
    "linux-x64": ("AppImage", appimage),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", required=True, choices=sorted(ARTIFACTS))
    parser.add_argument("--ffmpeg-dir", type=Path, help="macOS: output of build_ffmpeg_macos.sh")
    parser.add_argument("--skip-payload", action="store_true", help="reuse build/payload-<platform>")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="after building, run the frozen launcher's own --smoke: first sync, /health, a page, quit",
    )
    args = parser.parse_args(argv)

    if NATIVE[args.platform] != sys.platform:
        raise SystemExit(f"{args.platform} must be built on {NATIVE[args.platform]}, not {sys.platform}")
    if args.platform == "macos-arm64" and platform.machine() != "arm64":
        raise SystemExit("macos-arm64 must be built on Apple Silicon")

    payload = BUILD / f"payload-{args.platform}"
    if not args.skip_payload:
        build_payload.build(args.platform, payload, args.ffmpeg_dir)

    pyinstaller = build_environment(payload)
    frozen = freeze(pyinstaller, payload, args.platform)

    DIST.mkdir(parents=True, exist_ok=True)
    suffix, package = ARTIFACTS[args.platform]
    out = DIST / f"MyScribe-{version()}-{args.platform}.{suffix}"
    out.unlink(missing_ok=True)
    package(frozen, out)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print(json.dumps({"artifact": str(out), "bytes": out.stat().st_size, "sha256": digest}, indent=2))

    if args.smoke:
        # The frozen launcher, not this interpreter: what CI has to prove is
        # that the thing built here starts, serves and stops - "the build
        # produced a file" is not that, and it is the failure a release
        # actually ships. A fresh home so it is a first run, the way a user's
        # is (and so a stale environment cannot make it pass).
        home = BUILD / f"smoke-home-{args.platform}"
        shutil.rmtree(home, ignore_errors=True)
        binary = _frozen_binary(frozen, args.platform)
        print(f"smoke: {binary} --home {home} --smoke")
        code = subprocess.call([str(binary), "--home", str(home), "--smoke"])
        if code != 0:
            print(f"::error::the built artifact failed its smoke test (exit {code})")
            return code
        print("smoke: the artifact started, served and stopped")
    return 0


def _frozen_binary(frozen: Path, platform_name: str) -> Path:
    """The executable inside the frozen onedir, per platform layout.

    PyInstaller's onedir puts everything in a folder named after the app, with
    the executable inside it - which `inno` and `appimage` both already knew
    (each hands on `frozen / "MyScribe"` as a directory). The first version of
    this read that name as the executable, so the smoke test tried to run a
    directory and the Linux job died with "PermissionError: [Errno 13]
    Permission denied" after building a perfectly good 121 MB AppImage.

    macOS is the exception, because `dmg` wraps the onedir in a bundle and the
    executable moves to Contents/MacOS.
    """
    app = next(frozen.glob("*.app"), None)
    if platform_name == "macos-arm64" and app is not None:
        return app / "Contents" / "MacOS" / FROZEN_NAME
    suffix = ".exe" if platform_name == "windows-x64" else ""
    onedir = frozen / FROZEN_NAME
    # The fallback keeps a layout change from becoming a mystery: if the
    # onedir is not there, the old spelling is at least a path that exists.
    return onedir / f"{FROZEN_NAME}{suffix}" if onedir.is_dir() else frozen / f"{FROZEN_NAME}{suffix}"


if __name__ == "__main__":
    sys.exit(main())
