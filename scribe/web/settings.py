"""Settings: what this machine has, and what the next transcription gets.

Everything about this machine on one page, and none of it loads a model in
the web process (ADR-001):

* **The doctor.** The CPU checks (`doctor.CPU_CHECKS`: python, sqlite,
  ffmpeg, the data directory, disk, database) run in the request; they take
  under a second and touch nothing a browser could not. The GPU checks load a
  model and transcribe a clip, which is exactly what this process never does,
  so they are a job: `POST /settings/doctor` with `gpu=1` queues a `doctor`
  job, the runner child runs `doctor.gpu_stage` and stores what it found in
  the `doctor_last` setting, and this page shows that result - and the job it
  came from - the next time it is asked. One such job at a time: a second one
  would only measure the first one's card.
* **The models on disk.** `doctor.installed_models` reads the Hugging Face
  hub cache and the app's own weights directory. Measured, never opened; the
  names of the runtimes that would open them do not appear in this module.
* **The store.** How much MEDIA_DIR holds and how much room is left on its
  drive, so a disk filling up is visible before a job finds out.
* **The defaults.** Language, tier and speakers are the same `setting` rows
  the transcribe dialog reads (`transcribe_dialog.read_defaults`) and writes
  back on every submit; saving here is the same write. The browse roots are
  `fsbrowse.SETTING_KEY`, one absolute directory per line, checked to exist
  before they are stored - the user is at the form, so a typo can be named
  now rather than surface as a 403 in the dialog later. A blank field
  deletes the row and the default root returns.
* **The watch folders.** Folders that ingest by themselves: a file dropped
  in one becomes a recording and a queued job without anybody opening the
  dialog. The rows are `watch_folder`, added here and by the first-run setup
  through the same `add_watched`, switched off here, and read by the watcher
  thread the lifespan starts; each carries its own transcribe
  options, because nobody is at the dialog when the file lands. A path is
  checked against `fsbrowse`'s roots, against being inside `DATA_DIR` (the
  app's own scratch is full of things that look like media), and against
  already being watched - all three at the form, where a mistake can be
  named, rather than in a thread where it would be a log line.
* **Start at login.** One per-user login entry - a value under this user's own
  Run key, a launchd agent or an XDG autostart file - written and read back by
  `scribe.autostart`. Nothing about it is stored here: the OS is asked on every
  load, so an entry removed by hand reads as off. It is the counterpart to the
  watch folders above, which do nothing at all while the app is not running.
* **The glossary.** The names this machine should get right, spent on both
  sides of the decode (`scribe.glossary`): as `hotwords` while a recording is
  transcribed, and afterwards as a correction layer over the words it
  produced. Terms are `vocab` rows, added one at a time or pasted in one per
  line. Nothing is corrected here - re-applying the glossary reads whole
  transcripts, so the button queues one `correct` job per recording that has
  one and the runner children do the work (ADR-001). Removing a term takes its
  corrections out on the next such re-run, because each job replaces its run's
  layer outright rather than adding to it.
* **The export presets.** One `export_preset` row per saved set of export
  options, read and written through `scribe.web.exports_ui` so the export
  dialog and this page cannot disagree about what a preset is. Saving takes
  a name and either the option fields (what the dialog posts) or one
  ``options`` field of JSON (this page's form); a saved name cannot be one
  of the built-ins and cannot be taken twice.

Every mutation is a POST with a form body that answers twice: an htmx post
gets the section it changed re-rendered, with a flash out of band for the
page's status region; a plain post gets a 303 back to the page. A preset
saved from the export dialog is the one exception: the dialog targets its
preset select (`HX-Target` names it), and gets that select back with the
new preset chosen, so the fields the user just filled in stay as they are.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from scribe import accel, autostart, credentials, db, doctor, fsbrowse, glossary, jobs, paths
from scribe.exports.options import PRESETS
from scribe.ingest import watching
from scribe.llm import base as llm_base
from scribe.llm import ollama
from scribe.options import TranscribeOptions, parse_options
from scribe.stages import correct, probe, transcribe
from scribe.web import ai_ui, exports_ui, library, render, transcribe_dialog
from scribe.web.transcribe_dialog import human_size

router = APIRouter()

# The form field carrying the browse roots is named after the setting it sets.
FIELD_ROOTS = fsbrowse.SETTING_KEY
# The form field on the doctor form that asks for the GPU job as well.
FIELD_GPU = "gpu"

FLASH_SAVED = "Settings saved."
FLASH_HF_SAVED = "Hugging Face token saved."
FLASH_HF_CLEARED = "Hugging Face token cleared; the environment decides now."
FLASH_PRESET_SAVED = "Preset saved."
FLASH_PRESET_DELETED = "Preset deleted."
FLASH_WATCH_ADDED = "Watching that folder. Anything already in it is picked up shortly."
FLASH_WATCH_ENABLED = "Watching that folder again."
FLASH_WATCH_DISABLED = "Stopped watching that folder."
FLASH_WATCH_REMOVED = "Folder removed. Everything already transcribed stays in the library."
FLASH_TERM_ADDED = "Added to the glossary. It biases the next transcription and corrects the ones already here."
FLASH_TERM_REMOVED = "Term removed. Re-run the corrections to take its changes back out of the transcripts."
FLASH_AUTOSTART_ON = "MyScribe will start when you log in."
FLASH_AUTOSTART_OFF = "MyScribe will not start when you log in."
FLASH_CPU_FALLBACK_ON = "A job will run on the CPU when the GPU cannot be reached."
FLASH_CPU_FALLBACK_OFF = "A job is refused when the GPU cannot be reached."

# The form field carrying the folder to watch, and the one carrying its switch.
FIELD_WATCH_PATH = "path"
FIELD_WATCH_ENABLED = "enabled"
FIELD_WATCH_VIDEO = "include_video"


def _count(n: int, one: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {one}s"


def _what(found: "watching.Preview", include_video: bool) -> str:
    video = sum(n for ext, n in found.by_extension.items() if ext in probe.VIDEO_EXTENSIONS)
    audio = found.taken - video
    if include_video and video:
        return f"{_count(audio, 'audio file')} and {_count(video, 'video file')}" if audio else _count(video, "video file")
    return _count(audio, "audio file")


def watch_added_sentence(left: "watching.Preview", include_video: bool) -> str:
    """What a new watch folder does with what it already holds, said right
    after adding it (TASK-107.01). 'Picked up shortly' told nobody that
    ~/Downloads was 882 jobs; now the folder takes what arrives, and what was
    there is counted here and left alone until somebody asks for it."""
    sentence = "Watching that folder for new recordings."
    if left.taken:
        verb = "was" if left.taken == 1 else "were"
        sentence += (f" {_what(left, include_video)} already in it {verb} left alone; "
                     "Transcribe them below if you want them too.")
    if left.skipped_video:
        sentence += f" {_count(left.skipped_video, 'video file')} ignored: video is off for this folder."
    return sentence


# The switch on the start-at-login card.
FIELD_AUTOSTART = "enabled"

# The glossary form's fields: one term with its weight and known misspellings,
# or a whole list pasted into the import box.
FIELD_TERM = "term"
FIELD_WEIGHT = "weight"
FIELD_VARIANTS = "variants"
FIELD_TERM_LIST = "terms"


# --- what the page shows ---------------------------------------------------------


def store_usage(root: Path) -> dict:
    """What the media store holds and what its drive has left.

    ``bytes`` is the sum of the files under ``root``, links counted as links;
    a recording hardlinked in from elsewhere on the same volume counts its
    full size, which is what the store would hold if the original went away.
    ``free`` and ``total`` are the drive's, measured at ``root`` or, before
    the store exists, at its parent. Never raises: a store that cannot be
    measured shows zeros, not an error page.
    """
    files = 0
    total = 0
    if root.is_dir():
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                try:
                    total += os.lstat(os.path.join(dirpath, filename)).st_size
                except OSError:
                    continue
                files += 1
    probe = root if root.exists() else root.parent
    try:
        disk = shutil.disk_usage(probe)
        free, disk_total = disk.free, disk.total
    except OSError:
        free, disk_total = 0, 0
    return {"path": root, "files": files, "bytes": total, "free": free, "total": disk_total}


def pending_doctor_job(conn: sqlite3.Connection) -> dict | None:
    """The doctor job still queued or running, if there is one."""
    with db.LOCK:
        row = conn.execute(
            "SELECT id, status FROM job WHERE type=? AND status IN ('queued', 'running')"
            " ORDER BY id LIMIT 1",
            (doctor.JOB_TYPE,),
        ).fetchone()
    return None if row is None else dict(row)


def queue_gpu_checks(conn: sqlite3.Connection) -> tuple[int, bool]:
    """Queue the doctor job; returns (job id, whether it is new).

    A job already waiting or running is returned instead of a second one:
    the answer would be the same card measured twice, a minute apart.
    """
    pending = pending_doctor_job(conn)
    if pending is not None:
        return pending["id"], False
    return jobs.enqueue(conn, doctor.JOB_TYPE), True


def _roots_setting(conn: sqlite3.Connection) -> str | None:
    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (fsbrowse.SETTING_KEY,)
        ).fetchone()
    return None if row is None else row["value"]


def doctor_context(
    conn: sqlite3.Connection,
    *,
    cpu_checks: list[doctor.Check] | None = None,
    flash: str | None = None,
) -> dict:
    """What _doctor_panel.html renders from. ``cpu_checks`` are run here
    unless the caller already has them.

    `WEB_SAFE_CHECKS` rather than every CPU check (TASK-022): the accelerator
    check imports torch to find out whether there is a card, and this runs in
    the web process, where ADR-001 says a model runtime may never be. What the
    machine would transcribe on is shown from the last doctor run instead -
    that job runs in a runner child, where importing torch is the point.
    """
    return {
        "cpu_checks": doctor.web_checks() if cpu_checks is None else cpu_checks,
        "gpu_last": doctor.last_run(conn),
        "gpu_job": pending_doctor_job(conn),
        "flash": flash,
    }


def defaults_context(conn: sqlite3.Connection, *, flash: str | None = None) -> dict:
    """What _settings_defaults.html renders from: the dialog's defaults and
    the browse roots, stored and effective."""
    return {
        "options": transcribe_dialog.read_defaults(conn),
        "languages": transcribe.LANGUAGE_CHOICES,
        "roots_text": _roots_setting(conn) or "",
        "roots": fsbrowse.allowed_roots(conn),
        "default_roots": fsbrowse.ALLOWED_ROOTS,
        "hf": hf_token_context(conn),
        "flash": flash,
    }


def hf_token_context(conn: sqlite3.Connection) -> dict:
    """What the page says about the Hugging Face token, never its value.

    The same shape and the same manners as a provider key (`ai_ui`): dots when
    one resolves, and the *name of the source* that answered, so a stale
    environment variable outranking a token just typed is visible without the
    value being. The token itself is written to `setting` and never read back
    into a response.

    It lives beside "Recognise speakers" rather than with the AI providers
    because that is what it buys: `diarize.hf_token` is read by the diarize
    stage, not by anything that answers questions about a transcript.

    The lookup is `scribe.credentials`, so this page cannot disagree with the
    stage about whether a token is set - it once could, and did. The source is
    the short one a provider key prints (`credentials.short_source`); the
    hive and the `.env` path belong to the found table, not to a page whose
    wording is already fixed by the tests around it.
    """
    from scribe.stages import diarize

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE)
    source = credentials.short_source(resolved.source)
    return {
        "found": resolved.found,
        "source": source,
        "mask": ai_ui.KEY_MASK if resolved.found else "",
        "stored_here": source == credentials.SETTINGS,
        "conditions_url": f"https://hf.co/{diarize.DEFAULT_PIPELINE}",
        "setting_key": diarize.SETTING_TOKEN,
    }


def models_context() -> dict:
    """The weights on this machine, sizes already formatted for the table."""
    local = doctor.local_weights_dir()
    return {
        "models": [
            {
                "name": model.name,
                "path": model.path,
                "size": human_size(model.size_bytes),
                "source": model.source,
            }
            for model in doctor.installed_models()
        ],
        "cache_dir": doctor.hf_cache_dir(),
        "local_weights": local if local.is_dir() else None,
    }


def storage_context() -> dict:
    # `library` in this module is the page module scribe.web.library.
    from scribe.library import CHANGE_LIBRARY

    usage = store_usage(paths.MEDIA_DIR)
    return {
        "storage": {
            **usage,
            "size": human_size(usage["bytes"]),
            "free_size": human_size(usage["free"]),
            "total_size": human_size(usage["total"]),
            # TASK-089.19, criterion 10: the store's path is above; how to
            # point MyScribe at another library is this one line.
            "change_library": CHANGE_LIBRARY,
        }
    }


def presets_context(conn: sqlite3.Connection, *, flash: str | None = None) -> dict:
    """What _settings_presets.html renders from: the saved presets, each
    with a one-line summary, and the built-ins by name."""
    return {
        "presets": [
            {**preset, "summary": exports_ui.describe(preset["options"])}
            for preset in exports_ui.saved_presets(conn)
        ],
        "builtin_presets": [
            {"name": name, "label": exports_ui.PRESET_LABELS.get(name, name)} for name in PRESETS
        ],
        "flash": flash,
    }


def describe_options(options) -> str:
    """One line saying what a watched folder transcribes with.

    The tier's own label, never the model it maps to: this sits next to a
    folder path, where a checkpoint name answers a question nobody asked - and
    ADR-004 keeps that name spelled in exactly one module anyway.
    """
    language = dict(transcribe.LANGUAGE_CHOICES).get(options.language, "Auto-detect")
    tier = "Maximum" if options.tier == "max" else "Turbo"
    speakers = "speakers recognised" if options.diarize else "no speaker recognition"
    return f"{language}, {tier}, {speakers}"


def watch_context(
    conn: sqlite3.Connection,
    *,
    watcher: "watching.Watcher | None" = None,
    flash: str | None = None,
) -> dict:
    """What _settings_watch.html renders from: the folders, each with its
    options summarised and every reason it might not be doing anything.

    Three ways a row that says "On" can still be taking nothing in, and the
    page has to say which:
      missing       - not on this machine right now
      outside_roots - the browse roots were narrowed and no longer cover it,
                      so `watching.watchable` skips it (folders() computes
                      this as `allowed`)
      unwatchable   - the observer refused to open a watch on it. Files
                      already there are still found by the startup reconcile;
                      new ones are not noticed until the next start.
    `watcher` is None when the app runs without a supervisor, and then
    `unwatchable` stays False: nothing has tried, which is not the same as
    tried and refused.
    """
    return {
        "watch_folders": [
            {
                "id": folder["id"],
                "path": folder["path"],
                "enabled": bool(folder["enabled"]),
                "missing": not Path(folder["path"]).is_dir(),
                "outside_roots": not folder.get("allowed", True),
                "unwatchable": bool(watcher and watcher.cannot_watch(folder["path"])),
                "summary": describe_options(folder["options"]) + ("; audio and video" if folder.get("include_video", 1) else "; audio only"),
                "left": watching.existing_left(conn, folder["id"]),
                "include_video": bool(folder.get("include_video", 1)),
            }
            for folder in watching.folders(conn, enabled_only=False)
        ],
        "quiesce_seconds": int(watching.QUIESCE_SECONDS),
        # What the add-form opens with. Named apart from `defaults_context`'s
        # `options` so the two sections cannot shadow each other in
        # `page_context`, even though today they hold the same defaults.
        "watch_options": transcribe_dialog.read_defaults(conn),
        "languages": transcribe.LANGUAGE_CHOICES,
        "flash": flash,
    }


def glossary_context(conn: sqlite3.Connection, *, flash: str | None = None) -> dict:
    """What _glossary.html renders from: the terms, heaviest first, and how
    much of the library a re-run would cover."""
    return {
        "glossary_terms": [
            {
                "id": entry.id,
                "term": entry.term,
                "weight": entry.weight,
                "variants": ", ".join(entry.variants),
            }
            for entry in glossary.terms(conn)
        ],
        "transcribed_media": len(glossary.media_with_transcripts(conn)),
        "hotword_limit": glossary.HOTWORD_TOKEN_LIMIT,
        "flash": flash,
    }


def autostart_context(*, flash: str | None = None) -> dict:
    """What _settings_autostart.html renders from: the login entry exactly as
    the OS holds it right now.

    Asked on every page load and never remembered. An entry the user deleted
    in regedit, in Finder or with rm has to show as off, and a stored row
    could not do that. Where nothing on this machine is known to start
    MyScribe again there is no switch at all: registering a path that is wrong
    at the next login is worse than registering nothing.

    An OS that will not answer costs this card and not the page. Settings is
    where somebody goes to find out what is wrong with this machine, and a
    registry read that raised would take every other card on it away too.
    """
    try:
        entry = autostart.status()
    except (OSError, ValueError) as refused:
        return {"autostart": None, "autostart_error": str(refused), "flash": flash}
    return {
        "autostart": {
            "on": entry.on,
            "where": entry.where,
            "command": entry.command,
            "can_switch": entry.on or bool(entry.command),
        },
        "autostart_error": None,
        "flash": flash,
    }


def page_context(
    conn: sqlite3.Connection, *, watcher: "watching.Watcher | None" = None
) -> dict:
    """Everything settings.html renders from.

    `watcher` is threaded through for `watch_context` alone: whether a folder
    is being watched live is a fact about the running process, not about the
    database, so it cannot be read back from `conn`.
    """
    return {
        **doctor_context(conn),
        **models_context(),
        **storage_context(),
        **defaults_context(conn),
        **watch_context(conn, watcher=watcher),
        **autostart_context(),
        **cpu_fallback_context(conn),
        **glossary_context(conn),
        **presets_context(conn),
        **ai_ui.settings_context(conn),
        "oob": False,
    }


# --- the browse roots ----------------------------------------------------------------


def parse_roots(text: str | None) -> tuple[Path, ...]:
    """The browse roots a form posted: one absolute, existing directory per
    line, blank lines ignored. A 400 names the first line that is not."""
    roots: list[Path] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        path = Path(line)
        if not path.is_absolute():
            raise HTTPException(
                status_code=400,
                detail=f"browse root {line} is not an absolute path; give the whole path from the drive",
            )
        if not path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"browse root {line} is not a directory on this machine",
            )
        roots.append(path)
    return tuple(roots)


def save_roots(conn: sqlite3.Connection, roots: tuple[Path, ...]) -> None:
    """Store the roots one per line; none at all removes the row, so the
    default root applies again rather than a stored empty string."""
    with db.LOCK:
        if roots:
            conn.execute(
                "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
                (fsbrowse.SETTING_KEY, "\n".join(str(root) for root in roots)),
            )
        else:
            conn.execute("DELETE FROM setting WHERE key=?", (fsbrowse.SETTING_KEY,))
        conn.commit()


# --- routes ------------------------------------------------------------------------------


# The categories of the settings page, in sidebar order: key, label, and the
# one line under the label that says what is in there. One section is shown
# at a time; the key is what `?section=` and the URL hash carry.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("defaults", "Transcription", "Language, model tier, speakers, browse roots"),
    ("watch", "Watch folders", "Folders that transcribe by themselves"),
    ("autostart", "Start at login", "Start MyScribe when you log in"),
    ("glossary", "Glossary", "Names and terms Whisper gets wrong"),
    ("llm", "AI providers", "Ollama, OpenAI, OpenRouter and their keys"),
    ("presets", "Export presets", "Saved export settings"),
    ("machine", "This machine", "Doctor, models on disk, storage"),
)
DEFAULT_SECTION = SECTIONS[0][0]


def opening_section(request: Request) -> str:
    """Which category the page opens on: `?section=<key>`, or the first. An
    unknown key is a hand-edited URL and opens the first, not an error."""
    asked = str(request.query_params.get("section") or "").strip().lower()
    return asked if asked in {key for key, _, _ in SECTIONS} else DEFAULT_SECTION


def _back_to(section: str, anchor: str | None = None) -> Response:
    """The plain-form answer: a 303 to the card that was just saved.

    `?section=` is what opens a category - the radio the page checks from it
    - and a fragment alone cannot: the card it names is `display:none` behind
    Defaults, so `/settings#watch-folders` showed the Defaults form and no
    sign that a folder had been added (TASK-077). The anchor rides along for
    the scroll, once the card is open.
    """
    assert section in {key for key, _, _ in SECTIONS}, section
    where = f"/settings?section={section}"
    if anchor:
        where += f"#{anchor}"
    return RedirectResponse(where, status_code=303)


@router.get("/settings", include_in_schema=False)
def settings_page(request: Request) -> Response:
    conn = request.app.state.conn
    return render(
        request,
        "settings.html",
        sections=SECTIONS,
        section=opening_section(request),
        **page_context(conn, watcher=getattr(request.app.state, "watcher", None)),
    )


@router.post("/settings", include_in_schema=False)
async def save_settings(request: Request) -> Response:
    """Save the defaults and, when the form carries the field, the browse
    roots. Everything is validated before anything is written, so a bad
    root does not half-save the language next to it."""
    conn = request.app.state.conn
    form = await request.form()
    fields = transcribe_dialog._fields(form)
    options = parse_options(fields)
    roots = parse_roots(fields[FIELD_ROOTS]) if FIELD_ROOTS in fields else None

    transcribe_dialog.save_defaults(conn, options)
    if roots is not None:
        save_roots(conn, roots)

    if library._is_htmx(request):
        return render(
            request, "_settings_defaults.html", oob=True, **defaults_context(conn, flash=FLASH_SAVED)
        )
    return _back_to("defaults", "settings-defaults")


@router.post("/settings/hf-token", include_in_schema=False)
async def save_hf_token(request: Request) -> Response:
    """Store the Hugging Face token, or clear it back to the environment.

    Deliberately the same shape as `save_llm_key`: the value is plain text in
    `setting`, it is never rendered back, and nothing here logs it. The
    diarize stage has told users to set this row since it was written
    (`_no_weights_hint` names `hf_token` by key); until now there was no field
    to set it in, and the only routes were `.env` or SQL (TASK-040.06).
    """
    conn = request.app.state.conn
    from scribe.stages import diarize

    fields = transcribe_dialog._fields(await request.form())
    if library._truthy(fields.get("clear")):
        ai_ui.setting_drop(conn, diarize.SETTING_TOKEN)
        return _hf_answer(request, conn, FLASH_HF_CLEARED)

    value = (fields.get("hf_token") or "").strip()
    if not value:
        raise HTTPException(
            status_code=400,
            detail="paste a token to save, or use Clear to fall back to HF_TOKEN in the environment",
        )
    ai_ui.setting_put(conn, diarize.SETTING_TOKEN, value)
    return _hf_answer(request, conn, FLASH_HF_SAVED)


def _hf_answer(request: Request, conn: sqlite3.Connection, flash: str) -> Response:
    if library._is_htmx(request):
        return render(request, "_settings_defaults.html", oob=True, **defaults_context(conn, flash=flash))
    return _back_to("defaults", "settings-defaults")


@router.post("/settings/doctor", include_in_schema=False)
async def run_doctor(request: Request) -> Response:
    """Run the CPU checks now and answer with their table; with ``gpu=1``
    also queue the doctor job for the GPU checks.

    The checks run in a thread: ffmpeg and ffprobe are subprocesses, and the
    event loop has a jobs board to keep answering while they start. A plain
    post is sent back to the page, which runs the checks for itself.
    """
    conn = request.app.state.conn
    form = await request.form()
    fields = transcribe_dialog._fields(form)
    gpu = library._truthy(fields.get(FIELD_GPU))

    flash = None
    if gpu:
        job_id, fresh = queue_gpu_checks(conn)
        flash = (
            f"GPU checks queued as job {job_id}."
            if fresh
            else f"GPU checks are already waiting as job {job_id}."
        )

    if not library._is_htmx(request):
        return _back_to("machine")

    results = await run_in_threadpool(doctor.checks, include_gpu=False)
    response = render(
        request,
        "_doctor_panel.html",
        oob=True,
        **doctor_context(conn, cpu_checks=results, flash=flash),
    )
    if gpu:
        response.headers["HX-Trigger"] = "jobs-changed"
    return response


# --- watch folders --------------------------------------------------------------------------
#
# A folder that ingests by itself is the one door with nobody standing at it,
# so everything knowable is checked here, at the form, where it can be named -
# rather than in the watcher thread, where the only place to say it would be a
# log line nobody reads. The watcher itself starts in the lifespan
# (`scribe.app.create_app`) and picks up a folder added here within one poll
# interval; `scribe/ingest/watching.py` owns the rules about what it takes in.


def _watch_answer(request: Request, conn: sqlite3.Connection, *, flash: str) -> Response:
    """The section re-rendered for htmx; a 303 back to the page otherwise."""
    if not library._is_htmx(request):
        return _back_to("watch", "watch-folders")
    return render(
        request,
        "_settings_watch.html",
        oob=True,
        **watch_context(
            conn, watcher=getattr(request.app.state, "watcher", None), flash=flash
        ),
    )


def parse_watch_path(conn: sqlite3.Connection, raw: str | None) -> Path:
    """The folder a form asked to watch, or a 4xx saying why it will not be.

    Four noes, and each one is a mistake the user can fix from where they are
    standing:

    * not an absolute path that exists as a directory - a typo, or a drive
      that is not mounted, and the watcher would silently watch nothing;
    * outside `fsbrowse`'s roots - the same rule the transcribe dialog's path
      field and `POST /api/media` apply, so all three doors agree about what
      this app may read;
    * inside `paths.DATA_DIR` - the store, the per-job scratch and the
      microphone chunks all live there and all look like media, so watching it
      would have the app ingesting its own working files, forever;
    * already registered - the column is UNIQUE, and two rows over one folder
      would be two ingests of every file that lands in it.
    """
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="give the folder to watch as a full path")
    path = Path(text)
    if not path.is_absolute():
        raise HTTPException(
            status_code=400,
            detail=f"{text} is not an absolute path; give the whole path from the drive",
        )
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"{path} is not a folder on this machine")
    if not fsbrowse.is_allowed(path, fsbrowse.allowed_roots(conn)):
        raise HTTPException(
            status_code=403,
            detail=f"{path} is outside the folders this app may read from; widen them under Settings",
        )
    if watching.in_data_dir(path):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{path} is inside this app's own data directory; watching it would"
                " have MyScribe ingesting its own working files"
            ),
        )
    return path


def add_watched(conn: sqlite3.Connection, raw: str | None, options: TranscribeOptions,
                *, include_video: bool = False) -> tuple[Path, "watching.Preview"]:
    """Watch a folder after the four refusals, or a 4xx saying which one.

    The one function behind both doors that add a folder - this form and the
    first-run setup engine (`scribe.setup`, TASK-089.20) - so the refusals and
    their sentences exist once (ADR-015: an answer given at the start is the
    same answer Settings takes). The fourth refusal is not in
    `parse_watch_path`: the column is UNIQUE, so a folder already registered
    is `watching.add_folder`'s IntegrityError, said here as the 409 the route
    has always answered with.
    """
    path = parse_watch_path(conn, raw)
    try:
        folder_id = watching.add_folder(conn, path, options, include_video=include_video)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"{path} is already being watched") from None
    # Watched "for new recordings": what is already there is recorded and left
    # alone, and said, with a way to take it after all (TASK-107.01).
    left = watching.leave_existing(conn, folder_id, path, include_video=include_video)
    return path, left


def _get_watch_folder(conn: sqlite3.Connection, folder_id: int) -> dict:
    for folder in watching.folders(conn, enabled_only=False):
        if folder["id"] == folder_id:
            return folder
    raise HTTPException(status_code=404, detail=f"no watched folder with id {folder_id}")


@router.post("/settings/watch", include_in_schema=False)
async def add_watch_folder(request: Request) -> Response:
    """Watch a folder, with the options everything found in it gets.

    The options are the transcribe dialog's own fields, validated by
    `parse_options` and stored as `TranscribeOptions.model_dump()` - the
    form's vocabulary, not the job's, so this form can render them back and
    the tier is still mapped to a model at enqueue time (ADR-004).
    """
    conn = request.app.state.conn
    fields = transcribe_dialog._fields(await request.form())
    options = parse_options(fields)
    include_video = fields.get(FIELD_WATCH_VIDEO) == "1"
    _path, left = add_watched(conn, fields.get(FIELD_WATCH_PATH), options, include_video=include_video)
    return _watch_answer(request, conn, flash=watch_added_sentence(left, include_video))


@router.post("/settings/watch/{folder_id}", include_in_schema=False)
async def toggle_watch_folder(folder_id: int, request: Request) -> Response:
    """Switch one folder on or off, keeping its path and its options.

    Off rather than removed is the useful state for a folder on a drive that
    is not always plugged in: the options survive, and so does the row the
    page shows.
    """
    conn = request.app.state.conn
    _get_watch_folder(conn, folder_id)
    fields = transcribe_dialog._fields(await request.form())
    enabled = library._truthy(fields.get(FIELD_WATCH_ENABLED))

    watching.set_enabled(conn, folder_id, enabled)
    return _watch_answer(
        request, conn, flash=FLASH_WATCH_ENABLED if enabled else FLASH_WATCH_DISABLED
    )


@router.post("/settings/watch/{folder_id}/video", include_in_schema=False)
async def switch_watch_video(folder_id: int, request: Request) -> Response:
    """Video on or off for one folder (TASK-107.01): the folder setup added is
    audio only, and this is how its video gets in without removing it."""
    conn = request.app.state.conn
    _get_watch_folder(conn, folder_id)
    fields = transcribe_dialog._fields(await request.form())
    on = fields.get(FIELD_WATCH_VIDEO) == "1"
    watching.set_include_video(conn, folder_id, on)
    return _watch_answer(request, conn, flash=(
        "That folder now takes video files too." if on else "That folder now takes audio files only."))


@router.post("/settings/watch/{folder_id}/existing", include_in_schema=False)
def take_existing_files(folder_id: int, request: Request) -> Response:
    """Transcribe what a folder held when it was added, after all
    (TASK-107.01): the rows that kept it out are forgotten, and the watcher's
    next walk takes those files in with the folder's options."""
    conn = request.app.state.conn
    folder = _get_watch_folder(conn, folder_id)
    taken = watching.take_existing(conn, folder_id)
    watcher = getattr(request.app.state, "watcher", None)
    if watcher is not None:
        watcher.request_reconcile()
    kind = "audio or video file" if folder.get("include_video", 1) else "audio file"
    verb = "it" if taken == 1 else "them"
    return _watch_answer(request, conn, flash=(
        f"{_count(taken, kind)} already in it will be transcribed; the watcher takes {verb} in shortly."))


