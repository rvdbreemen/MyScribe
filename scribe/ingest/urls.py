"""yt-dlp, wrapped so a failure is a sentence rather than a stack trace.

Three functions and a taxonomy. `probe` asks a URL what it is without
downloading anything, `download` fetches the audio into a directory we own,
and `hotword_terms` reads the names out of the metadata so the decoder can be
biased towards them. Nothing here writes to the database and nothing here
knows what a job is; `scribe/stages/url_stage.py` is what joins the two.

**This module reaches the network, so it runs in a runner child** (ADR-001).
The one exception the plan allows is the dialog's preview, which calls `probe`
in a threadpool with a timeout - a read of a page, never a download.

Everything a URL returns is untrusted input. A video title is chosen by a
stranger and ends up in a filename, so the download lands on a fixed stem and
is *then* renamed through the same Win32 scrub the exporters use; a title of
``..\\..\\evil`` comes out as a name inside the destination directory. The real
title still goes on the media row, where it is text and not a path.

**yt-dlp ages by design.** Sites change their players and their APIs, and an
extractor pinned three months ago stops working with no warning and no
version bump on our side. This module pins nothing itself - `pyproject.toml`
and `uv.lock` do - but it does two things about it: `is_stale` says when the installed
copy is old enough to be the likely culprit, and a failure whose message
smells like a broken extractor says so in words, with the installed version
and the command that fixes it. That sentence is the difference between "this
app is broken" and "run one command".

Windows note, and it is not a small one: `--cookies-from-browser` cannot read
Chrome or Edge cookies since App-Bound Encryption (Chrome 127+). Firefox still
works, and so does an exported `cookies.txt`. That is why `download_opts`
takes a cookies *file* and the dialog offers a file field rather than a browser
picker - a door that only sometimes works is worse than one that says what it
needs.
"""

from __future__ import annotations

