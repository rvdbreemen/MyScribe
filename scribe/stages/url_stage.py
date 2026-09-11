"""The `ingest_url` job type: a paste of a link becomes a recording.

Downloading is a job of its own rather than something the web process does
while the browser waits, and that is the whole design (ADR-001). A slow
network never blocks a page; a download that fails is a row on the jobs board
with a reason and a retry button; a two-hour video downloads while the user
does something else.

Two stages, and the split is where the interesting decision lives:

* **fetch** asks the URL what it is, and only then fetches. A playlist is
  *not* downloaded here - it is read flat and carried to the next stage as a
  list of entries. A single video is downloaded into this job's work
  directory, with the byte counts on the jobs board.
* **register** takes what fetch found into the library. For a video that is
  one `media.ingest_path` and one `transcribe` job. For a playlist it is one
  `ingest_url` job per entry instead - so a fifty-video playlist becomes fifty
  rows with fifty progress bars and fifty independent failures, rather than
  one job that dies on video forty-one and takes the other forty-nine with it.

The fan-out children are marked `from_playlist`, and a child that turns out to
be a playlist itself refuses rather than fanning out again: a channel URL
nested inside a playlist would otherwise spawn jobs without end, and a job
storm is very hard to get back out of a real database. That guard says nothing
about *width*, though - a channel URL pasted directly is one flat playlist of
everything the channel has published - so `MAX_FAN_OUT` bounds one link's
children too, and says the count out loud when it refuses.

The download lands in the job's work directory and is *hardlinked* into the
store, which makes it a move rather than a copy: `WORK_DIR` and `MEDIA_DIR`
are both under `DATA_DIR`, so the link always succeeds, and the runner's
`remove_job_work_dir` at the end of the job removes the only other name for
those bytes.

Cancellation is honoured at the stage boundary, not inside the download.
yt-dlp wraps an exception raised from a progress hook into a `DownloadError`,
so a cancel signalled that way would land on the board as a *failure* - the
same trade `prepare` makes, for the same reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Sequence

from scribe import jobs, media, paths
from scribe.ingest import urls
from scribe.stages import transcribe

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

JOB_TYPE = "ingest_url"
"""Registered in `scribe.runner.STAGES`. The jobs board draws its stepper from
that registry, so `fetch` and `register` appear there without further work."""

TRANSCRIBE_JOB_TYPE = "transcribe"
"""What `register` enqueues once the bytes are in the store. Spelled here
rather than imported from `scribe.web.transcribe_dialog`: a stage must not
depend on a router (ADR-001 keeps the traffic one-way)."""

# The params key carrying the transcribe options, as `TranscribeOptions
# .to_params()` produced them. Nested rather than merged into the job's own
# params so the two vocabularies cannot collide and the playlist fan-out can
# copy them through untouched.
OPTIONS_KEY = "options"

BULK_PRIORITY = -10
"""The priority a playlist's or feed's entries queue at, and the transcriptions
they queue in turn. The queue runs one job at a time, priority first, then
oldest (`jobs.claim_next`), so at the default 0 a pasted feed of 47 episodes
put two recordings made afterwards behind all 47 (reported 2026-09-12). Work
started one item at a time - a recording, an upload, a single link - stays at
0 and goes first; the bulk import carries on after it."""

ENTRY_TITLE_KEY = "entry_title"
"""What a fanned-out entry is called, carried from the playlist to its own job:
a podcast feed knows "Ep 179 - The Courthouse - Revisited" while the episode's
enclosure, fetched on its own, is only a bare ``default.mp3`` (`urls.
episode_names`). It names the job on the board, the file and the recording."""

MAX_FAN_OUT = 500
"""The most entries one link may become jobs for, inclusive.

The nested-playlist guard below does not cover this case at all: a channel URL
is a *one-level* playlist, so it fans out exactly once - into every video the
channel has ever published. And nobody has agreed to that by the time this
runs: `POST /transcribe/url` deliberately does not probe, so the preview's
"all N" was a courtesy the user may never have looked at.