@router.post("/settings/watch/{folder_id}/delete", include_in_schema=False)
def delete_watch_folder(folder_id: int, request: Request) -> Response:
    """Stop watching a folder. Nothing already in the library is touched."""
    conn = request.app.state.conn
    _get_watch_folder(conn, folder_id)
    watching.remove_folder(conn, folder_id)
    return _watch_answer(request, conn, flash=FLASH_WATCH_REMOVED)


# --- start at login ------------------------------------------------------------------------
#
# One entry in this user's own profile, written and read back by
# `scribe.autostart`. Nothing about it is stored here: the OS is asked on every
# load, so an entry somebody removed by hand shows as off rather than as a row
# insisting it is on. It is functional and not a convenience - a watch folder
# added above does nothing at all while the app is not running.


def _autostart_answer(request: Request, *, flash: str) -> Response:
    """The section re-rendered for htmx; a 303 back to the page otherwise."""
    if not library._is_htmx(request):
        return _back_to("autostart", "start-at-login")
    return render(request, "_settings_autostart.html", oob=True, **autostart_context(flash=flash))


@router.post("/settings/autostart", include_in_schema=False)
async def set_autostart(request: Request) -> Response:
    """Switch the login entry on or off. Off removes exactly that one entry.

    A 409 rather than a quiet no-op when there is nothing to start: the card
    offers no switch in that case, so a post that arrives anyway came from a
    page that is out of date, and the reason belongs in the answer.
    """
    fields = transcribe_dialog._fields(await request.form())
    wanted = library._truthy(fields.get(FIELD_AUTOSTART))
    try:
        if wanted:
            autostart.enable()
        else:
            autostart.disable()
    except autostart.NothingToStart as nothing:
        raise HTTPException(status_code=409, detail=str(nothing)) from None
    except (OSError, ValueError) as refused:
        raise HTTPException(
            status_code=500, detail=f"the login entry could not be changed: {refused}"
        ) from None
    return _autostart_answer(request, flash=FLASH_AUTOSTART_ON if wanted else FLASH_AUTOSTART_OFF)


