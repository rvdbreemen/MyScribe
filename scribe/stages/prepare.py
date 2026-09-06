"""One decode, into the one format the rest of the pipeline wants.

Whisper and pyannote both want mono 16 kHz PCM. Left to themselves they would
each decode the source again, in their own way, and disagree about where in
the file a given moment is - which is exactly the kind of drift that puts a
speaker label half a word late. So the file is decoded once, here, and every
later stage reads the same samples with the same clock.

Two things this stage is careful about.

**It never holds the file.** ffmpeg streams from disk to disk; a four-hour
video costs a pipe buffer, not four hours of RAM (spec section 3). The output
is a plain wav rather than anything cleverer because the next two stages read
it as an array and cleverness would only cost a decode.

**Its progress is measured, not invented.** ffmpeg's ``-progress pipe:1``
reports the position it has reached in the file, roughly twice a second, and
that position divided by the duration probe measured is the honest fraction.
When the container never told probe a duration there is nothing to divide by,
so the stage reports nothing in between and simply completes - the plan's rule
is that a stage that cannot measure itself reports 0 and then 1, never a
convincing-looking guess.

Cancellation is not handled here on purpose: the runner has no mapping for a
mid-stage cancel yet (it arrives with the transcribe stage, which owns that
exception), so raising one now would mark a cancelled job *failed*. Honouring
cancel at the stage boundary is the lesser evil, and this stage is minutes at
worst. There is no timeout either - any constant would be a lie about somebody
else's four-hour recording, and the supervisor's kill grace is the outer net.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator

from scribe import db, paths

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

# What every downstream model wants: one channel, 16 kHz, signed 16-bit.
TARGET_RATE = 16000
TARGET_CHANNELS = 1

# The filename inside the job's work directory. Fixed, because the work
# directory is per job and finalize deletes the whole thing.
WAV_NAME = "audio.wav"

# How much of ffmpeg's complaint travels with the exception - enough for the
# real sentence, short enough to sit in a `job.error_detail` column.
STDERR_TAIL = 800


class ConversionError(Exception):
    """ffmpeg opened the file and then could not turn it into audio."""


def progress_fractions(lines: Iterable[str], duration: float) -> Iterator[float]:
    """Turn ffmpeg's ``-progress`` key/value stream into 0..1 fractions.

    Split out from the subprocess so the awkward cases can be tested without
    provoking them from a real encoder: ``out_time_us=N/A`` before the first
    frame is decoded, the int64 sentinel some builds write instead, a stall
    that repeats a position, and a container that overruns the duration it
    advertised.

    ``out_time_us`` is the key, not ``out_time_ms`` - which also carries
    microseconds, an ffmpeg quirk older than most of its users.

    Every line is read whatever we make of it, including when the duration is
    unknown and there is nothing to report: ffmpeg blocks forever if its stdout
    pipe fills, so abandoning this loop early would hang the conversion.
    """
    duration = float(duration or 0.0)
    highest = 0.0
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key != "out_time_us" or duration <= 0:
            continue
        micros = _as_float(value)
        if micros is None or micros < 0:
            continue  # a position ffmpeg does not know yet is not a position
        fraction = min(1.0, micros / 1e6 / duration)
        if fraction < highest:
            continue  # a progress bar that goes backwards is worse than none
        highest = fraction
        yield fraction


def to_wav(
    src: str | Path,
    dst: str | Path,
    duration: float,
    on_progress: Callable[[float], None],
) -> Path:
    """Decode ``src`` into mono 16 kHz PCM at ``dst``; returns ``dst``.

    ``duration`` is what probe measured, and 0 means unknown - the conversion
    still happens, only the progress in between is unavailable.

    ``-y`` is the one addition to the plan's command line, and it is not
    optional: with ``-nostdin`` an output file left behind by a crashed attempt
    turns ffmpeg's overwrite prompt into an instant EOF and a failed retry.

    stderr goes to a file rather than a second pipe. Reading one pipe while
    another fills is the classic subprocess deadlock, and this loop is busy
    with stdout for as long as the conversion runs.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    log = dst.with_name(dst.name + ".ffmpeg.log")

    try:
        with open(log, "wb") as errors:
            proc = subprocess.Popen(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-y",
                    "-progress", "pipe:1",
                    "-i", str(src),
                    "-vn", "-sn", "-dn",  # audio only: no video, subs or data
                    "-ac", str(TARGET_CHANNELS),
                    "-ar", str(TARGET_RATE),
                    "-c:a", "pcm_s16le",
                    str(dst),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errors,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                with proc.stdout:
                    for fraction in progress_fractions(proc.stdout, duration):
                        on_progress(fraction)
                # Draining stdout to EOF does not reap the child; without this
                # wait, returncode is still None and every file "converts".
                returncode = proc.wait()
            except BaseException:
                # Ctrl-C, a raising callback, a dying parent: do not leave an
                # ffmpeg running against a file nobody is waiting for.
                proc.kill()
                proc.wait()
                raise

        if returncode != 0:
            # ffmpeg either wrote nothing or wrote a truncated file; neither is
            # something the next stage should ever find lying there.
            dst.unlink(missing_ok=True)
            raise ConversionError(
                f"ffmpeg could not convert {src.name}: {_tail(log)}"
            )
    finally:
        log.unlink(missing_ok=True)

    on_progress(1.0)
    return dst


def run(ctx: "RunnerContext") -> None:
    """The stage: normalise this job's media into its work directory."""
    if ctx.media_path is None:
        raise RuntimeError("prepare needs a media file, but this job has no media row")

    dst = paths.job_work_dir(ctx.job["id"]) / WAV_NAME
    try:
        to_wav(ctx.media_path, dst, _media_duration(ctx), ctx.report)
    except Exception:
        # A job that fails here never reaches finalize, which is what deletes
        # work directories - so this one would be collected by nobody. rmdir
        # rather than rmtree, deliberately: it refuses a directory with
        # anything in it, and anything in it is not ours to throw away. A job
        # that fails in a *later* stage still leaves its wav behind for
        # finalize or a sweep; that is not this stage's litter.
        with contextlib.suppress(OSError):
            dst.parent.rmdir()
        raise
    # The next stage reads samples, not the store: hand it the path rather
    # than making it re-derive one.
    ctx.state["wav"] = dst


def _media_duration(ctx: "RunnerContext") -> float:
    """Seconds of media as probe recorded them; 0 when nobody knows."""
    with db.LOCK:
        row = ctx.conn.execute(
            "SELECT duration FROM media WHERE id=?", (ctx.job["media_id"],)
        ).fetchone()
    return float(row["duration"]) if row is not None and row["duration"] else 0.0


def _tail(log: Path) -> str:
    try:
        return log.read_bytes().decode("utf-8", "replace").strip()[-STDERR_TAIL:]
    except OSError:
        return "ffmpeg failed and left no readable error output"


def _as_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