import functools
import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urljoin, urlparse
from urllib.request import (
    AbstractHTTPHandler,
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

from scribe import glossary

# The name yt-dlp is installed under, which is not the name it is imported
# under: `pip show yt-dlp`, `import yt_dlp`.
DISTRIBUTION = "yt-dlp"

# The stem every download lands on before it is renamed. Fixed on purpose: a
# `%(title)s` in the output template would put a stranger's text straight into
# a path, and yt-dlp's own sanitiser is not a promise we should be leaning on.
DOWNLOAD_STEM = "download"

# What a media file is called when the title scrubs away to nothing (a title of
# "..." or of punctuation only). It still has to be a name.
FALLBACK_STEM = "download"

# Suffix yt-dlp gives the metadata sidecar it writes next to the media.
INFO_JSON_SUFFIX = ".info.json"

# Files in the download directory that are not the media: the sidecar, a
# partial fragment, yt-dlp's own resume bookkeeping.
_NOT_THE_MEDIA = (INFO_JSON_SUFFIX, ".part", ".ytdl", ".temp")

# How long yt-dlp may be before a failure is worth blaming on it. Three months
# is roughly the interval over which a big site has changed something that
# mattered; it is a heuristic in a hint, not a gate on anything.
STALE_DAYS = 90

# What to run about an old yt-dlp. The environment is uv's, which has no pip in
# it (ADR-012); an installed copy gets a newer yt-dlp with the next release.
UPDATE_COMMAND = "uv lock --upgrade-package yt-dlp && uv sync"

# Seconds to wait on a socket. Long enough for a slow server, short enough that
# a hung connection does not become a job that never ends.
SOCKET_TIMEOUT = 15

# The link types a podcast page announces its feed with, best first. RSS is
# what carries enclosures and so what yt-dlp can actually read as episodes;
# Atom is usually the blog feed published beside it, worth trying only when
# there is no RSS. Ordered, because a page that offers both means both.
FEED_LINK_TYPES = ("application/rss+xml", "application/atom+xml")

# How much of a page discovery will read. The link tag lives in `<head>`, so
# this is a ceiling on a body that turned out not to be a page at all - an
# unsupported URL can be a 200 MB mp3, and looking for a link tag in it is the
# bug this number exists to prevent. Half a megabyte is several times the
# largest podcast home page and still nothing on a socket.
MAX_DISCOVERY_BYTES = 512 * 1024

# Content types worth parsing for a link tag. A feed itself is never here:
# yt-dlp reads those, and one that failed will not read better twice.
_HTML_TYPES = ("text/html", "application/xhtml+xml")

# Seconds discovery may spend on its page, and deliberately not `SOCKET_TIMEOUT`.
# The dialog's preview is the one place ADR-001 lets the web process touch the
# network, on a 10 s budget that an abandoned thread then holds for as long as
# `SOCKET_TIMEOUT` allows. Discovery is a second read inside that same budget,
# so borrowing the full 15 s would let one preview hold a socket for 30 - twice
# what ADR-001's exception was written for. Five seconds is a page, or nothing.
DISCOVERY_TIMEOUT = 5

# What discovery asks for. Not a nicety: a Castopod page answers
# `https://host/@show` with ActivityPub JSON when the request states no
# preference, and urllib states none by default - which is exactly how the
# first real run of this code found no feed on a page that has one. Asking for
# HTML is what makes the server serve the document the link tag lives in.
_HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"

# A run of letters, apostrophes and hyphens - what counts as a name here and
# in the hotwords. One definition, in `scribe.glossary` (Phase 6 Task 5), which
# is a module of rules and rows: importing it costs nothing, where importing
# the transcribe stage for the same regex would pull numpy into a function that
# only wants to find capitals.
_WORD = glossary._WORD

# yt-dlp lines that carry no information about what went wrong: its standard
# request to file a bug. Dropped before the last line is taken as the reason,
# so a two-line error reports the failure and not the boilerplate under it.
_BOILERPLATE = (
    "please report this issue",
    "confirm you are on the latest version",
    "make sure you are using the latest version",
    "type  yt-dlp -u  to update",
)

# Messages that mean the video is gone, private, or not ours to fetch. Nothing
# about the installation is wrong and updating will not help; the user needs a
# different URL or an account.
_UNAVAILABLE = (
    "video unavailable",
    "private video",
    "video is private",
    "has been removed",
    "removed by the uploader",
    "this video is not available",
    "not made this video available in your country",
    "not available in your country",
    "members-only",
    "join this channel",
    "sign in to confirm your age",
    "account associated with this video has been terminated",
    "geo restricted",
)

# Messages that smell like an extractor that no longer matches the site. Every
# one of them is yt-dlp failing to find something in a page it could read.
_STALE_EXTRACTOR = (
    "unable to extract",
    "extraction failed",
    "please report this issue",
    "confirm you are on the latest version",
    "no video formats found",
    "failed to parse json",
    "unable to recognize playlist",
)


# --- what can go wrong ---------------------------------------------------------


class UrlError(RuntimeError):
    """A URL could not be read or fetched. `code` is the job's error_code.

    Three subclasses rather than three messages, because the three call for
    three different actions: fix the URL, find another copy, or fix the tool.
    `scribe.runner._ERROR_CODES` maps them onto the codes the jobs board shows.
    """

    code = "DOWNLOAD_FAILED"


class UnsupportedUrl(UrlError):
    """Not a URL yt-dlp can fetch - a bad scheme, or a site it has no extractor for."""

    code = "UNSUPPORTED_URL"


class Unavailable(UrlError):
    """The URL is fine; what it points at is private, removed or region-locked."""

    code = "UNAVAILABLE"


class DownloadFailed(UrlError):
    """Everything else: a network error, a refused request, a stale extractor."""

    code = "DOWNLOAD_FAILED"


class TooManyEntries(UrlError):
    """A playlist with more entries than this app will queue in one go.

    Its own code because nothing failed and nothing is wrong with the link:
    what has to change is how much of it is asked for at once.
    """

    code = "TOO_MANY_ENTRIES"


# --- what a URL turned out to be -----------------------------------------------


@dataclass(frozen=True)
class UrlInfo:
    """What `probe` learned without downloading anything.

    `entries` is empty for a single video and holds one ``{id, title, duration,
    url, name, timestamp, source_id}`` per item for a playlist - flat, because
    a playlist is expanded into one job per entry and each of those probes its
    own URL properly. ``name`` is what the entry's job and file are called:
    the title, or for a podcast feed `episode_names`' number-and-title.
    """

    kind: Literal["single", "playlist"]
    title: str
    duration: float | None
    uploader: str
    webpage_url: str
    entries: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    total: int | None = None
    """yt-dlp's `playlist_count`: the whole source's length, whatever `limit`
    cut the listing to. A feed is a list and always reports one; a paginated
    channel does not, so None there means "unknown", never "zero"."""
    truncated: bool = False
    """Whether `limit` cut the listing short. From `total` when there is one;
    otherwise the count reaching the limit is the only signal, and a channel
    of exactly `limit` videos reads as cut - the honest answer, since nothing
    can tell the two apart."""


@dataclass(frozen=True)
class DownloadedMedia:
    """A file on disk plus the metadata that came with it."""

    path: Path
    title: str
    duration: float | None
    uploader: str
    info: dict


# --- the yt-dlp seam -----------------------------------------------------------


def build_ydl(opts: dict):
    """A configured `yt_dlp.YoutubeDL`.

    The one place yt-dlp is imported, and it is imported here rather than at
    module scope for two reasons: importing it costs several hundred
    milliseconds of extractor registration that a process which never fetches
    a URL should not pay, and this module stays importable on a machine where
    yt-dlp is not installed - which is what lets the doctor say so politely
    instead of the app failing to start.

    Tests replace this function; nothing else in the module reaches yt-dlp.
    """
    from yt_dlp import YoutubeDL

    return YoutubeDL(opts)


def installed_version() -> str | None:
    """The installed yt-dlp's version string, or None if it is not installed.

    Read from the distribution metadata, not from `yt_dlp.version`, and the
    difference is the whole point: the doctor's `check_ytdlp` is a CPU check,
    and the settings page runs those in the request. Importing the package to
    read a string cost 554 ms of import time and left the whole of yt-dlp
    resident in the web process for good - exactly the cost `build_ydl`'s
    docstring says is deferred so a process that never fetches a URL does not
    pay it. `importlib.metadata` reads the installed `METADATA` file instead.

    The distribution is spelled `yt-dlp`; the module is `yt_dlp`.
    """
    try:
        return str(metadata.version(DISTRIBUTION))
    except metadata.PackageNotFoundError:
        return None


def release_date(version: str | None) -> date | None:
    """The release date a yt-dlp version encodes, or None if it does not.

    Releases are dated (`2026.08.19`) and nightlies add a time (`.232303`), so
    the first three dot-separated parts are the date in both. Read as parts
    rather than as a fixed-width slice, because the distribution metadata
    spells the same release the way PEP 440 normalises it - `2026.8.19`, one
    character shorter - and a slice then cuts a nightly in the wrong place and
    reports "release date unknown" for a copy installed yesterday.

    Anything else - a fork, a git checkout, a string somebody edited - is None
    rather than a guess.
    """
    parts = str(version or "").split(".")
    if len(parts) < 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def age_days() -> int | None:
    """How many days old the installed yt-dlp's release is, if it can be told."""
    released = release_date(installed_version())
    return None if released is None else (date.today() - released).days


def is_stale() -> bool:
    """Whether the installed yt-dlp is old enough to be the likely culprit.

    A version we cannot date is not called stale: a false accusation sends
    somebody chasing the wrong thing.
    """
    age = age_days()
    return age is not None and age > STALE_DAYS


def stale_note() -> str:
    """The sentence appended to a failure that smells like a broken extractor."""
    version = installed_version() or "unknown"
    age = age_days()
    old = f", released {age} days ago" if age is not None else ""
    return (
        f"This usually means yt-dlp's extractor for this site is out of date "
        f"(yt-dlp {version} installed{old}); update it with: {UPDATE_COMMAND}"
    )


# --- the options ---------------------------------------------------------------


def _common_opts() -> dict:
    """What every call wants: quiet, no console progress bar, a socket timeout.

    `noprogress` turns off yt-dlp's own terminal bar - the runner child has no
    terminal, and progress belongs on the jobs board, through the hook.
    """
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        "socket_timeout": SOCKET_TIMEOUT,
    }


