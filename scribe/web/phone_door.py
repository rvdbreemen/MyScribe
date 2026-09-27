"""Receive from phone: a door on the local network that takes one kind of thing.

MyScribe binds 127.0.0.1, has no login, and refuses any Host but its own
(`scribe.guard`). A phone on the same Wi-Fi cannot reach it, and must not:
the library behind it answers anybody who can. This module is the one
exception, and it is made as small as it can be (ADR-022):

* **A second listener, not the app.** Its own ASGI callable served by its own
  uvicorn on its own thread and port (4243), bound to the laptop's LAN
  address only. It knows nothing about the library: it serves an upload form
  and hands an accepted file to the ``accept`` callback it was built with.
  No route of the main app exists here, so none can be reached through it.
* **Closed until somebody on the laptop opens it**, and closed again after
  `OPEN_SECONDS` (15 minutes) or on request. Closing stops the server and
  releases the port. Nothing is written down about it, so a restart of
  MyScribe finds it closed.
* **One secret per opening.** `secrets.token_urlsafe(32)` is the whole path
  (``/<secret>/``); anything else is a 404 before a byte of the body is read,
  and the comparison is `hmac.compare_digest`. The secret is never logged: the
  app log records that the door opened, where and for how long, never the
  URL - and uvicorn's access log, which would print the path, is off for this
  server alone (`_QuietH11`, because uvicorn's own ``access_log=False`` empties
  the process-wide ``uvicorn.access`` logger and would silence the app's log
  as well).
* **Audio and video only, up to 4 GiB.** The extension has to be one
  `probe.MEDIA_EXTENSIONS` knows - the same list `POST /api/media` holds a
  path to - and the bytes are counted as they arrive, so a body without a
  Content-Length (or with a false one) is cut off at the limit too. The laptop
  upload has no limit of its own to borrow; 4 GiB takes an hour of 4K iPhone
  video in HEVC with room to spare and keeps a stranger on the LAN from filling
  the disk in the fifteen minutes.

The phone page is plain HTML: a form, a file input, a button, and a sentence
back. No script, no stylesheet link, nothing from the internet - Safari on an
iPhone shows it as it is.

Three seams keep the tests off the network: ``address_picker`` (which LAN
address to bind and show), ``publisher`` (mDNS; `ZeroconfPublisher` for
real), and ``clock`` (the deadline is measured on it, and the watchdog thread
closes the door when it passes). `SHORTER_ENV` can make the door close
sooner - a real run's proof that it closes on time - and never later.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Callable, Mapping, Protocol

import uvicorn
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse
from starlette.types import Receive, Scope, Send
from uvicorn.protocols.http.h11_impl import H11Protocol

from scribe import applog
from scribe.stages import probe

DEFAULT_PORT = 4243
OPEN_SECONDS = 15 * 60
MDNS_HOST = "myscribe.local"
MAX_UPLOAD_BYTES = 4 * 1024**3
FILE_FIELD = "file"

# token_urlsafe(32): 32 random bytes, 43 characters in the URL.
SECRET_BYTES = 32

# Seconds, and only ever fewer than OPEN_SECONDS. For a real run that has to
# watch the door close without waiting a quarter of an hour.
SHORTER_ENV = "SCRIBE_PHONE_DOOR_SECONDS"

# The address a UDP socket is "connected" to, to learn which interface the
# default route leaves by. TEST-NET-1 (RFC 5737): routed like any public
# address, owned by nobody, and a UDP connect sends no packet at all.
ROUTE_PROBE = ("192.0.2.1", 9)

# How long a Close waits for an upload still in flight before cutting it off.
GRACE_SECONDS = 2.0

Accept = Callable[[BinaryIO, str], object]


def open_seconds(environ: Mapping[str, str] = os.environ) -> float:
    """How long the door stays open: 15 minutes, or fewer if asked."""
    raw = environ.get(SHORTER_ENV)
    try:
        wanted = float(raw) if raw else OPEN_SECONDS
    except ValueError:
        return float(OPEN_SECONDS)
    if not 0 < wanted <= OPEN_SECONDS:
        return float(OPEN_SECONDS)
    return wanted


def lan_address(socket_factory=socket.socket) -> str | None:
    """The IPv4 address of the interface the default route uses, or None.

    Connecting a UDP socket sends nothing; it only makes the OS choose the
    source address it would use for that destination, which is the address
    of the interface a phone on the same Wi-Fi reaches this laptop on - not a
    WSL or Hyper-V adapter, which never carries the default route. A laptop
    with no route (Wi-Fi off) or only a self-assigned 169.254 address has
    nothing a phone could reach.
    """
    sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(ROUTE_PROBE)
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
        return None
    return address


class Publisher(Protocol):
    def publish(self, host: str, address: str, port: int) -> None: ...

    def withdraw(self) -> None: ...


class ZeroconfPublisher:
    """``myscribe.local`` over mDNS, on the one interface the door binds.

    Registered as the host of an ``_http._tcp`` service, which is how
    python-zeroconf answers address queries for a name. Blocking: zeroconf's
    synchronous calls refuse to run on an event loop, and the laptop's routes
    that open and close the door are plain ``def`` routes for that reason.
    """

    SERVICE_TYPE = "_http._tcp.local."
    SERVICE_NAME = "MyScribe._http._tcp.local."

    def __init__(self, zeroconf_factory=None) -> None:
        self._factory = zeroconf_factory
        self._zc = None
        self._info = None

    def publish(self, host: str, address: str, port: int) -> None:
        from zeroconf import ServiceInfo, Zeroconf

        factory = self._factory or Zeroconf
        info = ServiceInfo(
            self.SERVICE_TYPE,
            self.SERVICE_NAME,
            port=port,
            server=f"{host}.",
            addresses=[socket.inet_aton(address)],
        )
        zc = factory(interfaces=[address])
        try:
            zc.register_service(info)
        except BaseException:
            zc.close()
            raise
        self._zc, self._info = zc, info

    def withdraw(self) -> None:
        zc, info = self._zc, self._info
        self._zc = self._info = None
        if zc is None:
            return
        try:
            zc.unregister_service(info)
        finally:
            zc.close()


class DoorError(Exception):
    """Why the door could not open, as a sentence for the laptop's panel."""


