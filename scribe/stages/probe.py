"""ffprobe is the arbiter of what counts as media.

Every later stage assumes it was handed something decodable. This stage is
where that assumption is bought: one ffprobe call, and either a summary the
pipeline can plan against or a refusal with ffprobe's own words attached.

Two different noes, kept separate on purpose (spec section 3):

* **ffprobe cannot open it** - a PDF renamed to .mp3, a truncated download, a
  file still being written. ffprobe exits non-zero and says why; that sentence
  goes into the error, because "Invalid data found when processing input" tells
  a user far more than "unsupported file" ever will.
* **ffprobe opens it happily and there is no audio** - a screen recording
  exported without its audio track. Nothing is wrong with the file; there is
  simply nothing here to transcribe.

Both raise :class:`NotMediaError`, which the runner maps to ``FFMPEG_DECODE``.
A file that is simply *gone* raises ``FileNotFoundError`` instead: "we cannot
find this file" and "this file is not media" are different problems and the
user needs to be told which one they have.

A third no, added after a recording of 8.3 seconds at -91 dBFS went through
the whole pipeline and came out ``done`` with an empty transcript: **the file
decodes and there is no sound in it**. That is what a wrong or muted input
device produces, and a job that says "done" about it is a lie the user only
discovers by opening the transcript. So probe measures loudness (ffmpeg's
volumedetect over the first :data:`SILENCE_SCAN_SECONDS`) and raises
:class:`SilentAudioError` - its own code, ``SILENT_AUDIO`` - when the peak is
below :data:`SILENT_PEAK_DB`. A microphone that works produces room noise well
above that; a quiet but real recording at -40 dBFS passes. The measurement is
emitted as a ``loudness`` event for every file, refused or not, so the number
is on the job page when someone wonders.

The other job here is `media.duration`, which is not decoration - the prepare
stage turns ffmpeg's byte progress into a percentage with it, the transcribe
stage divides segment ends by it, and `eta_seconds` scales every estimate by
it. A wrong duration makes every progress bar downstream lie.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scribe import db, jobs

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

# Generous: ffprobe normally answers in milliseconds, but a large file on a
# cold spinning disk or a network share can take real seconds, and a hang here
# would wedge the whole job with no way out.
FFPROBE_TIMEOUT = 60.0

# How much of ffprobe's complaint travels with the exception. Enough for the
# real sentence, short enough to sit in a `job.error_detail` column.
STDERR_TAIL = 800

# Loudness: how much of the file is listened to, and where "nothing" begins.
# Decoding is ~300x realtime (36 min of mp3 measured at 7.3 s), so a whole file
# would be fine for most and a minute for a four-hour one; the cap keeps probe
# an instant stage. Thirty minutes below -60 dBFS is not a quiet recording, it
# is no recording - a working microphone's room noise sits far above that.
SILENCE_SCAN_SECONDS = 1800.0
SILENT_PEAK_DB = -60.0
# Decode-only at ~300x realtime is seconds; a 4 GB video container on a
# network share is minutes of I/O even with -vn. Past this the pass is
# skipped as "not measured" rather than failing the job as RUNTIME.
LOUDNESS_TIMEOUT = 300.0
_VOLUME_RE = re.compile(r"(mean|max)_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")

# The per-stream facts anything downstream actually uses. The full ffprobe
# blob has ~40 keys per stream, most of them about video colour primaries.
_STREAM_KEYS = ("index", "codec_type", "codec_name", "channels", "sample_rate")

# Extensions a file browser may flag as "probably media", so a folder listing
# can point at the recordings without opening any of them. A hint for the eye
# and nothing more: ffprobe decides what is media (see the module docstring),
# and a `.bin` that turns out to be audio is as welcome in a job as a `.wav`.
MEDIA_EXTENSIONS = frozenset(
    {
        # audio
        ".aac", ".ac3", ".aif", ".aiff", ".amr", ".ape", ".caf", ".dts", ".flac",
        ".m4a", ".m4b", ".mka", ".mp2", ".mp3", ".oga", ".ogg", ".opus", ".wav",
        ".wma", ".wv",
        # video: the audio track is what gets transcribed
        ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg",
        ".mpg", ".mts", ".ogv", ".ts", ".vob", ".webm", ".wmv",
    }
)


class NotMediaError(Exception):
    """This file cannot be transcribed: undecodable, or carrying no audio."""


class SilentAudioError(Exception):
    """The file decodes and there is no sound in it: a dead or wrong input.

    Not a NotMediaError on purpose. That code says "fix the file"; this one
    says "fix the microphone and record again", and the board has to be able
    to tell them apart without anyone reading a sentence."""


def probe_media(path: str | Path) -> dict:
    """Ask ffprobe what this file is; raise if the answer is 'not media'.

    Returns ``{duration, format_name, streams, chapters, tags}``.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such media file: {path}")

    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams", "-show_chapters",
            str(path),
        ],
        # Bytes, not text=True: ffprobe writes UTF-8 whatever the console code
        # page says, and decoding with the locale encoding would mangle every
        # accented metadata tag. CREATE_NO_WINDOW keeps a console from flashing
        # up on Windows once per probe.
        capture_output=True,
        timeout=FFPROBE_TIMEOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise NotMediaError(
            f"ffprobe could not read {path.name}: {_text(proc.stderr)[-STDERR_TAIL:]}"
        )

    try:
        payload = json.loads(_text(proc.stdout) or "{}")
    except json.JSONDecodeError as exc:
        raise NotMediaError(
            f"ffprobe returned no usable answer for {path.name}: {exc}"
        ) from exc

    return summarize(payload)