def probe_opts(cookies_file: str | None = None, *, limit: int | None = None) -> dict:
    """Options for asking what a URL is, without fetching it.

    `extract_flat="in_playlist"` is the whole trick: a single video is
    extracted properly, while a playlist yields its entries as stubs. Fetching
    fifty videos' metadata to answer "is this a playlist" would make the
    dialog's preview take a minute.

    ``limit`` becomes `playlistend`, and what it bounds depends on the source.
    A channel is paginated - yt-dlp stops asking for pages once it has enough
    entries, so the limit bounds the *work* (measured 2026-09-08: about 40
    entries a second, so 2500 is about a minute). A feed is one document,
    read whole and only trimmed afterwards; the limit bounds nothing but the
    length of the answer. yt-dlp honours `playlistend` only while
    `playlist_items` is None, which is why that key is spelled out below.

    `noplaylist` is here for the same reason `download_opts` sets it, and it
    matters more here: the probe is what *decides*. `url_stage.fetch` probes
    first and returns without ever reaching `download` when the answer comes
    back "playlist", so the guard on the download alone sat on a path this
    input never takes - and the input is the URL YouTube actually gives you.
    The address bar reads `watch?v=VIDEO&list=PLAYLIST` while you are watching
    a video inside a playlist, and without this that paste probed as the whole
    playlist and `register` fanned it out into one job per entry.

    It cannot over-reach, because of how yt-dlp spends it (measured 2026-09-04
    against yt-dlp 2026.08.19, `InfoExtractor._yes_playlist`): the option only
    ever breaks a tie between a video id and a list id that arrived together.
    A bare `/playlist?list=...`, or a channel URL, names no video, so it still
    probes as a playlist and still fans out.
    """
    opts = {
        **_common_opts(),
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlist_items": None,
        "noplaylist": True,
    }
    if limit is not None:
        opts["playlistend"] = int(limit)
    if cookies_file:
        # A members-only video needs the cookie to say even what it is called.
        opts["cookiefile"] = str(cookies_file)
    return opts