# --- the CPU switch (TASK-092) ---------------------------------------------------------------
#
# One row, `accel.SETTING_CPU_FALLBACK`, off by default: a machine with an NVIDIA
# card that CUDA cannot reach refuses a job rather than running it thirty times
# slower on the CPU without a word. On writes the row; off deletes it, so off
# and never-asked are one state, as ADR-016 has it for the provider. Reading it
# costs no torch: the web process never asks whether there is a card (ADR-001).

FIELD_CPU_FALLBACK = "enabled"


def cpu_fallback_context(conn: sqlite3.Connection, *, flash: str | None = None) -> dict:
    """What _settings_cpu_fallback.html renders from."""
    return {
        "cpu_fallback": {"on": accel.cpu_fallback_allowed(conn), "label": accel.CPU_FALLBACK_LABEL},
        "flash": flash,
    }


@router.post("/settings/cpu-fallback", include_in_schema=False)
async def set_cpu_fallback(request: Request) -> Response:
    conn = request.app.state.conn
    fields = transcribe_dialog._fields(await request.form())
    wanted = library._truthy(fields.get(FIELD_CPU_FALLBACK))
    with db.LOCK:
        if wanted:
            conn.execute(
                "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
                (accel.SETTING_CPU_FALLBACK, accel.CPU_FALLBACK_ON),
            )
        else:
            conn.execute("DELETE FROM setting WHERE key=?", (accel.SETTING_CPU_FALLBACK,))
        conn.commit()
    flash = FLASH_CPU_FALLBACK_ON if wanted else FLASH_CPU_FALLBACK_OFF
    if not library._is_htmx(request):
        return _back_to("machine", "cpu-fallback")
    return render(request, "_settings_cpu_fallback.html", oob=True, **cpu_fallback_context(conn, flash=flash))


