"""The recording's exact-seeking copy, made while the job is at it anyway.

A browser seeks a VBR MP3 by estimate and plays seconds away from where its
clock says (TASK-057; the measurement is in `scribe.playback`). What does not
seek exactly plays from an AAC proxy, and this stage makes that proxy for new
media, so the transcript opens onto a player whose seeks land where it says
rather than onto an 80-second transcode or a highlight that runs late. An
original that seeks exactly costs one read of its first frame and nothing
else; a proxy that is already there costs a stat.

It follows prepare: both are ffmpeg over the original and neither needs the
card, so the models after them still get the machine to themselves.

**Its failure is not the job's.** The words do not depend on the proxy. A
proxy ffmpeg cannot make, or makes a different length, is logged with the
reason and the job goes on; the page tells the reader the player fell back
to the original, and `python -m scribe.proxies` tries again.

Like prepare it reports 0 and then 1 - the transcode passes out no measure
of itself - and it does not honour a cancel mid-stage: 80 s for 39 minutes,
and the runner checks the flag again before the next stage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from scribe import applog, db, playback
from scribe.media import proxy_path_for

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

log = logging.getLogger(__name__)


def run(ctx: "RunnerContext") -> None:
    """The stage: make this job's media's proxy when its original needs one."""
    if ctx.media_path is None:
        raise RuntimeError("proxy needs a media file, but this job has no media row")
    if playback.seeks_exactly(ctx.media_path):
        return

    media_id = ctx.job["media_id"]
    with db.LOCK:
        row = ctx.conn.execute("SELECT sha256 FROM media WHERE id=?", (media_id,)).fetchone()
    try:
        playback.ensure_proxy(ctx.media_path, proxy_path_for(row["sha256"]))
    except playback.ProxyError as exc:
        log.warning("no proxy for media %s; it plays from the original: %s", media_id, exc)
        applog.log("proxy.failed", level="warn", job=ctx.job["id"], media=media_id, error=str(exc))