def download_opts(dest_dir: str | Path, cookies_file: str | None = None) -> dict:
    """Options for fetching the audio of one video into ``dest_dir``.

    `bestaudio/best` with an empty `postprocessors` list is the no-re-encode
    rule: whatever the site already has (Opus in WebM, AAC in M4A) is taken as
    it is. Asking for `--extract-audio` would run it through ffmpeg for no
    gain - the prepare stage decodes to 16 kHz mono anyway, so a re-encode here
    would only cost time and a generation of quality.

    `noplaylist` matters more than it looks: a YouTube video URL usually
    carries a `&list=` from wherever it was copied, and without this a single
    video would quietly become the whole playlist.
    """
    opts = {
        **_common_opts(),
        "format": "bestaudio/best",
        "outtmpl": str(Path(dest_dir) / f"{DOWNLOAD_STEM}.%(ext)s"),
        "writeinfojson": True,
        "noplaylist": True,
        "postprocessors": [],
        "overwrites": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if cookies_file:
        # A file the user exported, never a browser: see the module docstring
        # for why --cookies-from-browser is not on offer on Windows.
        opts["cookiefile"] = str(cookies_file)
    return opts


# --- reading a URL -------------------------------------------------------------


def ensure_http_url(url: str) -> str:
    """``url`` if it is an http(s) URL; `UnsupportedUrl` otherwise.

    The guard sits here rather than only in the route because this is where the
    danger is: yt-dlp reads `file://` through its generic extractor and turns a
    bare string into a search query, so an `ingest_url` job enqueued through
    the JSON API could otherwise ask it to open a local file or search the web.
    """
    text = (url or "").strip()
    scheme = urlparse(text).scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsupportedUrl(
            f"{text or 'an empty string'} is not a web address; "
            "paste an http:// or https:// link"
        )
    return text


def is_local_host(host: str | None) -> bool:
    """Does this host, as written, point at this machine or its network?

    A literal address is judged by `ipaddress`: anything not globally
    routable - loopback, private, link-local, unspecified, multicast,
    reserved - is local. A name is local when it is `localhost`, ends in
    `.localhost`, or ends in `.local` (mDNS: printers, NAS boxes). No host at
    all is local too: there is nothing public to fetch. Names are not
    resolved *here* - that is `resolves_private`, and it runs at request
    time, where redirect hops are judged too. This stays the cheap literal
    check that refuses before any network is touched (TASK-072).
    """
    name = (host or "").strip().lower()
    if not name:
        return True
    if name == "localhost" or name.endswith(".localhost") or name.endswith(".local"):
        return True
    try:
        return not ipaddress.ip_address(name).is_global
    except ValueError:
        return False


def ensure_public_http_url(url: str) -> str:
    """`ensure_http_url`, and the host must not be this machine or its network.

    For an address a feed or playlist author wrote down, never for one a
    person pasted (TASK-072). A followed feed is polled unattended on its own
    timer, and its author could publish an enclosure at 127.0.0.1:11434 or
    192.168.1.1/admin and have this app fetch it on their behalf, from inside
    the network. A person who pastes their NAS's address chose it, and the
    app listens on 127.0.0.1 only, so `ensure_http_url` stays a scheme check.
    """
    text = ensure_http_url(url)
    if is_local_host(urlparse(text).hostname):
        raise UnsupportedUrl(
            f"{text} points at this machine or the local network; "
            "a feed or playlist cannot ask for that"
        )
    return text


def resolves_private(host: str | None, addresses: list[str] | None = None) -> bool:
    """Does ``host`` resolve to an address outside the public internet?

    The fetch-time half of `is_local_host`: the literal spelling is judged
    before anything is fetched, but a public-looking name can resolve to a
    private address, and checking it is only safe the moment the request is
    actually made - which is why `public_only` calls this per request rather
    than `ensure_public_http_url` calling it once.

    ``addresses`` are answers already looked up - the ones a connection is
    about to use (`_connect_checked`) - and are judged instead of asking again.

    A name that will not resolve answers False rather than refusing: it
    reaches nothing either way, and the fetch's own "could not resolve host"
    says the truer thing. That fail-open is safe only because the connection
    judges its own lookup again before it connects (GitHub #41). The lookup
    has no timeout of its own - the OS resolver's applies, as it does to the
    connection's lookup. A literal answers by `ipaddress` without a lookup.
    """
    name = (host or "").strip()
    if not name:
        return True
    try:
        return not ipaddress.ip_address(name).is_global
    except ValueError:
        pass
    if addresses is None:
        try:
            addresses = [answer[4][0] for answer in socket.getaddrinfo(name, None)]
        except OSError:
            return False
    return any(not ipaddress.ip_address(address).is_global for address in addresses)


def _refuse_private(what: str) -> UnsupportedUrl:
    return UnsupportedUrl(
        f"{what} resolves to this machine or the local network; "
        "a feed or playlist cannot ask for that"
    )


def _require_public_target(url: str) -> str:
    """`ensure_public_http_url`, and the host must resolve publicly too."""
    text = ensure_public_http_url(url)
    if resolves_private(urlparse(text).hostname):
        raise _refuse_private(text)
    return text


def _connect_checked(address, timeout, source_address=None):
    """`socket.create_connection`, connecting only to answers it has judged.

    Checking a name and then letting the connection look it up again leaves
    a gap: a resolver can answer public to the check and private to the
    connect (DNS rebinding, GitHub #41). Here the one lookup is both judged
    and used - the socket goes to the very address that passed. TLS is not
    affected: `http.client` still wraps the socket with the host *name*.
    """
    host, port = address[0], address[1]
    answers = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if resolves_private(host, [answer[4][0] for answer in answers]):
        raise _refuse_private(host)
    error: OSError | None = None
    for answer in answers:
        try:
            return socket.create_connection(answer[4][:2], timeout, source_address)
        except OSError as exc:
            error = exc
    raise error or OSError(f"{host} has no address to connect to")


def public_only(ydl) -> None:
    """Restrict a `YoutubeDL` to fetching public http(s) URLs.

    Two layers, because yt-dlp has two places a URL enters. `ydl.urlopen` is
    the call every request a `YoutubeDL` *starts* goes through, so wrapping it
    judges each URL an extractor or downloader asks for - the literal
    spelling and what the name resolves to - before anything is sent.

    Redirects do not come back through it: urlopen hands the request to a
    request handler once, and the handler follows any 302 itself (GitHub
    #41). So the `YoutubeDL` also gets a request director holding a single
    handler, yt-dlp's urllib one, whose redirect step judges every hop - see
    `_public_urllib_handler` for why that handler and not the others.

    A resolver that answers differently at check time and connect time is
    met by that handler too: without a proxy it connects to the address it
    judged (see `_guard_opener`). Behind a proxy that pin is off, and the
    URL and redirect checks - by name - are the guard. What the connection
    then reaches depends on the proxy: an HTTP proxy, and a socks4a or
    socks5h one, looks the name up itself, an answer the guard never sees;
    a socks4 or socks5 one is handed an address yt-dlp's SOCKS code looked
    up locally, a second lookup the guard did not judge (GitHub #41).

    Downloads stay inside yt-dlp, where the two layers above can see them:
    see `_native_downloads_only`. ffmpeg still post-processes local files;
    it is never handed a URL to fetch.

    A `ydl` with no `urlopen` has no network path to guard - the test seam
    is built that way - and is left alone. Guarding twice is a no-op: `probe`
    passes the same `ydl` on when it retries with a discovered feed.
    """
    real_open = getattr(ydl, "urlopen", None)
    if real_open is None or getattr(ydl, "_scribe_public_only", False):
        return

    def guarded(req, *args, **kwargs):
        _require_public_target(_request_url(req))
        return real_open(req, *args, **kwargs)

    ydl.urlopen = guarded
    build_director = getattr(ydl, "build_request_director", None)
    if build_director is not None:
        if "_request_director" in vars(ydl):
            # Already built (a cached_property): close what it opened first.
            ydl._request_director.close()
        ydl._request_director = build_director([_public_urllib_handler()])
    _native_downloads_only(ydl)
    ydl._scribe_public_only = True


NATIVE_PROTOCOLS = frozenset(
    {"http", "https", "m3u8", "m3u8_native", "http_dash_segments", "http_dash_segments_generator"}
)
"""The protocols yt-dlp's own downloaders fetch over plain http(s), through
`ydl.urlopen`: a file, an HLS playlist and its segments, DASH segments."""


def _not_held(url, by: str) -> UnsupportedUrl:
    return UnsupportedUrl(
        f"{url} would be fetched by {by}, which is not held to this app's "
        "address checks; a feed or playlist cannot ask for that"
    )


def _native_downloads_only(ydl) -> None:
    """Keep every download of a guarded ``ydl`` inside yt-dlp's own fetching.

    yt-dlp gives HLS to ffmpeg by default, honours an ``external_downloader``
    (aria2c, curl, ...), fetches rtmp with rtmpdump - and each of those opens
    its own connections to the playlist, segment and key URLs, outside
    `ydl.urlopen` and every check `public_only` makes. A feed's author writes
    those URLs (GitHub #41). So, in three layers:

    - every protocol is sent to yt-dlp's native downloader
      (``external_downloader`` "native" - what ``hls_prefer_native`` became);
    - a format whose protocol no native downloader fetches over http(s) -
      rtmp, rtsp, mms, f4m - is refused by name before a downloader starts;
    - an outside downloader yt-dlp still starts by itself is refused for this
      ``ydl`` (`_outside_downloaders_refuse`): a live stream always goes to
      ffmpeg, and the HLS downloader hands a playlist it cannot decrypt
      (SAMPLE-AES) to ffmpeg whatever the options say.
    """
    params = getattr(ydl, "params", None)
    if isinstance(params, dict):
        params["external_downloader"] = {"default": "native"}
    real_dl = getattr(ydl, "dl", None)
    if real_dl is None:
        return
    _outside_downloaders_refuse()

    def dl(name, info, *args, **kwargs):
        from yt_dlp.utils import determine_protocol

        for fmt in info.get("requested_formats") or [info]:
            for protocol in determine_protocol(fmt).split("+"):
                if protocol not in NATIVE_PROTOCOLS:
                    raise _not_held(fmt.get("url"), f"an outside program ({protocol})")
        return real_dl(name, info, *args, **kwargs)

    ydl.dl = dl


@functools.cache
def _outside_downloaders_refuse() -> None:
    """Make yt-dlp's outside downloaders refuse a ``ydl`` under `public_only`.

    On the classes, because yt-dlp builds these downloaders itself, deep
    inside a download (`HlsFD.real_download` constructs `FFmpegFD` directly).
    Every one that starts a program comes through one of these two methods:
    `ExternalFD` is ffmpeg, aria2c, curl, wget and the rest, `RtmpFD` is
    rtmpdump. A ``ydl`` nobody guarded - a URL a person pasted - is let
    through untouched. Built on first use, like `_public_urllib_handler`.
    """
    from yt_dlp.downloader.external import ExternalFD
    from yt_dlp.downloader.rtmp import RtmpFD

    for cls in (ExternalFD, RtmpFD):
        cls.real_download = _refused_when_public(cls.real_download)


def _refused_when_public(real_download):
    def checked(self, filename, info_dict):
        if getattr(self.ydl, "_scribe_public_only", False):
            basename = getattr(self, "get_basename", None)
            raise _not_held(info_dict.get("url"), basename() if basename else "rtmpdump")
        return real_download(self, filename, info_dict)

    return checked


@functools.cache
def _public_urllib_handler():
    """yt-dlp's urllib request handler, judging every redirect it follows.

    Why only this handler: yt-dlp prefers `requests` (and `curl_cffi` when it
    is installed), and both follow redirects inside their own sessions, where
    nothing outside can see the hop. The urllib handler follows them through
    an `urllib.request.OpenerDirector`, whose redirect step `_guard_opener`
    can wrap. yt-dlp supports running on urllib alone - it is what its
    ``prefer-legacy-http-handler`` option selects - so an unattended feed
    loses connection reuse, not features.

    The director is a cached_property that only `YoutubeDL.close()` drops
    (and rebuilds unguarded on next use); nothing here closes a `ydl` it
    still means to use.

    Built on first use, because importing yt-dlp is what `build_ydl` exists
    to put off.
    """
    from yt_dlp.networking._urllib import UrllibRH
    from yt_dlp.networking.exceptions import RequestError

    class PublicUrllibRH(UrllibRH):
        # The key YoutubeDL looks its urllib handler up by (its `_opener`,
        # its "file:// URLs are disabled" message), so it still finds it.
        RH_KEY = "Urllib"

        def _create_instance(self, *args, **kwargs):
            return _guard_opener(super()._create_instance(*args, **kwargs))

        def _send(self, request):
            try:
                return super()._send(request)
            except UrlError as refused:
                # The director reports anything that is not a RequestError
                # as a yt-dlp bug ("please report this issue"); a refused hop
                # is a failed request, in the guard's own words.
                raise RequestError(str(refused), cause=refused) from refused

    return PublicUrllibRH


def _request_url(req) -> str:
    """The URL of whatever was handed to ``urlopen``.

    yt-dlp passes its own `yt_dlp.networking.common.Request`, which has
    ``.url``, no ``.full_url``, and a str() that is its repr - read as a
    string, every request was refused as "not a web address" (GitHub #41).
    A urllib `Request` has ``.full_url``; a plain string is the URL. Anything
    else falls through to str() and is refused, which is the safe way round.
    """
    if isinstance(req, str):
        return req
    for name in ("full_url", "url"):
        url = getattr(req, name, None)
        if isinstance(url, str):
            return url
    return str(req)


def _guard_opener(opener):
    """``opener``, judging every hop, not just the first: a public URL may not 302 home.

    `redirect_request` is where urllib decides the next hop, so the check
    lives there - refusing by raising, which urllib surfaces as an error on
    the request rather than a redirect it followed. It is wrapped on the
    handler the opener already holds rather than replaced by a subclass,
    because the same function guards two openers: discovery's own and the
    one yt-dlp's urllib handler builds with its own redirect handler, which
    has to keep its redirect rules (GitHub #41).

    Its http(s) handlers connect through `_connect_checked` for the same
    reason: every hop opens its own connection, and that connection goes to
    the address that was judged. Not behind a proxy: the socket then goes to
    the proxy, which may well live on this network, so there the URL and
    redirect checks are the whole guard. An HTTP, socks4a or socks5h proxy
    looks the name up itself; for socks4 and socks5 yt-dlp's SOCKS code
    (`yt_dlp.socks`) looks it up here, at connect time, in a lookup of its
    own that this pin does not reach (GitHub #41).
    """
    for handler in opener.handlers:
        if isinstance(handler, HTTPRedirectHandler):
            handler.redirect_request = _judging_hops(handler.redirect_request)
        if isinstance(handler, AbstractHTTPHandler):
            handler.do_open = _connecting_checked(handler.do_open)
    return opener


def _judging_hops(redirect_request):
    def judged(req, fp, code, msg, headers, newurl):
        try:
            _require_public_target(newurl)
        except UrlError:
            # urllib reads and closes the 302's body only once this returns;
            # raising past that would leave its socket open until GC.
            if fp is not None:
                fp.close()
            raise
        return redirect_request(req, fp, code, msg, headers, newurl)

    return judged


def _connecting_checked(do_open):
    def opened(http_class, req, **kwargs):
        def connection(*args, **conn_kwargs):
            conn = http_class(*args, **conn_kwargs)
            # A SOCKS proxy passes this test - yt-dlp carries it in a header,
            # not as a urllib proxy - and its connection class ignores
            # `_create_connection`, so the assignment is harmless there.
            if not (req.has_proxy() or req._tunnel_host):
                conn._create_connection = _connect_checked
            return conn

        return do_open(connection, req, **kwargs)

    return opened


def fetch_page(url: str, *, limit: int, public: bool = False) -> tuple[str, str]:
    """``(content_type, text)`` for ``url``, reading at most ``limit`` bytes.

    The one place discovery touches the network, and a seam the tests replace.
    Deliberately small: no cookies, no retry. A page that will not load in one
    try is a page whose podcast the user can still reach by pasting the feed.

    ``public`` is for a page a feed pointed at rather than a person: the URL
    itself and every redirect it answers with must stay outside this network.
    """
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MyScribe)",
            "Accept": _HTML_ACCEPT,
        },
    )
    if public:
        _require_public_target(url)
        open_url = _guard_opener(build_opener()).open
    else:
        open_url = urlopen
    with open_url(request, timeout=DISCOVERY_TIMEOUT) as response:  # noqa: S310 - scheme checked by the caller
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read(limit)
    return content_type, body.decode(charset, errors="replace")