# --- the glossary --------------------------------------------------------------------------
#
# One list of names, spent on both sides of the decode (`scribe.glossary`): as
# `hotwords` before a transcription and as a reversible correction layer after
# it. Nothing here corrects anything - the pass reads a whole transcript and a
# library-wide re-run reads all of them, which is a runner child's work and not
# a request's (ADR-001). This page only edits rows and queues jobs.


def _glossary_answer(request: Request, conn: sqlite3.Connection, *, flash: str) -> Response:
    """The section re-rendered for htmx; a 303 back to the page otherwise."""
    if not library._is_htmx(request):
        return _back_to("glossary", "glossary")
    return render(request, "_glossary.html", oob=True, **glossary_context(conn, flash=flash))


def parse_weight(raw: str | None) -> float:
    """A term's weight as the form sends it; 1.0 when it sends nothing.

    Weight only ever orders the list, and the list is truncated at the hotword
    budget - so a heavier term is one that survives a tight budget, not one the
    decoder believes more. Refused above zero only: a zero or negative weight
    would sort a term below every other and look like a bug.
    """
    text = (raw or "").strip()
    if not text:
        return 1.0
    try:
        weight = float(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{text!r} is not a number") from None
    if weight <= 0:
        raise HTTPException(status_code=400, detail="a weight has to be greater than zero")
    return weight


def queue_recorrect(conn: sqlite3.Connection) -> list[int]:
    """One `correct` job per transcribed media; returns the ids queued.

    A media that already has one waiting is skipped rather than given a second:
    both would read the same glossary and write the same rows, and the second
    would only be the first one's work done twice.
    """
    with db.LOCK:
        pending = {
            row["media_id"]
            for row in conn.execute(
                "SELECT DISTINCT media_id FROM job WHERE type=?"
                " AND status IN ('queued', 'running')",
                (correct.JOB_TYPE,),
            ).fetchall()
        }
    return [
        jobs.enqueue(conn, correct.JOB_TYPE, media_id=media_id)
        for media_id in glossary.media_with_transcripts(conn)
        if media_id not in pending
    ]


@router.post("/settings/glossary", include_in_schema=False)
async def add_glossary_term(request: Request) -> Response:
    """Add one term, with its weight and any misspellings already known.

    The variants field is comma-separated because that is how a person writes
    a short list; they are looked for by the correction pass and deliberately
    kept out of the hotwords, where they would bias the decoder towards the
    very spellings we are trying to get rid of.
    """
    conn = request.app.state.conn
    fields = transcribe_dialog._fields(await request.form())
    text = (fields.get(FIELD_TERM) or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="give the term to add")
    variants = [part.strip() for part in (fields.get(FIELD_VARIANTS) or "").split(",")]

    try:
        glossary.add(conn, text, weight=parse_weight(fields.get(FIELD_WEIGHT)), variants=variants)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"{text} is already in the glossary") from None
    return _glossary_answer(request, conn, flash=FLASH_TERM_ADDED)


