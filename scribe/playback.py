"""Which file a browser's player gets, so that a seek lands where it says.

The transcript view follows the sound word by word, and it can only be as
right as the player's clock: `currentTime` has to be the moment the speakers
are playing. For most of what a browser plays that holds. Not for all of it.

* **A VBR MP3 seeks by estimate.** Its first frame carries a Xing (or VBRI)
  table of contents, a hundred points across the whole file; a browser asked
  for 15:00 starts decoding near the point that says 15:00 and reports 15:00.
  Measured on 2026-09-15 (TASK-057, screen capture against loopback of the
  laptop speakers) on a 39-minute recording: after a seek Firefox 155 played
  3.0 s late at 900 s and 3.7 s late at 1700 s, Chrome 152 0.36 s. From the
  start both were in sync, and the word times were right all along.
* **A CBR MP3 and AAC in an MP4 box seek exactly.** A constant bitrate (an
  "Info" tag in that first frame) makes a byte offset a time; an MP4 carries
  a sample table. The same recording as the proxy below measured -14..+13 ms
  after the same seeks.

`seeks_exactly` decides, from the first frame of an MP3 and from the suffix
of everything else. What does not seek exactly plays from the proxy: AAC in
a faststart MP4 at `MEDIA_DIR/proxy/<sha256>.m4a`, the file the transcript
view has always made for a container a browser cannot open at all. The
original stays in the store untouched - the download route hands it out and
every timestamp came from it.

The proxy's rules:

* **One transcode, never a half-written proxy.** ffmpeg writes to a `.part`
  name that is renamed into place, so nobody finds a truncated file wearing
  the proxy's name, and a caller that arrives during a transcode waits on a
  per-proxy lock for it instead of starting a second one - a browser asks
  for one `<audio>` twice. Across processes (the runner child and the web
  process) the rename is what keeps it whole: two transcodes can race, and
  the second rename replaces a file with an identical one.
* **The same audio, to 50 ms.** Timestamps came from the original; a proxy
  whose ffprobe duration is not within 50 ms of the original's would put
  every highlight late or early, so it is refused, and a refused proxy never
  reaches its final name - the next attempt tries again.

Who makes it: the `proxy` stage of the transcribe pipeline for new media,
`python -m scribe.proxies` for a library that predates that stage, the HTML
export when it finds one missing, and the audio route for a container a
browser cannot play. The route does not make one for a VBR MP3: that
original plays, only not exactly after a seek, and a transcode is 80 s for
39 minutes that the player would sit blank. The page says so instead.

No web framework and no model here: the runner child imports this module for
the stage, the web process for the route (ADR-001). ffmpeg and ffprobe are
its only subprocesses.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from scribe import db, paths
from scribe.media import proxy_path_for

log = logging.getLogger(__name__)

# Containers a browser's <audio> element plays from the original file, with
# the type it is served under. Anything else only ever plays from the proxy.
PLAYABLE: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}

# The playable containers that seek exactly whatever is inside them. An MP3
# is decided by its first frame (`mp3_seeks_exactly`). Raw ADTS AAC is left
# out: it has no index at all, so a browser estimates the byte offset from
# the bitrate, as it does for a VBR MP3 - reasoned, not measured, because the
# library that showed the fault held no .aac. Measured: MP3 both ways and AAC
# in MP4. The rest carry an index or timestamps a browser seeks by.
EXACT_SUFFIXES = frozenset(PLAYABLE) - {".mp3", ".aac"}

# The proxy: AAC in an MP4 box. Where it lives is the store's business
# (`media.proxy_path_for`), so the purge knows to remove it with the row.
PROXY_MEDIA_TYPE = "audio/mp4"
PROXY_BITRATE = "96k"

# How far the proxy's duration may sit from the original's before the
# timestamps would visibly drift: the spec's 50 ms.
PROXY_TOLERANCE_SECONDS = 0.05

# How much of ffmpeg's complaint travels with the exception.
STDERR_TAIL = 800

# How far past its ID3 tags an MP3's first frame may start. Encoders pad a
# little; a first frame further in than this is not one we vouch for.
FRAME_SCAN_BYTES = 64 * 1024

# How many ID3v2 tags in a row are skipped before the first frame. One is
# normal; a file edited by two taggers can carry two.
ID3_MAX_TAGS = 4


class ProxyError(Exception):
    """ffmpeg could not make the proxy, or made one that is not the same audio."""


# --- the rule ----------------------------------------------------------------------------


def seeks_exactly(path: Path) -> bool:
    """Whether a browser seeking in ``path`` plays from where it says it does."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return mp3_seeks_exactly(path)
    return suffix in EXACT_SUFFIXES