class _FeedLinks(HTMLParser):
    """Collects ``<link rel=alternate type=...feed...>`` hrefs by type.

    `HTMLParser` rather than a regex because this is a stranger's markup: the
    attributes come in any order, the quoting is whatever their templating
    did, and the tag may never be closed. The stdlib parser is bored by all
    three, and it costs no dependency.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        got = {name.lower(): (value or "").strip() for name, value in attrs}
        rel = got.get("rel", "").lower().split()
        kind = got.get("type", "").lower().split(";")[0].strip()
        href = got.get("href", "")
        if "alternate" in rel and kind in FEED_LINK_TYPES and href:
            self.found.setdefault(kind, href)


def feed_links(html: str) -> list[str]:
    """Every feed href ``html`` announces, RSS before Atom.

    Relative as written; `discover_feed` resolves and judges them.
    """
    parser = _FeedLinks()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - a stranger's markup is never a crash here
        pass
    return [parser.found[kind] for kind in FEED_LINK_TYPES if kind in parser.found]


def discover_feed(
    page_url: str,
    *,
    fetch: Callable[..., tuple[str, str]] | None = None,
    public: bool = False,
) -> str | None:
    """The feed ``page_url`` announces, or None.

    A podcast home page is not something yt-dlp has an extractor for, but it
    says where its feed is - `<link rel="alternate" type="application/rss+xml">`
    is on every Castopod, Podbean, Transistor and Buzzsprout page there is.
    Reading that tag turns the most obvious thing a user can paste into the
    thing the app can actually import.

    **The href is the page author's text, not the user's** - the TASK-072
    case one page further in. A page may advertise its feed at
    `http://127.0.0.1:11434/api/tags` and have this app fetch it from inside
    the network, so what comes back goes through `ensure_public_http_url`
    before anything touches it. Nothing here raises: a discovery that cannot
    answer returns None and lets the original failure stand.
    """
    fetch = fetch or (
        lambda url, *, limit: fetch_page(url, limit=limit, public=public)
    )
    try:
        content_type, body = fetch(page_url, limit=MAX_DISCOVERY_BYTES)
    except Exception:  # noqa: BLE001 - discovery is a second chance, never a new failure
        return None
    if content_type.lower().split(";")[0].strip() not in _HTML_TYPES:
        return None
    for href in feed_links(body):
        candidate = urljoin(page_url, href)
        try:
            return ensure_public_http_url(candidate)
        except UrlError:
            continue  # a private address, or not a web address at all
    return None


def probe(
    url: str,
    *,
    cookies_file: str | None = None,
    limit: int | None = None,
    ydl=None,
    fetch: Callable[..., tuple[str, str]] | None = None,
    discover: bool = True,
    public: bool = False,
) -> UrlInfo:
    """What this URL is, without downloading a byte.

    ``limit`` caps a playlist's listing (see `probe_opts`) and is what
    `truncated` is judged against. `returned` is yt-dlp's raw count, before
    `_entries` drops the items nobody could fetch: the question is whether
    the *site* had more, not whether every item was usable.

    ``discover`` is the podcast home page fallback (TASK-083): a URL yt-dlp
    has no extractor for gets read once as a page, and if it announces a feed,
    that feed is probed instead. Off the happy path by construction - a URL
    that works costs no extra request - and one hop only, because the feed's
    own failure is the user's answer, not a third thing to try.
    """
    url = ensure_http_url(url)
    ydl = ydl if ydl is not None else build_ydl(probe_opts(cookies_file, limit=limit))
    if public:
        public_only(ydl)
    try:
        info = _extract(ydl, url, download=False)
    except UnsupportedUrl as unsupported:
        if not discover:
            raise
        feed = discover_feed(url, fetch=fetch, public=public)
        if feed is None:
            raise
        try:
            return probe(feed, cookies_file=cookies_file, limit=limit, ydl=ydl, discover=False, public=public)
        except UrlError:
            # The feed was announced but is no better. The page is what the
            # user pasted and what they can act on, so its error is the one
            # that survives.
            raise unsupported from None

    entries = _entries(info)
    returned = len(info.get("entries") or [])
    total = _as_int(info.get("playlist_count"))
    if total is not None:
        truncated = total > returned
    else:
        truncated = limit is not None and returned >= limit
    # A podcast feed is what yt-dlp's Generic extractor reads an RSS document
    # as; YouTube's playlists come from its own extractors and keep their titles.
    if entries and info.get("extractor_key") == "Generic":
        names = episode_names(entries)
    else:
        names = [entry["title"] for entry in entries]
    for entry, name in zip(entries, names):
        entry["name"] = name
    return UrlInfo(
        kind="playlist" if entries else "single",
        title=str(info.get("title") or ""),
        duration=_as_float(info.get("duration")),
        uploader=_uploader(info),
        webpage_url=str(info.get("webpage_url") or info.get("original_url") or url),
        entries=entries,
        info=info,
        total=total,
        truncated=truncated,
    )


def download(
    url: str,
    dest_dir: str | Path,
    *,
    on_progress: Callable[[float], None] | None = None,
    cookies_file: str | None = None,
    ydl=None,
    title_hint: str | None = None,
    public: bool = False,
) -> DownloadedMedia:
    """Fetch the audio of one video into ``dest_dir``; returns where it landed.

    ``title_hint`` names the file when given: a feed knows what its episode is
    called, and a bare enclosure does not (measured 2026-09-08: it probes as
    `default.mp3_ywr3ahjkcgo_…`, the CDN's name for it). The hint is a
    stranger's text posted back by a browser and goes through the same scrub
    as a site's title; an empty hint means "not given", and one that scrubs
    away to nothing gets `FALLBACK_STEM`, exactly as a title does. The
    returned `title` stays the site's own - what the library calls the
    recording is the register stage's decision, not this function's.

    Progress is yt-dlp's own byte counts mapped onto 0..1, and it never goes
    backwards - a fragment that restarts would otherwise walk the bar back.
    When the server never said how big the file is, nothing is reported in
    between and the stage simply completes: the house rule (see
    `prepare.progress_fractions`) is that a stage which cannot measure itself
    reports 0 and then 1 rather than a convincing-looking guess.

    There is no cooperative cancel inside the download. yt-dlp wraps an
    exception raised from a progress hook into a `DownloadError`, which would
    land a cancelled job on the board as *failed* - so cancel is honoured at
    the stage boundary instead, exactly as `prepare` does and for the same
    reason.
    """
    url = ensure_http_url(url)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    if ydl is None:
        ydl = build_ydl(download_opts(dest, cookies_file))
    if public:
        public_only(ydl)
    if on_progress is not None:
        ydl.add_progress_hook(_progress_hook(on_progress))

    info = _extract(ydl, url, download=True)
    path = _rename_to_title(_locate(dest, info), title_hint or str(info.get("title") or ""))

    if on_progress is not None:
        # Unconditionally, the way `prepare.to_wav` closes: the file is here,
        # whether or not the hook ever fired.
        on_progress(1.0)

    return DownloadedMedia(
        path=path,
        title=str(info.get("title") or path.stem),
        duration=_as_float(info.get("duration")),
        uploader=_uploader(info),
        info=info,
    )


# --- names worth biasing the decoder towards -----------------------------------


def hotword_terms(info: dict) -> list[str]:
    """Capitalised words from the title, the uploader and the chapter titles.

    Only capitalised ones, and no digits, by the same rule
    `transcribe.name_candidates` applies to a filename: a lowercase word is
    not a name, and the hotword budget it would eat belongs to terms somebody
    actually chose. The description is deliberately not read - it is long,
    mostly links, and would swamp the budget with sponsors.

    Task 5's `glossary.compose_hotwords` is what spends these. Note the seam:
    the info-json lives in the job's work directory, which the runner deletes
    when the job ends, so whatever a later phase wants from it has to be taken
    while the `ingest_url` job is still running.
    """
    sources: list[str] = [
        str((info or {}).get("title") or ""),
        _uploader(info or {}),
    ]
    for chapter in (info or {}).get("chapters") or []:
        if isinstance(chapter, dict):
            sources.append(str(chapter.get("title") or ""))

    out: list[str] = []
    seen: set[str] = set()
    for text in sources:
        for token in _WORD.findall(text):
            key = token.casefold()
            if len(token) >= 2 and token[0].isupper() and key not in seen:
                seen.add(key)
                out.append(token)
    return out


# --- the plumbing --------------------------------------------------------------


def _extract(ydl, url: str, *, download: bool) -> dict:
    """`ydl.extract_info`, with every yt-dlp failure mapped onto a `UrlError`."""
    try:
        info = ydl.extract_info(url, download=download)
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
        raise _classify(exc) from exc
    if not isinstance(info, dict):
        raise DownloadFailed(f"yt-dlp returned nothing for {url}")
    return info


def _classify(exc: BaseException) -> UrlError:
    """Which of the three this yt-dlp exception is, and what to tell the user."""
    if isinstance(exc, UrlError):
        return exc

    original = exc
    exc_info = getattr(exc, "exc_info", None)
    if isinstance(exc_info, tuple) and len(exc_info) >= 2 and isinstance(exc_info[1], BaseException):
        original = exc_info[1]

    whole = f"{exc}\n{original}".lower()
    reason = _reason(str(exc))

    if _is_unsupported(original) or "unsupported url" in whole:
        return UnsupportedUrl(reason)
    if any(smell in whole for smell in _UNAVAILABLE):
        return Unavailable(reason)
    # Only the message decides this, never `is_stale()` on its own: blaming a
    # timeout on an old yt-dlp would send somebody to run a pip command that
    # cannot help, and would make this classification depend on the calendar.
    if any(smell in whole for smell in _STALE_EXTRACTOR):
        return DownloadFailed(f"{reason} - {stale_note()}")
    return DownloadFailed(reason)


def _is_unsupported(exc: BaseException) -> bool:
    """yt-dlp's own "no extractor for this" class, without importing it eagerly."""
    try:
        from yt_dlp.utils import UnsupportedError
    except ImportError:  # pragma: no cover - yt-dlp raised it, so it is installed
        return False
    return isinstance(exc, UnsupportedError)


def _reason(message: str) -> str:
    """The useful line of a yt-dlp error message.

    The last line, per the plan - but boilerplate lines are dropped first, so a
    two-line failure reports "Unable to extract nsig function" rather than the
    request to file a bug underneath it. `ERROR:` goes too: it is yt-dlp
    shouting, not information.
    """
    lines = [line.strip() for line in str(message).strip().splitlines() if line.strip()]
    useful = [
        line for line in lines
        if not any(line.lower().startswith(prefix) for prefix in _BOILERPLATE)
    ]
    line = (useful or lines or ["yt-dlp failed without saying why"])[-1]
    return line.removeprefix("ERROR:").strip()


def source_id_for(entry: dict) -> str | None:
    """The extractor-scoped identity of a listed entry, or None.

    `Youtube:kVXp6UNVPTo` for a flat YouTube entry (its `ie_key` and `id`);
    `Generic:<guid>` for a feed item, whose guid yt-dlp smuggles into the
    enclosure URL's fragment so that its own id is the guid and not the CDN's
    filename. Scoped by extractor because ids are unique only within one.
    Compared by the dialog's listing to say "in library", stored on the media
    row by the register stage; rendered nowhere.
    """
    ie_key, ident = entry.get("ie_key"), entry.get("id")
    if ie_key and ident:
        return f"{ie_key}:{ident}"
    guid = _smuggled_video_id(str(entry.get("url") or ""))
    return f"Generic:{guid}" if guid else None


def _smuggled_video_id(url: str) -> str | None:
    """The `force_videoid` yt-dlp smuggled into ``url``'s fragment, if any.

    Read with yt-dlp's own reader rather than a copy of its format, imported
    lazily like `_is_unsupported`: this runs inside `probe`, where yt-dlp is
    already loaded, and never in a process that has not paid for it.
    """
    if "#__youtubedl_smuggle=" not in url:
        return None
    try:
        from yt_dlp.utils import unsmuggle_url
    except ImportError:  # pragma: no cover - the URL came out of yt-dlp
        return None
    _bare, data = unsmuggle_url(url, {})
    value = (data or {}).get("force_videoid")
    return str(value) if value else None


def _entries(info: dict) -> list[dict]:
    """A playlist's items as ``{id, title, duration, url, timestamp,
    source_id}``; empty for a video.

    An entry with no URL to follow is dropped rather than kept: it would
    become a job whose only possible outcome is a failure. The URL is kept
    *verbatim*, fragment and all: yt-dlp smuggles a feed's guid, and other
    extractors a referer or headers, into that fragment, and the child that
    fetches the entry needs exactly what the listing said. Anything that
    compares or stores the URL strips the fragment itself.

    `timestamp` is epoch seconds from `timestamp`, else `release_timestamp`
    (a scheduled premiere), else None. A feed has one (its `pubDate`);
    YouTube's flat listing has none, and no flat source delivers an
    `upload_date`, so there is no third fallback.
    """
    raw = info.get("entries")
    if not raw:
        return []

    entries: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or entry.get("webpage_url")
        if not url:
            continue
        timestamp = _as_float(entry.get("timestamp"))
        if timestamp is None:
            timestamp = _as_float(entry.get("release_timestamp"))
        entries.append(
            {
                "id": str(entry.get("id") or ""),
                "title": str(entry.get("title") or ""),
                "duration": _as_float(entry.get("duration")),
                "url": str(url),
                "timestamp": timestamp,
                "source_id": source_id_for(entry),
                # A podcast feed's itunes fields, as yt-dlp's RSS reader maps
                # them; empty or None for anything that is not a feed.
                "episode": str(entry.get("episode") or ""),
                "episode_number": _as_int(entry.get("episode_number")),
                "season_number": _as_int(entry.get("season_number")),
            }
        )
    return entries


# "179: ", "#7 ", "Ep. 12 - ", "Episode 12 | " at the front of a title that
# already says its number; group 1 is the number it says.
_LEADING_NUMBER = re.compile(
    r"^\s*(?:#|ep\.?|episode)?\s*(\d+)\s*(?:[:.|\-–—]\s*|\s+)", re.IGNORECASE
)


def episode_names(entries: list[dict]) -> list[str]:
    """What each feed episode is called: its number and title from the feed.

    ``Ep 179 - The Courthouse - Revisited`` when the feed numbers its episodes
    (itunes:episode), using the feed's itunes:title when it has one and never
    saying the number twice; numbers padded to the widest in the feed so the
    files sort; the season only when the feed has more than one. An episode
    without a number gets its release date instead (``2026-09-11 - Title``):
    feeds are often a window onto a longer show, so a position in the list is
    not an episode number and is never used as one.
    """
    numbers = [e["episode_number"] for e in entries if e.get("episode_number") is not None]
    width = len(str(max(numbers))) if numbers else 0
    seasons = {e["season_number"] for e in entries if e.get("season_number") is not None}

    names = []
    for entry in entries:
        title = entry.get("episode") or entry.get("title") or ""
        number = entry.get("episode_number")
        if number is not None:
            leading = _LEADING_NUMBER.match(title)
            if leading and int(leading.group(1)) == number:
                title = title[leading.end():]
            prefix = f"Ep {number:0{width}d}"
            if len(seasons) > 1 and entry.get("season_number") is not None:
                prefix = f"S{entry['season_number']} {prefix}"
            names.append(f"{prefix} - {title}".rstrip(" -"))
        elif entry.get("timestamp") is not None:
            day = datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc).date()
            names.append(f"{day.isoformat()} - {title}".rstrip(" -"))
        else:
            names.append(title)
    return names