@router.post("/settings/glossary/import", include_in_schema=False)
async def import_glossary(request: Request) -> Response:
    """A pasted list, one term per line. Terms already there keep their weights."""
    conn = request.app.state.conn
    fields = transcribe_dialog._fields(await request.form())
    added = glossary.import_terms(conn, fields.get(FIELD_TERM_LIST) or "")
    return _glossary_answer(
        request, conn, flash=f"{added} term(s) added; anything already listed was left as it was."
    )


@router.post("/settings/glossary/{term_id}/delete", include_in_schema=False)
def delete_glossary_term(term_id: int, request: Request) -> Response:
    """Remove one term. The corrections it already made stay until the next
    re-run, which is the button below the list - deleting them here would mean
    reading every transcript in the library inside this request."""
    conn = request.app.state.conn
    if not glossary.remove(conn, term_id):
        raise HTTPException(status_code=404, detail=f"no glossary term with id {term_id}")
    return _glossary_answer(request, conn, flash=FLASH_TERM_REMOVED)


@router.post("/settings/glossary/recorrect", include_in_schema=False)
def recorrect_library(request: Request) -> Response:
    """Re-apply the glossary to every transcript there is, as one job each.

    One job per media rather than one job for the library: a failure then costs
    one recording rather than all of them, and the jobs board shows progress
    that means something. Each job replaces its run's whole correction layer,
    so this is also how a term deleted from the list stops changing anything.
    """
    conn = request.app.state.conn
    queued = queue_recorrect(conn)
    flash = (
        f"Re-running corrections on {len(queued)} recording(s)."
        if queued
        else "Nothing to re-run: every transcript already has a correction job waiting."
    )
    response = _glossary_answer(request, conn, flash=flash)
    if library._is_htmx(request):
        response.headers["HX-Trigger"] = "jobs-changed"
    return response