def summarize(payload: dict[str, Any]) -> dict:
    """Reduce an ffprobe JSON blob to the facts the pipeline uses.

    Split out from the subprocess so the awkward containers can be tested
    without owning one: ffprobe reports numbers as strings, omits the
    format-level duration for some containers, and writes the literal string
    ``"N/A"`` where a number is unknown. Anything that will not parse becomes
    None rather than an exception - an unknown sample rate is not a reason to
    refuse a file whose audio decodes fine.
    """
    raw_streams = payload.get("streams") or []
    streams = [
        {key: _coerce(key, stream.get(key)) for key in _STREAM_KEYS}
        for stream in raw_streams
    ]
    if not any(s["codec_type"] == "audio" for s in streams):
        raise NotMediaError("no audio stream: there is nothing here to transcribe")

    fmt = payload.get("format") or {}
    duration = _as_float(fmt.get("duration"))
    if duration is None:
        # Matroska and some MPEG-TS captures carry no format-level duration.
        # The longest stream is the honest stand-in for the file's length.
        candidates = [_as_float(s.get("duration")) for s in raw_streams]
        known = [d for d in candidates if d is not None]
        duration = max(known) if known else None

    return {
        "duration": duration,
        "format_name": fmt.get("format_name") or "",
        "streams": streams,
        "chapters": payload.get("chapters") or [],
        "tags": fmt.get("tags") or {},
    }


def measure_loudness(path: str | Path, scan_seconds: float = SILENCE_SCAN_SECONDS) -> dict:
    """ffmpeg's volumedetect over the first ``scan_seconds``.

    Returns ``{mean_db, max_db, scanned_seconds}``; the two levels are None
    when ffmpeg decoded nothing it could measure (it then prints no volume
    lines at all), and None is never treated as silence - a measurement that
    did not happen is not a reading of zero.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-v", "info",
                "-t", f"{scan_seconds:g}", "-i", str(path),
                "-vn", "-af", "volumedetect", "-f", "null", "-",
            ],
            capture_output=True,
            timeout=LOUDNESS_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        # A slow volume or a huge video container: the measurement did not
        # happen, which is not a verdict about the sound. The job goes on
        # without it, and the loudness event says so.
        return {"mean_db": None, "max_db": None, "scanned_seconds": scan_seconds,
                "timed_out": True}
    mean_db, max_db = parse_volumedetect(_text(proc.stderr))
    return {"mean_db": mean_db, "max_db": max_db, "scanned_seconds": scan_seconds}


def parse_volumedetect(stderr: str) -> tuple[float | None, float | None]:
    """The two numbers out of volumedetect's report, or None for each missing."""
    found: dict[str, float] = {}
    for which, value in _VOLUME_RE.findall(stderr):
        found[which] = float(value)
    return found.get("mean"), found.get("max")


def run(ctx: "RunnerContext") -> None:
    """The stage: probe the media, record its duration, listen for sound."""
    if ctx.media_path is None:
        raise RuntimeError("probe needs a media file, but this job has no media row")

    info = probe_media(ctx.media_path)
    loud = measure_loudness(ctx.media_path)

    with db.LOCK:
        ctx.conn.execute(
            "UPDATE media SET duration=? WHERE id=?",
            (info["duration"], ctx.job["media_id"]),
        )
        ctx.conn.commit()

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "probe",
        duration=info["duration"],
        format_name=info["format_name"],
        streams=info["streams"],
        n_chapters=len(info["chapters"]),
        tags=info["tags"],
    )
    jobs.emit(ctx.conn, ctx.job["id"], "loudness", **loud)
    if loud["max_db"] is not None and loud["max_db"] < SILENT_PEAK_DB:
        scanned = min(loud["scanned_seconds"], info["duration"] or loud["scanned_seconds"])
        raise SilentAudioError(
            f"the recording contains no sound: peak {loud['max_db']:.0f} dBFS over the"
            f" first {scanned:.0f} s (a working microphone gives room noise above"
            f" {SILENT_PEAK_DB:.0f} dBFS). Check the input device and record again."
        )
    ctx.report(1.0)  # ffprobe is instant; the loudness pass above is seconds


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").strip()


def _coerce(key: str, value: Any) -> Any:
    if key in ("channels", "sample_rate"):
        return _as_int(value)
    return value


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)