def mp3_seeks_exactly(path: Path) -> bool:
    """True only when the first MPEG audio frame carries an "Info" tag.

    LAME and ffmpeg both write a tag into the first frame, after its side
    information: "Info" for a constant bitrate, "Xing" for a variable one
    (Fraunhofer's encoder writes "VBRI" at a fixed offset instead). Anything
    but "Info" - a table of contents, or no tag, which is a file nobody
    vouched for - does not seek exactly. Wrong in that direction costs a
    proxy; wrong in the other costs seconds of highlight.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(_after_id3(fh))
            window = fh.read(FRAME_SCAN_BYTES)
    except OSError:
        return False
    at = _first_frame(window)
    if at is None:
        return False
    header = window[at:at + 4]
    mono = header[3] >> 6 == 0b11
    mpeg1 = (header[1] >> 3) & 0b11 == 0b11
    side_info = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    crc = 0 if header[1] & 0b1 else 2  # the protection bit is 0 when a CRC follows
    tag_at = at + 4 + crc + side_info
    return window[tag_at:tag_at + 4] == b"Info"


def _after_id3(fh: BinaryIO) -> int:
    """The offset just past the ID3v2 tags at the start of the file. The size
    is syncsafe - seven bits a byte - and excludes the 10-byte header and the
    footer a tag may have."""
    offset = 0
    for _ in range(ID3_MAX_TAGS):
        fh.seek(offset)
        head = fh.read(10)
        if len(head) < 10 or head[:3] != b"ID3":
            break
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        footer = 10 if head[5] & 0x10 else 0
        offset += 10 + size + footer
    return offset


def _first_frame(data: bytes) -> int | None:
    """Where the first MPEG Layer III frame header in ``data`` starts."""
    at = data.find(b"\xff")
    while 0 <= at <= len(data) - 4:
        if _is_layer3_header(data[at:at + 4]):
            return at
        at = data.find(b"\xff", at + 1)
    return None


def _is_layer3_header(h: bytes) -> bool:
    return (
        h[1] & 0xE0 == 0xE0  # the rest of the 11-bit sync
        and (h[1] >> 3) & 0b11 != 0b01  # a version that exists
        and (h[1] >> 1) & 0b11 == 0b01  # Layer III
        and h[2] >> 4 not in (0b0000, 0b1111)  # neither "free" nor invalid bitrate
        and (h[2] >> 2) & 0b11 != 0b11  # a sample rate that exists
    )


# --- which file the player gets ----------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """The file to play, its media type, and whether a seek in it is exact."""

    path: Path
    media_type: str
    exact: bool


def source(original: Path, sha256: str) -> Source | None:
    """What a player should get for the stored ``original``, without making
    anything: the original when it seeks exactly, else the proxy when it is
    there, else a playable original that does not seek exactly. None for a
    container a browser cannot play whose proxy nobody has made yet."""
    suffix = original.suffix.lower()
    if seeks_exactly(original):
        return Source(original, PLAYABLE[suffix], exact=True)
    proxy = proxy_path_for(sha256)
    if proxy.is_file():
        return Source(proxy, PROXY_MEDIA_TYPE, exact=True)
    if suffix in PLAYABLE:
        return Source(original, PLAYABLE[suffix], exact=False)
    return None


# --- the proxy ---------------------------------------------------------------------------


def transcode(src: Path, dst: Path) -> None:
    """Write ``src``'s audio as AAC in a faststart MP4 at ``dst``.

    ``-f mp4`` is spelled out because ``dst`` is a ``.part`` name ffmpeg
    cannot infer a container from. CREATE_NO_WINDOW keeps a console from
    flashing up on Windows; there is no timeout, because any constant would
    be a lie about somebody else's ten-hour recording.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-i", str(src),
            "-vn", "-sn", "-dn",
            "-c:a", "aac", "-b:a", PROXY_BITRATE,
            "-movflags", "+faststart",
            "-f", "mp4",
            str(dst),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[-STDERR_TAIL:]
        raise ProxyError(f"ffmpeg could not convert {src.name}: {detail}")