# --- export presets ------------------------------------------------------------------------


def _presets_answer(
    request: Request, conn: sqlite3.Connection, *, flash: str, selected: str | None = None
) -> Response:
    """The section re-rendered for htmx - or, when the post came from the
    export dialog's save button, its preset select with ``selected``
    chosen; a 303 back to the page for a plain post."""
    if not library._is_htmx(request):
        return _back_to("presets", "settings-presets")
    if request.headers.get("HX-Target") == exports_ui.PRESET_SELECT_ID:
        return render(
            request,
            "_export_presets.html",
            presets=exports_ui.preset_choices(conn),
            selected=selected,
            preset_select_id=exports_ui.PRESET_SELECT_ID,
        )
    return render(request, "_settings_presets.html", oob=True, **presets_context(conn, flash=flash))


@router.post("/settings/presets", include_in_schema=False)
async def save_preset(request: Request) -> Response:
    """Save a preset: ``name`` plus the export option fields, or ``name``
    plus ``options`` as JSON. Validated whole before the row is written."""
    conn = request.app.state.conn
    form = await request.form()
    options = exports_ui.preset_options_from(form)
    name = exports_ui.save_preset(
        conn, transcribe_dialog._fields(form).get(exports_ui.PRESET_NAME_FIELD), options
    )
    return _presets_answer(request, conn, flash=FLASH_PRESET_SAVED, selected=name)


