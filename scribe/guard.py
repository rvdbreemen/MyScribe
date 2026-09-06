"""Two guards that keep a localhost app to its one user's own browser.

The app binds 127.0.0.1 and has no login (spec: one user, one machine). That
is not the same as "only this user can reach it": a browser is a confused
deputy. Any page the user visits can auto-submit a form to
`http://127.0.0.1:4242/media/bulk` and the browser will send it, cookies or
no cookies, because there is nothing to send - the routes simply have no
caller check. Media ids are small integers, so trashing, purging or
re-transcribing the whole library from a hidden form is a matter of guessing
`1, 2, 3`. With DNS rebinding (the page's own hostname re-pointed at
127.0.0.1 after it loaded) the page can read the answers as well.

Both doors close on headers every browser already sends, so the fix costs no
login and no token:

* **The Host header must name this machine.** Starlette's
  `TrustedHostMiddleware` with the loopback names. A rebound page still
  carries its own hostname in `Host`, and gets a 400 for it. `[::1]` is not
  in the list: the app binds IPv4 only (`scribe.__main__.HOST`), and the
  middleware's `host.split(":")[0]` could not match a bracketed IPv6 literal
  anyway.
* **A request that changes something must come from this app's own pages.**
  `SameOriginPosts` refuses any non-safe method whose `Sec-Fetch-Site` is
  anything but `same-origin` or `none` (the user typed the URL), or whose
  `Origin` names a place other than the one the request came in on. A
  request that carries neither header - curl, a script, the test client - is
  the user at the keyboard, and passes.

GETs are not guarded: a link from anywhere is how a shared `/media/3#t=42`
arrives, and reading changes nothing. What a cross-site page cannot do with
a GET is see the answer, which the Host guard and the browser's same-origin
policy already take care of between them.
"""

from __future__ import annotations

from typing import Mapping

from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# The names a browser puts in Host when it is talking to this machine.
ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost")

# Methods that read. Everything else mutates, and is checked.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Sec-Fetch-Site values that mean "this app's own page" - or no page at all:
# `none` is a navigation the user started (address bar, bookmark).
OWN_FETCH_SITES = frozenset({"same-origin", "none"})


def cross_site_reason(headers: Mapping[str, str], scheme: str) -> str | None:
    """Why the request is not from this app's own pages, or None when it is.

    ``headers`` is lower-cased names to values, as Starlette's ``Headers``
    gives them. The Origin check compares against the origin this request
    was addressed to (``scheme://Host``), which is what makes a page at
    `localhost:4242` posting to `127.0.0.1:4242` cross-origin: the browser
    would say so, and so does this.
    """
    site = headers.get("sec-fetch-site")
    if site is not None and site.strip().lower() not in OWN_FETCH_SITES:
        return f"cross-site request refused (Sec-Fetch-Site: {site.strip()})"

    origin = headers.get("origin")
    if origin is not None:
        own = f"{scheme}://{headers.get('host', '')}".lower()
        if origin.strip().lower().rstrip("/") != own:
            return f"request from {origin.strip()} refused; this app only answers its own pages"
    return None


class SameOriginPosts:
    """ASGI middleware: 403 for a mutating request from another origin.

    Pure ASGI rather than `BaseHTTPMiddleware` so the SSE stream and the
    file responses pass through untouched - nothing here wraps a body; a
    request either goes on to the app or gets a JSON 403 in the shape the
    routes' own errors have (``{"detail": ...}``), which app.js already
    knows how to show.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        reason = cross_site_reason(Headers(scope=scope), scope.get("scheme", "http"))
        if reason is None:
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"detail": reason}, status_code=403)
        await response(scope, receive, send)


def install(app) -> None:
    """Wrap ``app`` in both guards. Host check outermost: a request from a
    rebound hostname is refused before its origin is even looked at."""
    app.add_middleware(SameOriginPosts)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))