# What `episode_names` puts in front of a title: "Ep 07 - ", "S2 Ep 07 - ",
# "2026-09-11 - ". Group 0 is the whole prefix.
_EPISODE_PREFIX = re.compile(r"^(?:S\d+\s+)?(?:Ep\s+\d+|\d{4}-\d{2}-\d{2})\s+-\s+")


def spoken_title(name: str) -> str:
    """`episode_names`' name without its number, for the decoder's hotwords.

    "Ep 179 - The Courthouse" is what the library should call the recording,
    but `hotword_terms` would read "Ep" as a name worth biasing towards - two
    letters, capitalised, and in every episode of every feed. The number is
    for people and for sorting; only the title is worth telling the decoder.
    """
    return _EPISODE_PREFIX.sub("", name).strip()


def _uploader(info: dict) -> str:
    for key in ("uploader", "channel", "creator", "uploader_id"):
        value = (info or {}).get(key)
        if value:
            return str(value)
    return ""


def _progress_hook(on_progress: Callable[[float], None]) -> Callable[[dict], None]:
    """yt-dlp's progress dicts as monotonic 0..1 fractions."""
    highest = 0.0

    def hook(status: dict) -> None:
        nonlocal highest
        if not isinstance(status, dict):
            return
        total = _as_float(status.get("total_bytes")) or _as_float(
            status.get("total_bytes_estimate")
        )
        done = _as_float(status.get("downloaded_bytes"))
        if not total or total <= 0 or done is None or done < 0:
            return  # a size nobody knows is not a fraction
        fraction = min(1.0, done / total)
        if fraction < highest:
            return  # a fragment restarting is not the download going backwards
        highest = fraction
        on_progress(fraction)

    return hook