@router.post("/settings/presets/{preset_id}/delete", include_in_schema=False)
def delete_preset(preset_id: int, request: Request) -> Response:
    conn = request.app.state.conn
    exports_ui.delete_preset(conn, preset_id)
    return _presets_answer(request, conn, flash=FLASH_PRESET_DELETED)


# --- AI providers ---------------------------------------------------------------------------
#
# The rows are read and written through `scribe.web.ai_ui`, the same module the
# transcript rail asks for its provider list, so the page and the panel cannot
# disagree about what the default is. Nothing here calls a model (ADR-001):
# `available()` answers from a key or a loopback probe, and `models()` is a
# listing, asked for only when the user presses Refresh.


def _llm_answer(request: Request, conn: sqlite3.Connection, *, flash: str) -> Response:
    """The section re-rendered for htmx; a 303 back to the page otherwise."""
    if not library._is_htmx(request):
        return _back_to("llm", "llm-providers")
    return render(request, "_settings_llm.html", oob=True, **ai_ui.settings_context(conn, flash=flash))


@router.post("/settings/llm", include_in_schema=False)
async def save_llm_defaults(request: Request) -> Response:
    """The default provider, and one model per provider.

    One model row per provider rather than a single "default model": model ids
    are provider-scoped, so a shared row would name a model that is a 404 the
    moment the provider changes (`base.retarget` exists for the same reason).
    """
    conn = request.app.state.conn
    fields = transcribe_dialog._fields(await request.form())

    # Ollama's address (TASK-100), checked before anything is written: an
    # address outside the local network refuses the whole Save, so the page
    # never ends up half saved around a refusal.
    host_row: str | None = None
    if ai_ui.OLLAMA_HOST_FIELD in fields:
        typed = (fields.get(ai_ui.OLLAMA_HOST_FIELD) or "").strip()
        if typed:
            try:
                host_row = ollama.normalise_host(typed)
                ollama.OllamaProvider(None, host=host_row)  # the local-network check
            except ValueError as exc:
                return _llm_answer(request, conn, flash=str(exc))
        else:
            host_row = ""

    wanted = (fields.get("provider") or "").strip()
    if wanted:
        if wanted not in ai_ui.llm.PROVIDERS:
            raise HTTPException(status_code=400, detail=f"unknown LLM provider {wanted!r}")
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, wanted)

    for name in ai_ui.llm.PROVIDERS:
        field = f"{ai_ui.MODEL_FIELD_PREFIX}{name}"
        custom = f"{ai_ui.CUSTOM_MODEL_FIELD_PREFIX}{name}"
        if field not in fields and custom not in fields:
            continue
        # A typed id wins over the dropdown's pick: the box is for the model
        # the fetched list does not have (TASK-054). No check against the
        # list either way - a day-old list must not forbid a model that exists.
        model = (fields.get(custom) or "").strip() or (fields.get(field) or "").strip()
        key = ai_ui.MODEL_SETTING_PREFIX + name
        if model:
            ai_ui.setting_put(conn, key, model)
        else:  # blank means "whatever the provider's own default is"
            ai_ui.setting_drop(conn, key)

    # The form sends a hidden `0` ahead of the checkbox and `_fields` keeps the
    # last value, so an unticked box arrives as "0" rather than as silence -
    # the same convention the transcribe dialog uses for its checkboxes.
    if ai_ui.PRIVATE_DEFAULT_FIELD in fields:
        ai_ui.set_private_default(
            conn, library._truthy(fields[ai_ui.PRIVATE_DEFAULT_FIELD])
        )

    # Empty is "this computer": the row goes, and the provider's default is
    # this machine again (no row, not a row holding the default).
    if host_row == "":
        ai_ui.setting_drop(conn, ollama.SETTING_HOST)
    elif host_row:
        ai_ui.setting_put(conn, ollama.SETTING_HOST, host_row)

    return _llm_answer(request, conn, flash=ai_ui.FLASH_LLM_SAVED)


