"""Assemble a launcher payload: ``app/`` and ``bin/`` for one platform (ADR-011).

    python packaging/build_payload.py --platform macos-arm64 --out build/payload \\
        [--ffmpeg-dir DIR]     # macOS: the output of build_ffmpeg_macos.sh

``app/`` is the git-tracked source the app runs from - ``scribe/``,
``pyproject.toml``, ``uv.lock``, ``.env.example`` and the doctor's smoke clip -
so nothing untracked on the build machine can ride along. ``bin/`` holds uv
and ffmpeg/ffprobe from ``packaging/tools.json``; every download is checked
against its pinned sha256 and the build stops on a mismatch. ``MANIFEST.json``
records what went in, with a sha256 per file.

Stdlib only, so CI can run it before any environment exists.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = json.loads((REPO / "packaging" / "tools.json").read_text(encoding="utf-8"))
PLATFORMS = ("windows-x64", "macos-arm64", "linux-x64")
CACHE = REPO / "packaging" / ".cache"

# What the app needs at run time, relative to the repository.
APP_PATHS = ("scribe", "pyproject.toml", "uv.lock", ".env.example", "tests/fixtures/clip30.wav")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, expected: str) -> bytes:
    """``url``'s bytes, from the cache when present; refused on a bad sum."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / expected
    if cached.exists():
        data = cached.read_bytes()
    else:
        print(f"fetch {url}", flush=True)
        with urllib.request.urlopen(url, timeout=300) as response:
            data = response.read()
    actual = sha256(data)
    if actual != expected:
        raise SystemExit(f"checksum mismatch for {url}: expected {expected}, got {actual}")
    cached.write_bytes(data)
    return data


def extract(data: bytes, url: str, members: dict[str, str], dest: Path) -> None:
    """Copy the named archive members to ``dest`` (targets may climb one level)."""
    if url.endswith(".zip"):
        archive = zipfile.ZipFile(io.BytesIO(data))
        read = lambda name: archive.read(name)  # noqa: E731
    else:
        archive = tarfile.open(fileobj=io.BytesIO(data))
        read = lambda name: archive.extractfile(name).read()  # noqa: E731
    for member, target in members.items():
        out = (dest / target).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(read(member))
        out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def tracked_files(paths: tuple[str, ...]) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *paths], cwd=REPO, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p)


def build(platform: str, out: Path, ffmpeg_dir: Path | None, models: bool = False) -> dict:
    if out.exists():
        shutil.rmtree(out)
    app, bin_dir, licenses = out / "app", out / "bin", out / "licenses"
    for folder in (app, bin_dir, licenses):
        folder.mkdir(parents=True)

    files = tracked_files(APP_PATHS)
    for rel in files:
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    for required in ("uv.lock", "pyproject.toml", "scribe/__main__.py", "tests/fixtures/clip30.wav"):
        if not (app / required).exists():
            raise SystemExit(f"payload is missing {required}; is it committed?")

    uv = TOOLS["uv"]["platforms"][platform]
    extract(fetch(uv["url"], uv["sha256"]), uv["url"], uv["members"], bin_dir)
    with urllib.request.urlopen(TOOLS["uv"]["license_url"], timeout=60) as response:
        (licenses / "uv-LICENSE-MIT.txt").write_bytes(response.read())

    ff = TOOLS["ffmpeg"]["platforms"][platform]
    if "url" in ff:
        extract(fetch(ff["url"], ff["sha256"]), ff["url"], ff["members"], bin_dir)
    else:
        if ffmpeg_dir is None:
            raise SystemExit(f"{platform}: pass --ffmpeg-dir with the output of {ff['build']}")
        for name in ("ffmpeg", "ffprobe"):
            shutil.copy2(ffmpeg_dir / name, bin_dir / name)
        shutil.copy2(ffmpeg_dir / "ffmpeg-LICENSE.txt", licenses / "ffmpeg-LICENSE.txt")

    shipped_models: dict = {}
    if models:
        # Into `models/`, which the launcher installs as MODELS_DIR in the
        # per-user home: `diarize.local_weights_dir()` is MODELS_DIR/pyannote
        # and is tried before the Hub, so a bundled pipeline is simply found
        # (TASK-040.05). Verified against models.json before it lands here -
        # fetch_models raises rather than return a file that is not the pin.
        import fetch_models

        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        shipped_models = fetch_models.ensure(out / "models", token=token)
        fetch_models.write_licences(licenses)

    manifest = {
        "platform": platform,
        "version": _version(app),
        "models": shipped_models,
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(),
        "uv": TOOLS["uv"]["version"],
        "ffmpeg": TOOLS["ffmpeg"]["version"],
        "files": {
            str(p.relative_to(out)).replace("\\", "/"): sha256(p.read_bytes())
            for p in sorted(out.rglob("*")) if p.is_file()
        },
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _version(app: Path) -> str:
    text = (app / "scribe" / "__init__.py").read_text(encoding="utf-8")
    return text.split('__version__ = "', 1)[1].split('"', 1)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--with-models",
        action="store_true",
        help="fetch and verify the weights in models.json into the payload (about 1.6 GB)",
    )
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ffmpeg-dir", type=Path)
    args = parser.parse_args(argv)
    manifest = build(args.platform, args.out.resolve(), args.ffmpeg_dir, models=args.with_models)
    shipped = manifest.get("models") or {}
    note = f", models: {', '.join(sorted(shipped))}" if shipped else ""
    print(f"payload {manifest['platform']} {manifest['version']} ({manifest['commit'][:8]}): "
          f"{len(manifest['files'])} files at {args.out}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
