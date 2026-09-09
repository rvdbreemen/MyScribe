"""The HTML side of the app: templates, static files, and the page routers.

Server-rendered Jinja2 pages with htmx for the parts that update in place.
htmx is vendored under `scribe/static/` and there is no build step, so the
whole UI keeps working on a laptop with the Wi-Fi off - which is where a
transcription tool that runs its own GPU tends to be used.

Three things are decided here, once, for every page:

* **Autoescape is on, unconditionally.** Transcript text, titles, filenames
  and speaker names are all user content, and the template environment is the
  one place the escaping can be guaranteed rather than remembered. No template
  under `scribe/templates/` uses `|safe` on any of them.
* **The web process never loads a model** (ADR-001). Nothing under this
  package imports a speech, tensor or diarization runtime; the routers read
  rows and render them, and everything heavier is a job the runner child does.
* **Every page goes through `render()`**, which adds the context the base
  template expects (`version`, `nav`, `now`) so a router never has to.

`mount()` is the single entry point `scribe.app.create_app` calls; each
screen's router is added to it as it lands.
"""

from __future__ import annotations

import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path

import jinja2
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

import scribe
from scribe.render import confidence_band, format_ts

PACKAGE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

# Top navigation, in the order it is shown. Label, path.
NAV: tuple[tuple[str, str], ...] = (
    ("Library", "/"),
    ("Jobs", "/jobs"),
    ("Log", "/logs"),
    ("Settings", "/settings"),
)

# Python's mimetypes module reads the Windows registry on import, and a
# machine where some installer once registered .js as application/javascript
# (or worse, text/plain) would serve htmx under that type. Browsers refuse to
# run a script served as text/plain, so the type is pinned rather than trusted.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

templates = Jinja2Templates(
    env=jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
)


def asset_url(name: str) -> str:
    """`/static/<name>` with the file's own modification time on the end.

    There is no build step here to fingerprint a file with, and StaticFiles
    sends an ETag and a Last-Modified but no Cache-Control - which leaves a
    browser free to decide for itself how long it may keep the copy it has.
    Chrome decides generously: on 2026-09-05 a restyled page kept rendering
    the previous stylesheet while the new one sat on the wire, and the only
    cure was a hard refresh nobody should have to know about.

    The mtime is a stat per render (about 15 microseconds), not per file in a
    walk, and it means editing app.css is visible on the next reload without
    restarting the app. A file that is not there yields a bare path rather
    than an error: a missing asset is the browser's problem to report, not a
    reason for a 500.
    """
    try:
        stamp = int((STATIC_DIR / name).stat().st_mtime)
    except OSError:
        return f"/static/{name}"
    return f"/static/{name}?v={stamp}"


# What a date these two cannot render becomes. Both raise on an epoch the
# platform's C library will not take - on Windows that is every negative one,
# measured here 2026-09-09: `time.localtime(-1)` and, a little further out,
# `datetime.fromtimestamp(-31536000, tz=utc)` both give OSError [Errno 22].
# Linux takes them, which is exactly why this can pass review on one machine
# and take a page down on the other.
#
# App-generated epochs are never negative. A stranger's are: a feed's pubDate
# reaches these filters through yt-dlp's `unified_timestamp`, which clamps
# nothing, and it takes no archive to get there - epoch zero stamped with any
# offset east of Greenwich is already below zero (`Thu, 01 Jan 1970 00:00:00
# +0100` -> -3600). One such episode in a 50-item listing used to 500 the
# whole panel, on the one route whose contract is that failures are content.
#
# So an undisplayable date renders empty, the same answer a missing one
# already gets. The row keeps its title, its checkbox and its import.
_UNRENDERABLE = (OSError, OverflowError, ValueError)


def localtime(epoch: float | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """A unix epoch as this machine's local wall-clock time; None renders empty."""
    if not epoch:
        return ""
    try:
        return time.strftime(fmt, time.localtime(float(epoch)))
    except _UNRENDERABLE:
        return ""


def isotime(epoch: float | None) -> str:
    """A unix epoch as an ISO 8601 UTC instant, for <time datetime="...">."""
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except _UNRENDERABLE:
        return ""


# The filters every template may use. Media time (a position in a
# recording) and wall time (when something happened) are different things
# and get different names, so a template cannot format one as the other by
# accident.
templates.env.filters["clock"] = format_ts
templates.env.filters["localtime"] = localtime
templates.env.filters["isotime"] = isotime
# A word's probability as its confidence band, for the transcript's tint.
templates.env.filters["band"] = confidence_band


def nav_for(path: str) -> list[dict]:
    """The nav items with the one matching ``path`` marked active.

    The library is the home of everything that is not another section:
    `/search` and `/media/…` belong to it as much as `/` does.
    """
    elsewhere = any(path.startswith(href) for _, href in NAV if href != "/")
    return [
        {
            "label": label,
            "href": href,
            "active": (not elsewhere) if href == "/" else path.startswith(href),
        }
        for label, href in NAV
    ]


def render(request: Request, name: str, **ctx) -> Response:
    """Render ``name`` with the base-template context plus ``ctx``.

    A caller that needs a header on the way out (an `HX-Trigger`, say) sets
    it on the returned response; the context is the only thing this adds.
    """
    context = {
        "version": scribe.__version__,
        "asset_url": asset_url,
        "nav": nav_for(request.url.path),
        "now": time.time(),
        **ctx,
    }
    return templates.TemplateResponse(request, name, context)


def mount(app: FastAPI) -> None:
    """Attach static files and every page router to ``app``.

    The routers import ``render`` from this package, so they are imported
    here, once the package is fully defined, rather than at the top.
    """
    from scribe.web import (
        ai_ui,
        exports_ui,
        ingest_ui,
        jobs_ui,
        library,
        logs_ui,
        settings,
        transcribe_dialog,
        transcript,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(library.router)
    app.include_router(transcribe_dialog.router)
    app.include_router(ingest_ui.router)
    app.include_router(jobs_ui.router)
    app.include_router(transcript.router)
    app.include_router(exports_ui.router)
    app.include_router(ai_ui.router)
    app.include_router(settings.router)
    app.include_router(logs_ui.router)