@router.post("/settings/llm/{provider}/key", include_in_schema=False)
async def save_llm_key(provider: str, request: Request) -> Response:
    """Store a key for one provider, or clear it back to the environment.

    The value is written to `setting` as plain text and is never read back to
    the page - `_settings_llm.html` shows dots and the *source name* instead.
    Nothing here logs it, and it appears in no response, error or event.
    """
    conn = request.app.state.conn
    if provider not in ai_ui.llm.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown LLM provider {provider!r}")
    fields = transcribe_dialog._fields(await request.form())
    row = llm_base.setting_key(provider)

    if library._truthy(fields.get("clear")):
        ai_ui.setting_drop(conn, row)
        return _llm_answer(request, conn, flash=ai_ui.FLASH_KEY_CLEARED)

    value = (fields.get("key") or "").strip()
    if not value:
        raise HTTPException(
            status_code=400,
            detail="paste a key to save, or use Clear to fall back to the environment",
        )
    ai_ui.setting_put(conn, row, value)
    return _llm_answer(request, conn, flash=ai_ui.FLASH_KEY_SAVED)


@router.post("/settings/llm/{provider}/models", include_in_schema=False)
async def refresh_llm_models(provider: str, request: Request) -> Response:
    """Ask a provider what models it has, and remember the answer.

    Asked for, never automatic: OpenRouter's list is 423 ids over the internet,
    and a settings page that fetched it on every load would wait on somebody
    else's DNS to draw a dropdown. The list is stored, so every later render
    populates the field from a row; a provider that cannot answer leaves the
    field as free text and the reason in the flash, which is the honest state -
    this app does not know what that endpoint has.

    Only the models you can hold a conversation with are stored (TASK-089.06).
    Ollama serves embedding models from the same endpoint - five of the eight
    on the machine this was written for - and an embedder saved as the chat
    model shows green everywhere until the first summary fails inside a job.
    `chat_models()` is asked rather than `models()`, and every provider answers
    it: the base class returns the whole list, so there is no `if provider ==`
    here and nothing changes for a cloud provider.

    In a thread, because for a cloud provider this is a request over the
    network and the event loop has a jobs board to keep answering - and because
    for a daemon too old to report capabilities this asks once per model.
    """
    conn = request.app.state.conn
    if provider not in ai_ui.llm.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown LLM provider {provider!r}")

    instance = ai_ui.llm.PROVIDERS[provider](conn)
    try:
        found = await run_in_threadpool(instance.chat_models)
    except llm_base.LlmError as exc:
        return _llm_answer(request, conn, flash=f"{provider}: {exc}")
    if found is None:
        # "It would not say" is not "it has none", and storing an empty list
        # would leave the dropdown claiming this endpoint offers nothing.
        return _llm_answer(
            request,
            conn,
            flash=f"{provider}: answered, but would not say which models can chat; field left as free text.",
        )
    ai_ui.remember_models(conn, provider, found)
    return _llm_answer(request, conn, flash=f"{provider}: {len(found)} model(s) offered.")


@router.post("/settings/llm/{provider}/test", include_in_schema=False)
def test_llm_provider(provider: str, request: Request) -> Response:
    """Ask this provider to say one word, as a job.

    The only question the rest of this table cannot answer: `available()` says
    a key was found and `models()` says a list came back, and neither has ever
    completed anything - so a stopped daemon, a stale environment key
    outranking the one just typed, and a plausible model id that 404s all pass
    both and fail the first real request.

    Nothing is asked of a provider here (ADR-001): this writes a `job` row and
    the runner child makes the call, exactly as a rail action does. The result
    lands in a `setting` row and the row below the button says how it went;
    the jobs board carries the failure, because `llm_stage.probe_store` records
    the verdict before it raises.
    """
    conn = request.app.state.conn
    if provider not in ai_ui.llm.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown LLM provider {provider!r}")

    job_id, fresh = ai_ui.queue_provider_test(conn, provider)
    flash = (
        f"{provider}: test queued as job {job_id}."
        if fresh
        else f"{provider}: a test is already waiting as job {job_id}."
    )
    response = _llm_answer(request, conn, flash=flash)
    if library._is_htmx(request):
        response.headers["HX-Trigger"] = "jobs-changed"
    return response