def _locate(dest: Path, info: dict) -> Path:
    """The file yt-dlp just wrote, whatever it decided to call it.

    yt-dlp reports it in `requested_downloads`; the glob is the fallback for a
    build or an extractor that does not, and it is why the output template uses
    a fixed stem - there is exactly one candidate to find.
    """
    for entry in info.get("requested_downloads") or []:
        if isinstance(entry, dict) and entry.get("filepath"):
            candidate = Path(entry["filepath"])
            if candidate.is_file():
                return candidate

    found = [
        path
        for path in sorted(dest.glob(f"{DOWNLOAD_STEM}.*"))
        if path.is_file() and not any(path.name.endswith(s) for s in _NOT_THE_MEDIA)
    ]
    if not found:
        raise DownloadFailed(
            f"yt-dlp reported success but left no media file in {dest}"
        )
    return found[0]


def _rename_to_title(path: Path, title: str) -> Path:
    """Rename the download to the scrubbed title; carries the sidecar with it.

    The scrub is the exporters' (`exports.options.scrub`): forbidden Win32
    characters, path separators and trailing dots gone, reserved device names
    suffixed. That is what makes a title of ``..\\..\\evil`` a name inside this
    directory rather than a way out of it.

    Imported here rather than at module scope on purpose: `scribe.exports`
    pulls python-docx and lxml in through its writer registry, and a download
    has no business loading a Word writer to find out what a filename may
    contain. `media.private_default` sets the same precedent.
    """
    from scribe.exports.options import scrub

    stem = scrub(title) or FALLBACK_STEM
    target = path.with_name(f"{stem}{path.suffix}")
    if target == path:
        return path

    os.replace(path, target)
    sidecar = path.with_suffix(INFO_JSON_SUFFIX)
    if sidecar.is_file():
        os.replace(sidecar, target.with_suffix(INFO_JSON_SUFFIX))
    return target


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """A count, or None: a negative number is no count at all."""
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None