@dataclass(frozen=True)
class Opening:
    secret: str
    address: str
    port: int
    opened_at: float
    closes_at: float
    mdns_error: str | None

    @property
    def path(self) -> str:
        return f"/{self.secret}/"

    @property
    def ip_url(self) -> str:
        return f"http://{self.address}:{self.port}{self.path}"

    @property
    def mdns_url(self) -> str:
        return f"http://{MDNS_HOST}:{self.port}{self.path}"


class _QuietH11(H11Protocol):
    """uvicorn's h11 protocol without the access log.

    The path is the secret, and the access log prints paths. uvicorn decides
    per connection whether to log (``hasHandlers()`` on the shared
    ``uvicorn.access`` logger, which the app's own server has given one), so
    this server's connections say no for themselves and the app's keep
    logging as they did.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.access_log = False


class UvicornListener:
    """One uvicorn server on a socket the door bound, on a thread of its own."""

    def __init__(self, sock: socket.socket, app, *, grace: float = GRACE_SECONDS) -> None:
        self._sock = sock
        config = uvicorn.Config(
            app,
            http=_QuietH11,
            ws="none",
            lifespan="off",
            log_config=None,
            server_header=False,
            timeout_graceful_shutdown=grace,
        )
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 10.0) -> None:
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._sock]},
            name="scribe-phone-door",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise DoorError("The door's listener did not start; the app log may say why.")
            time.sleep(0.01)

    def stop(self, timeout: float = 15.0) -> None:
        self._server.should_exit = True
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._sock.close()


class _TooLarge(Exception):
    pass


def _basename(filename: str) -> str:
    return PurePosixPath(str(filename).replace("\\", "/")).name


class Door:
    """The door: closed, or open on one address, port and secret until a deadline."""

    def __init__(
        self,
        accept: Accept,
        *,
        address_picker: Callable[[], str | None] = lan_address,
        publisher: Publisher | None = None,
        port: int = DEFAULT_PORT,
        clock: Callable[[], float] = time.monotonic,
        seconds: float | None = None,
        poll: float = 1.0,
        max_bytes: int = MAX_UPLOAD_BYTES,
        listener_factory=UvicornListener,
    ) -> None:
        self._accept = accept
        self._pick = address_picker
        self._publisher = publisher if publisher is not None else ZeroconfPublisher()
        self._port = port
        self._clock = clock
        self._seconds = seconds
        self._poll = poll
        self.max_bytes = max_bytes
        self._listener_factory = listener_factory
        self._lock = threading.Lock()
        self._opening: Opening | None = None
        self._listener = None
        self._stop_watch: threading.Event | None = None
        self._closed = threading.Event()
        self._closed.set()
        self.received: list[str] = []
        # What ``accept`` answered for each file, in order (the laptop's side
        # keeps the media id there, to name the recording's job).
        self.results: list[object] = []
        # Why the door last closed: "received", "timeout", "app stopped", ...
        self.closed_reason: str | None = None

    # --- state -----------------------------------------------------------------

    @property
    def opening(self) -> Opening | None:
        return self._opening

    def is_open(self) -> bool:
        return self._opening is not None

    def remaining(self) -> float:
        opening = self._opening
        if opening is None:
            return 0
        return max(0, opening.closes_at - self._clock())

    def wait_closed(self, timeout: float) -> bool:
        return self._closed.wait(timeout)

    def admits(self, secret: str, path: str) -> bool:
        """Whether ``path`` is the live opening's path, made with ``secret``.

        ``secret`` is the one the serving app was built with, so an app left
        over from an earlier opening admits nothing. Past the deadline nothing
        is admitted, whether or not the watchdog has looked yet.
        """
        opening = self._opening
        if opening is None or opening.secret != secret:
            return False
        if self._clock() >= opening.closes_at:
            return False
        return hmac.compare_digest(path.encode("utf-8"), opening.path.encode("utf-8"))

    # --- open and close ------------------------------------------------------------

    def open(self) -> Opening:
        """Open the door, or return the opening that is already there.

        A second press keeps the secret the phone may already have scanned.
        """
        with self._lock:
            if self._opening is not None:
                return self._opening
            address = self._pick()
            if address is None:
                raise DoorError(
                    "This laptop has no network address a phone could reach."
                    " Connect it to the same Wi-Fi as the phone and try again."
                )
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((address, self._port))
                sock.listen(16)
            except OSError as exc:
                sock.close()
                raise DoorError(
                    f"Port {self._port} on {address} is in use by another program"
                    f" ({exc.strerror or exc}). Close it and try again."
                ) from exc
            port = sock.getsockname()[1]
            secret = secrets.token_urlsafe(SECRET_BYTES)
            listener = self._listener_factory(sock, _DoorApp(self, secret))
            listener.start()
            mdns_error = None
            try:
                self._publisher.publish(MDNS_HOST, address, port)
            except Exception as exc:  # any failure: the IP address still works
                mdns_error = f"{type(exc).__name__}: {exc}"
            now = self._clock()
            seconds = self._seconds if self._seconds is not None else open_seconds()
            opening = Opening(secret, address, port, now, now + seconds, mdns_error)
            self._opening = opening
            self._listener = listener
            self.received = []
            self.results = []
            self.closed_reason = None
            self._closed.clear()
            stop = threading.Event()
            self._stop_watch = stop
            threading.Thread(
                target=self._watch, args=(opening, stop), name="scribe-phone-door-timer", daemon=True
            ).start()
        applog.log("phone.open", address=address, port=port, seconds=seconds,
                   mdns=mdns_error is None, mdns_error=mdns_error)
        return opening

    def close(self, reason: str = "closed", *, only: Opening | None = None) -> None:
        """Stop the listener, withdraw the name, forget the secret. Idempotent.

        ``only``: close only if that opening is still the live one - the
        watchdog of an opening that was already closed by hand and reopened
        must not close the new one.

        Returns when the door is closed, also when another thread was already
        closing it (the door closes itself after a file): the listener is
        stopped and ``phone.close`` logged by then.
        """
        with self._lock:
            opening = self._opening
            if only is not None and opening is not only:
                return
            if opening is not None:
                self._opening = None
                self.closed_reason = reason
                listener, self._listener = self._listener, None
                stop, self._stop_watch = self._stop_watch, None
                received = len(self.received)
        if opening is None:
            # Closed already, or being closed by another thread right now.
            self._closed.wait(20)
            return
        if stop is not None:
            stop.set()
        try:
            if listener is not None:
                listener.stop()
        finally:
            try:
                if opening.mdns_error is None:
                    self._publisher.withdraw()
            except Exception as exc:
                applog.log("phone.mdns_withdraw_failed", level="warn", error=str(exc))
            # Logged first: whoever waits on _closed may read the log next.
            applog.log("phone.close", reason=reason, received=received)
            self._closed.set()

    def close_if_due(self) -> bool:
        """Close the door if its time is up; True when it is closed now.

        The watchdog calls this once a second, and so does anybody about to
        say the door is closed: the panel's countdown once answered "closed"
        from the clock while the port still accepted, for the second until
        the watchdog's next tick (real run, 2026-09-26).
        """
        opening = self._opening
        if opening is not None and self._clock() >= opening.closes_at:
            self.close("timeout", only=opening)
        return self._opening is None

    def _watch(self, opening: Opening, stop: threading.Event) -> None:
        while not stop.wait(self._poll):
            if self._opening is not opening:
                return
            if self.close_if_due():
                return

    # --- what an accepted file becomes ---------------------------------------------

    def _take(self, stream: BinaryIO, name: str) -> None:
        result = self._accept(stream, name)
        self.received.append(name)
        self.results.append(result)
        applog.log("phone.received", filename=name)

    def _close_after_answer(self, opening: Opening) -> None:
        """One recording per opening (Robert, 2026-09-26): once the phone has
        its answer, the door closes. On a thread of its own, because closing
        joins the listener's thread, which is the one that just answered."""
        threading.Thread(
            target=self.close, args=("received",), kwargs={"only": opening},
            name="scribe-phone-door-close", daemon=True,
        ).start()