Five hundred is chosen to sit above any playlist a person pastes on purpose -
a podcast back catalogue, a conference track - and far below the size at which
the answer stops being "queue it" and starts being "are you sure". Above it
the job fails with the count in the message, because a number the user can
read is the only thing that lets them decide what to do next."""


def transcribe_params(params: dict, extra_terms: Sequence[str] = ()) -> dict:
    """The options the enqueued `transcribe` job runs with.

    A copy, because the dict is handed to `jobs.enqueue` and then json-encoded;
    sharing it with the parent job's params would be a bug waiting for someone
    to mutate one of them.

    ``extra_terms`` are the names `urls.hotword_terms` found in the video's own
    metadata. They travel in the job's params because they have nowhere else to
    live: the info-json they came from sits in *this* job's work directory, and
    the runner deletes that the moment this job ends - long before the
    transcribe job it queues gets a GPU.
    """
    out = dict(params.get(OPTIONS_KEY) or {})
    terms = [str(term).strip() for term in extra_terms if str(term).strip()]
    if terms:
        out[transcribe.EXTRA_HOTWORDS_KEY] = terms
    return out


def _url(ctx: "RunnerContext") -> str:
    url = str(ctx.params.get("url") or "").strip()
    if not url:
        raise ValueError(
            "an ingest_url job needs a url in its params; there is nothing to fetch"
        )
    return url


def fetch(ctx: "RunnerContext") -> None:
    """Find out what the URL is, then fetch it - unless it is a playlist."""
    url = _url(ctx)
    cookies_file = ctx.params.get("cookies_file") or None

    info = urls.probe(url, cookies_file=cookies_file)

    if info.kind == "playlist":
        if ctx.params.get("from_playlist"):
            # A playlist inside a playlist. Refusing costs the user one entry;
            # fanning out again could cost them their database.
            raise urls.UnsupportedUrl(
                f"{info.title or url} is a playlist inside a playlist; "
                "add it as its own URL if you want everything in it"
            )
        ctx.state["playlist"] = info
        jobs.emit(
            ctx.conn,
            ctx.job["id"],
            "url-playlist",
            title=info.title,
            uploader=info.uploader,
            entries=len(info.entries),
            webpage_url=info.webpage_url,
        )
        ctx.report(1.0)
        return

    ctx.state["downloaded"] = urls.download(
        url,
        paths.job_work_dir(ctx.job["id"]),
        on_progress=ctx.report,
        cookies_file=cookies_file,
        title=str(ctx.params.get(ENTRY_TITLE_KEY) or "").strip() or None,
    )


def register(ctx: "RunnerContext") -> None:
    """Take what fetch found into the library, and queue the work on it."""
    playlist = ctx.state.get("playlist")
    if playlist is not None:
        _fan_out(ctx, playlist)
        return

    downloaded: urls.DownloadedMedia = ctx.state["downloaded"]
    row = media.ingest_path(
        ctx.conn,
        downloaded.path,
        title=downloaded.title or None,
        folder_id=ctx.params.get("folder_id"),
    )
    terms = urls.hotword_terms(downloaded.info)
    queued = jobs.enqueue(
        ctx.conn,
        TRANSCRIBE_JOB_TYPE,
        media_id=row["id"],
        params=transcribe_params(ctx.params, terms),
        # A feed's episode keeps its place behind hand-started work.
        priority=int(ctx.job.get("priority") or 0),
    )

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "url",
        # Not `job_id`: `jobs.emit` spends that name on its own parameter, and a
        # payload key of the same name is a TypeError rather than an event.
        transcribe_job=queued,
        media_id=row["id"],
        title=row["title"],
        uploader=downloaded.uploader,
        duration=downloaded.duration,
        deduped=bool(row.get("deduped")),
    )
    ctx.report(1.0)


def _fan_out(ctx: "RunnerContext", playlist: urls.UrlInfo) -> None:
    """One `ingest_url` job per entry, carrying this job's options forward."""
    if len(playlist.entries) > MAX_FAN_OUT:
        raise urls.TooManyEntries(
            f"{playlist.title or 'that link'} holds {len(playlist.entries)} videos"
            f" and this app queues at most {MAX_FAN_OUT} from one link."
            " A channel URL usually means the whole channel; add the playlists"
            " inside it one at a time, or the videos you actually want."
        )
    queued = [
        jobs.enqueue(
            ctx.conn,
            JOB_TYPE,
            priority=BULK_PRIORITY,
            params={
                **ctx.params,
                "url": entry["url"],
                "from_playlist": True,
                ENTRY_TITLE_KEY: entry.get("name") or entry.get("title") or "",
            },
        )
        for entry in playlist.entries
    ]
    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "url-fanout",
        title=playlist.title,
        entries=len(playlist.entries),
        queued=queued,
    )
    ctx.report(1.0)


STAGES: list[tuple[str, Callable]] = [
    ("fetch", fetch),
    ("register", register),
]