def probe_duration(path: Path) -> float | None:
    """The file's duration as ffprobe reports it; None when it cannot say."""
    # Imported here, not at the top: scribe.stages imports the proxy stage,
    # which imports this module.
    from scribe.stages import probe

    try:
        return probe.probe_media(path)["duration"]
    except (probe.NotMediaError, OSError, subprocess.SubprocessError):
        return None


def durations_agree(original: float | None, proxy: float | None) -> bool:
    """Whether the proxy is the same length as the original, to the tolerance.
    An unknown duration on either side is a disagreement: the timestamps
    cannot be trusted to a file nobody could measure."""
    if original is None or proxy is None:
        return False
    return abs(float(original) - float(proxy)) <= PROXY_TOLERANCE_SECONDS


# One lock per proxy path. The dict is never pruned: one lock per recording
# ever proxied by this process, which is bytes.
_proxy_locks: dict[str, threading.Lock] = {}
_proxy_locks_guard = threading.Lock()


def _proxy_lock(proxy: Path) -> threading.Lock:
    key = os.path.normcase(str(proxy))
    with _proxy_locks_guard:
        return _proxy_locks.setdefault(key, threading.Lock())


def ensure_proxy(original: Path, proxy: Path) -> None:
    """Make the proxy for ``original`` at ``proxy`` unless it is already there.

    The transcode lands on a temporary name and is renamed into place only
    once its duration has been checked against the original's, so the proxy
    path never names a file that is not known to be the same audio. One
    transcode per proxy at a time in this process: a caller that arrives
    during one waits for it and finds the result.
    """
    if proxy.is_file():
        return
    with _proxy_lock(proxy):
        if proxy.is_file():  # the caller ahead of this one made it
            return
        proxy.parent.mkdir(parents=True, exist_ok=True)
        tmp = proxy.with_name(f"{proxy.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            transcode(original, tmp)
            wanted, got = probe_duration(original), probe_duration(tmp)
            if not durations_agree(wanted, got):
                raise ProxyError(
                    f"the proxy of {original.name} lasts {got} s where the original"
                    f" lasts {wanted} s, more than {PROXY_TOLERANCE_SECONDS * 1000:.0f} ms apart"
                )
            os.replace(tmp, proxy)
        finally:
            tmp.unlink(missing_ok=True)  # a no-op once it has been renamed away


# --- the library that predates the proxy stage -------------------------------------------


def missing(conn) -> list[dict]:
    """Every media whose stored original is on disk, does not seek exactly
    and has no proxy yet, in id order. Trashed media included: a restore
    brings them back to a player."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT id, sha256, store_path, orig_name FROM media ORDER BY id"
        ).fetchall()
    todo = []
    for row in rows:
        original = paths.DATA_DIR / row["store_path"]
        if original.is_file() and not seeks_exactly(original) and not proxy_path_for(row["sha256"]).is_file():
            todo.append(dict(row))
    return todo


def main(argv: list[str] | None = None) -> int:
    """``python -m scribe.proxies [--dry-run]``: make every missing proxy.

    Safe beside a running app: it reads the library, writes only under
    MEDIA_DIR/proxy, and every proxy lands by rename. Exit 1 when any proxy
    could not be made, each with its reason on its own line.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scribe.proxies",
        description="Make the exact-seeking AAC copy of every recording that needs one and has none yet.",
    )
    parser.add_argument("--dry-run", action="store_true", help="list those recordings and make nothing")
    args = parser.parse_args(argv)
    # A title the console's code page cannot spell should cost a character, not the run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if not paths.DB_PATH.is_file():
        print(f"No library at {paths.DB_PATH}.")
        return 1
    conn = db.connect(paths.DB_PATH)
    try:
        todo = missing(conn)
    finally:
        conn.close()

    if not todo:
        print("Every recording already plays from a file that seeks exactly.")
        return 0
    verb = "would get" if args.dry_run else "get"
    print(f"{len(todo)} recording(s) {verb} an exact-seeking copy:", flush=True)
    failed = 0
    for n, row in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] media {row['id']}  {row['orig_name']}", end="", flush=True)
        if args.dry_run:
            print()
            continue
        started = time.monotonic()
        try:
            ensure_proxy(paths.DATA_DIR / row["store_path"], proxy_path_for(row["sha256"]))
        except ProxyError as exc:
            failed += 1
            print(f"  FAILED: {exc}", flush=True)
            continue
        print(f"  made in {time.monotonic() - started:.0f} s", flush=True)
    if failed:
        print(f"{failed} of {len(todo)} could not be made; those play from the original.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
