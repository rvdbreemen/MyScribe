"""Receive from phone: the laptop's side of the door (TASK-096, ADR-022).

The door (`phone_door.Door`) is a listener of its own on the LAN. This module
is how the app holds it: one `Door` on ``app.state.phone_door``, built closed
when the app is built and closed again when the app stops, and four routes
on the app itself - so behind `scribe.guard`, like every other route: only
this machine's own browser, on this app's own pages, can open or close it.

    GET  /phone            the panel (a fragment for the dialog, a page otherwise)
    POST /phone/open       open it, or show the opening that is already there
    POST /phone/close      close it
    GET  /phone/countdown  the minutes left, polled by the panel once a second

The control sits in the library's toolbar beside Transcribe and Record,
because it is a third way a recording arrives - not a setting.

Open and close are plain ``def`` routes: they run in the threadpool, where
zeroconf's blocking register and unregister are allowed (it refuses on an
event loop) and where a second or two of mDNS probing does not hold up
anything else.

What the door accepts goes through `transcribe_dialog.file_upload`, the
helper the laptop's own upload uses: same store, same dedupe, same job, with
the stored defaults (`read_defaults`) and no folder - the phone does not get
to choose options, and does not rewrite the laptop's defaults either.

The QR code carries the IP-address URL, not the myscribe.local one. It is
scanned, not typed, and the number needs nothing from mDNS - which is exactly
the part the firewall prompt or another responder on port 5353 can break.
The name is on the panel for typing.
"""

from __future__ import annotations

import sys

import segno
from fastapi import APIRouter, Request
from markupsafe import Markup
from starlette.responses import Response

from scribe.web import phone_door, render, transcribe_dialog
from scribe.web.library import _is_htmx

router = APIRouter()

STOP_POLLING = 286  # htmx stops an `every` trigger on this status

# One sentence for the panel, from the measurement in TASK-096's notes. The
# socket belongs to the base interpreter (C:\Python312\python.exe), not the
# venv's launcher, and a Windows that has no rule for it asks on the laptop
# the first time it listens on a LAN address. An allow rule covers the
# network kinds it names, and this laptop's own Wi-Fi is filed as Public, so a
# rule for Private alone would leave the phone outside. (Here the rules were
# already there, for Private and Public, and no prompt appeared.)
FIREWALL_NOTE = (
    "If Windows Firewall asks on this laptop whether Python may use the network,"
    " allow it, and tick the kind of network this Wi-Fi is: Windows often files a"
    " home Wi-Fi as Public, and a rule for Private alone leaves the phone outside."
    " Windows keeps that answer as a rule after the door closes; MyScribe adds no"
    " firewall rule itself."
)


def accept_for(app):
    """The door's ``accept``: a file becomes a recording with a queued job."""

    def accept(stream, name: str) -> None:
        conn = app.state.conn
        params = transcribe_dialog.read_defaults(conn).to_params()
        transcribe_dialog.file_upload(conn, stream, name, params=params, folder_id=None, via="phone")

    return accept


def build_door(app) -> phone_door.Door:
    """The door as it ships: the LAN address, port 4243, zeroconf. Closed.

    The classes are looked up here, at call time, so a test can stand a spy
    in for them before the app is built.
    """
    return phone_door.Door(
        accept_for(app),
        address_picker=phone_door.lan_address,
        publisher=phone_door.ZeroconfPublisher(),
        port=phone_door.DEFAULT_PORT,
        listener_factory=phone_door.UvicornListener,
    )


def install(app) -> None:
    app.state.phone_door = build_door(app)


def _door(request: Request) -> phone_door.Door:
    return request.app.state.phone_door


def _clock(seconds: float) -> str:
    whole = int(seconds + 0.999)  # 14:59.2 left shows as 15:00, never 0:00 while open
    return f"{whole // 60}:{whole % 60:02d}"


def _qr(url: str) -> Markup:
    # segno's own SVG of a URL this module built: no user content inside, so
    # it is marked safe here and nowhere else.
    return Markup(segno.make(url, error="m").svg_inline(scale=4))


def _panel_context(door: phone_door.Door, error: str | None = None) -> dict:
    opening = door.opening
    return {
        "opening": opening,
        "qr": _qr(opening.ip_url) if opening else None,
        "countdown": _clock(door.remaining()) if opening else None,
        "received": list(door.received) if opening else [],
        "error": error,
        "firewall_note": FIREWALL_NOTE if sys.platform == "win32" else None,
        "minutes": int(phone_door.OPEN_SECONDS // 60),
    }


def _panel(request: Request, error: str | None = None, status: int = 200) -> Response:
    ctx = _panel_context(_door(request), error)
    if _is_htmx(request):
        response = render(request, "_phone_panel.html", **ctx)
    else:
        response = render(request, "phone.html", **ctx)
    response.status_code = status
    return response


@router.get("/phone", include_in_schema=False)
def phone(request: Request) -> Response:
    ctx = _panel_context(_door(request))
    if _is_htmx(request):
        return render(request, "phone_dialog.html", **ctx)
    return render(request, "phone.html", **ctx)


@router.post("/phone/open", include_in_schema=False)
def open_door(request: Request) -> Response:
    try:
        _door(request).open()
    except phone_door.DoorError as exc:
        return _panel(request, error=str(exc))
    return _panel(request)


@router.post("/phone/close", include_in_schema=False)
def close_door(request: Request) -> Response:
    _door(request).close("closed on the laptop")
    return _panel(request)


@router.get("/phone/countdown", include_in_schema=False)
def countdown(request: Request) -> Response:
    door = _door(request)
    if not door.close_if_due():
        return render(
            request, "_phone_countdown.html",
            countdown=_clock(door.remaining()), received=list(door.received),
        )
    response = render(request, "_phone_panel.html", **_panel_context(door))
    response.status_code = STOP_POLLING
    response.headers["HX-Retarget"] = "#phone-panel"
    response.headers["HX-Reswap"] = "outerHTML"
    return response