def _answer(status: int, message: str | None = None, received: str | None = None) -> HTMLResponse:
    from scribe.web import templates

    html = templates.env.get_template("phone_upload.html").render(
        field=FILE_FIELD, message=message, received=received
    )
    return HTMLResponse(html, status_code=status)


class _DoorApp:
    """The whole of what the phone can reach: one path, GET and POST."""

    def __init__(self, door: Door, secret: str) -> None:
        self._door = door
        self._secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if not self._door.admits(self._secret, scope.get("path", "")):
            await HTMLResponse("Not Found", status_code=404)(scope, receive, send)
            return
        opening = self._door.opening
        taken = False
        method = scope["method"].upper()
        if method in ("GET", "HEAD"):
            response = _answer(200)
        elif method == "POST":
            response, taken = await self._upload(scope, receive)
        else:
            response = HTMLResponse("Method Not Allowed", status_code=405, headers={"Allow": "GET, POST"})
        await response(scope, receive, send)
        if taken and opening is not None:
            self._door._close_after_answer(opening)

    async def _upload(self, scope: Scope, receive: Receive) -> tuple[HTMLResponse, bool]:
        """The answer for the phone, and whether a file was taken."""
        limit = self._door.max_bytes
        too_large = _answer(
            413, f"That file is larger than {limit // 1024**3} GB, which is the most this door takes."
        )
        headers = dict((k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope["headers"])
        try:
            declared = int(headers.get("content-length", "0") or 0)
        except ValueError:
            declared = 0
        if declared > limit:
            return too_large, False

        seen = 0

        async def counted() -> dict:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge()
            return message

        request = Request(scope, counted)
        try:
            async with request.form(max_files=1, max_fields=10) as form:
                part = form.get(FILE_FIELD)
                if not isinstance(part, UploadFile) or not part.filename:
                    return _answer(400, "Choose a recording first, then press Send."), False
                name = _basename(part.filename) or "recording"
                if PurePosixPath(name).suffix.lower() not in probe.MEDIA_EXTENSIONS:
                    return _answer(
                        415,
                        f"{name} is not an audio or video file, so it was not sent."
                        " Choose a recording or a video.",
                    ), False
                await run_in_threadpool(self._door._take, part.file, name)
                return _answer(200, received=name), True
        except _TooLarge:
            return too_large, False
        except MultiPartException:
            return _answer(400, "The upload arrived damaged. Try sending it again."), False
        except ClientDisconnect:
            return _answer(400, "The upload stopped before the whole file arrived."), False
