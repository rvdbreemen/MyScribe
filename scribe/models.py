"""The weights this installation keeps, and how they get here (TASK-040.05).

The package ships without them. A 55 MB download that works is a better first
impression than a 1.7 GB one that might, and the pyannote pipeline is gated on
Hugging Face: fetching it with the user's own token, under conditions they
accepted themselves, is the difference between using a model and redistributing
one. So the weights are a separate download, and this module is it.

`models.json` beside this file pins each model by *revision* - a commit sha,
never a branch - and each file by sha256. A file that does not match its pin is
deleted rather than kept: on a machine that is about to go offline, the copy
here is the only copy there will be, and a wrong one is worse than a missing
one because nothing will look for it again.

Where things land is not a new convention. `paths.MODELS_DIR/pyannote` is what
`diarize.local_weights_dir()` has always looked in first, so a fetched pipeline
is simply found; and a user who put their own copy there keeps it, because a
file whose digest already matches is never fetched again.

This module reaches the network, so a job that needs it runs in a runner child
(ADR-001). The setup flow calls it directly, which is a download and not a
model load.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

from scribe import accel, credentials, paths

HERE = Path(__file__).resolve().parent
HUB = "https://huggingface.co"
CHUNK = 1 << 20
RETRIES = 3
"""How often one file is asked for again after the connection dropped part way
through it. Each try resumes from the `.part`, so three is three chances at the
rest of a file and never three times the file."""

# The diarization pipeline, named here because `folder` and the doctor both
# need to know which entry is the gated one. The *transcription* model is
# deliberately not named in this file: ADR-004 puts that name in
# `stages/transcribe.py` and nowhere else, and its own test greps the package
# to keep it that way. `models.json` pins it as data, the way `tools.json`
# pins a binary version.
DIARIZE = "pyannote/speaker-diarization-community-1"

NOT_MLX = "not-mlx"
"""What `backend_here()` answers where the MLX path is impossible. It is not a
backend name: which of cuda or cpu such a machine uses is the doctor's answer,
and asking costs a torch import."""

DEFAULT_TIER = "turbo"
"""Which quality setting a caller that names none is asking about - the stored
default of a fresh installation, and what `scribe.web.transcribe_dialog` writes
when somebody chooses. The tiers themselves are `tier` keys in `models.json`."""

EXIT_CODES = {"mismatch": 2, "token": 3, "disk": 4}
"""What a command exits with per `ModelError.reason`; anything else is 1. Named
here because `scribe.setup` returns the same codes for the same failure, and
two copies of a table are how they come to disagree. 2 is also argparse's usage
error, which `--hf-token` already uses."""


class ModelError(RuntimeError):
    """A fetch that could not finish, with `reason` naming which kind it was.

    Four kinds, because four different things have to be done about them:
    `offline` (nothing to do here but try again on a connection), `token` (the
    gate has not been accepted, or the token is not this account's), `disk`
    (there is no room for the file, here or on the volume it lands on) and
    `mismatch` (the file that arrived is not the file that was pinned).

    `disk` was split off `offline` because they read as the same sentence and
    are not: "could not be downloaded" sends somebody to look at a connection
    that is working.
    """

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Model:
    repo: str
    revision: str
    files: dict[str, dict]
    license: str
    credit: str
    gated: bool
    backends: tuple[str, ...] | None = None
    """Which transcribers load these weights; None means every one of them,
    which is what the diarization pipeline is."""
    tier: str | None = None
    """Which quality setting asks for them; None means every setting."""
    alias: str | None = None
    """The id the loader itself requests - a faster-whisper model name. It is
    data in `models.json` and never a literal in this package, so ADR-004's
    grep still finds the default model name spelled in one file."""

    @property
    def folder(self) -> str:
        """Where it lands under `MODELS_DIR`.

        pyannote keeps the bare name the diarize stage looks for; anything else
        keeps its Hub path so two organisations cannot collide.
        """
        return "pyannote" if self.repo.startswith("pyannote/") else self.repo.replace("/", "--")

    @property
    def bytes_total(self) -> int:
        return sum(int(f.get("size") or 0) for f in self.files.values())


def catalogue() -> dict[str, Model]:
    raw = json.loads((HERE / "models.json").read_text(encoding="utf-8"))
    return {
        name: Model(
            repo=name,
            revision=spec["revision"],
            files=spec["files"],
            license=spec["license"],
            credit=spec["credit"],
            gated=name.startswith("pyannote/"),
            backends=tuple(spec["backends"]) if spec.get("backends") else None,
            tier=spec.get("tier"),
            alias=spec.get("alias"),
        )
        for name, spec in raw.items()
        if not name.startswith("_")
    }


def backend_here() -> str:
    """Which transcriber this platform would load - `mlx` or `NOT_MLX`.

    `accel.transcription_backend()` is deliberately not asked. It tries CUDA
    first, and `cuda_available()` imports torch: 4.9 s in a cold process,
    measured on 2026-09-22 against 51 ms for all the rest of a plan. Worse than
    slow, it is forbidden - `check_models` reads this and sits in the doctor's
    WEB_SAFE_CHECKS, so asking it would import torch into the web process every
    time a settings page renders (ADR-001, Must Not). On Apple Silicon, where
    `mlx_available()` is true, that is exactly what would happen.

    Nothing is lost by not asking. mlx-whisper is the only platform-bound
    loader in the catalogue and it exists on Apple Silicon alone
    (`accel.is_apple_silicon`), where there is no CUDA; everywhere else cuda
    and cpu load the same files, so the catalogue cannot tell them apart and
    does not need to. `load_model` still asks the real probe, because it is
    about to load a model anyway.
    """
    return "mlx" if accel.mlx_available() else NOT_MLX


def loads_here(model: Model, backend: str) -> bool:
    """Would this transcriber load these weights?

    `NOT_MLX` is not a backend, so it is answered by what it rules out rather
    than by what it names: everything except the Apple conversions.
    """
    if model.backends is None:
        return True
    if backend == NOT_MLX:
        return "mlx" not in model.backends
    return backend in model.backends


def wanted_here(*, backend: str | None = None, tier: str | None = None) -> list[Model]:
    """The catalogue rows this machine would download, at this quality setting.

    The one place the question is answered. It used to be answered twice - once
    in the plan and not at all in the doctor - and a doctor that asked for
    1.6 GB of weights this platform cannot load was the result.
    """
    backend = backend or backend_here()
    tier = tier or DEFAULT_TIER
    return [
        m
        for m in catalogue().values()
        if loads_here(m, backend) and m.tier in (None, tier)
    ]


def local_dir(alias: str | None, backend: str, *, where: Path | None = None) -> Path | None:
    """The folder a loader should open for ``alias``, or None to let it resolve
    the name by itself.

    The copy `ensure()` wrote is the copy that gets loaded, so a machine that
    downloaded its weights while somebody watched does not download them a
    second time inside the first job, with no progress shown. A folder that is
    not whole is not an answer: the loader would open it and fail, where the
    bare name still resolves through the hub cache.
    """
    name = (alias or "").strip()
    if not name:
        return None
    base = where or root()
    for model in catalogue().values():
        if model.alias == name and loads_here(model, backend) and present(model, where=base):
            return base / model.folder
    return None


def root() -> Path:
    return paths.MODELS_DIR


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            sha.update(block)
    return sha.hexdigest()


def hub_snapshot(model: Model) -> Path | None:
    """The huggingface_hub cache copy of ``model`` at its pinned revision, if any.

    Found by validating on the Mac: the doctor said "1.6 GB still to download"
    on a machine that had just transcribed with that very model. The weights
    were where `huggingface_hub` puts them - `models--org--name/snapshots/<sha>`
    - rather than under MODELS_DIR, and a download this app already has is not
    a download to ask for again.

    The revision is the pin, so a cache holding some *other* revision is
    correctly not a hit. `doctor.hf_cache_dir` owns the precedence between
    HF_HUB_CACHE, HF_HOME and the default, and is imported here rather than
    copied: two spellings of a cache location is how they come to disagree.
    """
    from scribe import doctor

    try:
        base = doctor.hf_cache_dir() / f"models--{model.repo.replace('/', '--')}" / "snapshots" / model.revision
    except Exception:  # noqa: BLE001 - an unreadable environment is simply not a hit
        return None
    if not base.is_dir():
        return None
    for rel, pin in model.files.items():
        path = base / rel
        if not path.exists():
            return None
        size = int(pin.get("size") or 0)
        if size and path.stat().st_size != size:
            return None
    return base


def hub_any_revision(model: Model) -> tuple[str, Path] | None:
    """A huggingface_hub cache copy of ``model`` at *another* revision, whole:
    every pinned file there by name and size. Returns (revision, folder).

    Not `present` - that answers for the pinned revision only, and stays the
    question setup and the doctor ask. This is the narrower question a job asks
    before fetching (PR #9 review, finding 2): the loader resolves the bare
    name through this cache, as it did before jobs fetched anything, so a copy
    here is a copy it can use, and downloading ~3 GB again for a revision
    label would be the regression.
    """
    from scribe import doctor

    try:
        snapshots = doctor.hf_cache_dir() / f"models--{model.repo.replace('/', '--')}" / "snapshots"
        candidates = sorted(snapshots.iterdir()) if snapshots.is_dir() else []
    except OSError:
        return None
    for folder in candidates:
        if folder.name == model.revision or not folder.is_dir():
            continue
        whole = all(
            (folder / rel).exists()
            and (not int(pin.get("size") or 0) or (folder / rel).stat().st_size == int(pin["size"]))
            for rel, pin in model.files.items()
        )
        if whole:
            return folder.name, folder
    return None


def present(model: Model, *, where: Path | None = None, verify: bool = False) -> bool:
    """Is every pinned file here? The question "must I download?".

    `verify` decides how hard the question is asked, and the default is the
    cheap one on purpose: a settings page and a doctor card ask it on every
    render, and hashing 1.6 GB of weights to draw a table would make the page
    take seconds and the disk work for nothing. Size and existence is what a
    status line needs.

    The expensive answer is for the moment it matters - `ensure` verifies every
    byte it writes, and refuses anything that does not match. A file that is
    the right size and the wrong content is caught there, which is before
    anything has relied on it.

    One exception, and it is deliberate: a copy in the huggingface_hub cache is
    accepted on its revision, its names and its sizes whatever `verify` says.
    This app did not put it there and does not maintain it - huggingface_hub
    does its own integrity work - and hashing 1.6 GB of somebody else's cache
    to answer "must I download?" would cost more than the question is worth.
    """
    if where is None and hub_snapshot(model) is not None:
        return True
    base = (where or root()) / model.folder
    for rel, pin in model.files.items():
        path = base / rel
        if not path.exists():
            return False
        if verify:
            if digest(path) != pin["sha256"]:
                return False
        elif int(pin.get("size") or 0) and path.stat().st_size != int(pin["size"]):
            return False
    return True


def missing(
    *,
    where: Path | None = None,
    verify: bool = False,
    backend: str | None = None,
    tier: str | None = None,
) -> list[Model]:
    """What this machine still has to download. The same predicate `--fetch`
    walks, so "nothing is missing" and "nothing is fetched" cannot disagree."""
    return [
        m for m in wanted_here(backend=backend, tier=tier) if not present(m, where=where, verify=verify)
    ]


def status(*, where: Path | None = None, backend: str | None = None, tier: str | None = None) -> list[dict]:
    """What the setup screen and the doctor print: one row per model.

    Every catalogue row, filtered nowhere: an entry this machine does not load
    is still named, with `wanted` false, because a plan that silently drops a
    row cannot be read against `models.json`. Which rows *count* is what
    `wanted` says, and each reader decides what to do with a row that is not.
    """
    backend = backend or backend_here()
    tier = tier or DEFAULT_TIER
    return [
        {
            "repo": m.repo,
            "here": present(m, where=where),
            "at": str(hub_snapshot(m) or ((where or root()) / m.folder)),
            "bytes": m.bytes_total,
            "gated": m.gated,
            "licence": m.license,
            "tier": m.tier,
            "loads_here": loads_here(m, backend),
            "wanted": loads_here(m, backend) and m.tier in (None, tier),
        }
        for m in catalogue().values()
    ]


def origin_of(url: str) -> tuple[str, str]:
    """Scheme and host together - what "the same place" means for a credential.

    The host alone is not enough. `https://huggingface.co/x` and
    `http://huggingface.co/x` have the same netloc, so a redirect that only
    drops the s would read as the same place and the token would go out over
    the wire in the clear, which is the one way of losing it that costs the
    credential itself.
    """
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


class DropAuthAcrossHosts(urllib.request.HTTPRedirectHandler):
    """A redirect handler that does not carry the token anywhere else.

    urllib copies a request's headers into the redirected request, and
    `Authorization` is one of them, so a `Location` pointing anywhere else is
    all it takes for this account's Hugging Face token to be handed to whatever
    host that is. Same origin, same token; anything else, no header.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        followed = super().redirect_request(req, fp, code, msg, headers, newurl)
        if followed is not None and origin_of(newurl) != origin_of(req.full_url):
            followed.headers.pop("Authorization", None)
            followed.unredirected_hdrs.pop("Authorization", None)
        return followed


def _one_request(
    model: Model, url: str, part: Path, token: str | None, resume_from: int, on_bytes: Callable[[int], None]
) -> bool:
    """One GET appended to ``part``. True when the body ended early.

    A short body is not an error urllib reports: `HTTPResponse.read` returns
    b"" and closes when the stream ends before Content-Length is satisfied, so
    the only way to notice is to count what arrived and compare.
    """
    # The token is this account's credential for the one repository whose
    # conditions it accepted. A public file needs none, and sending one
    # anyway tells every repository who is downloading.
    headers = {"Authorization": f"Bearer {token}"} if token and model.gated else {}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(DropAuthAcrossHosts)
    try:
        with opener.open(request, timeout=120) as response:
            announced = int(response.headers.get("Content-Length") or 0)
            # A server that ignores the Range header answers 200 with the whole
            # file, and appending that to what is already there would build a
            # file of the right length out of the wrong bytes.
            mode = "ab" if resume_from and response.status == 206 else "wb"
            arrived = 0
            with part.open(mode) as out:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    arrived += len(block)
                    on_bytes(len(block))
            return bool(announced) and arrived != announced
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and resume_from:
            # "Range not satisfiable": what is on disk is longer than the file
            # the hub will serve from here. The `.part` carries the revision it
            # was begun for, so this is the network's own answer - a proxy, or
            # a server that once sent more than it announced - and not a
            # leftover of ours. It is thrown away, so the next try starts over.
            part.unlink(missing_ok=True)
            return True
        # Everything else keeps the `.part`. A 500 is as temporary as a dropped
        # connection, and a token that was refused will be replaced: deleting
        # what had already arrived made every one of those cost the whole file
        # again.
        # Every HTTP failure names a configured proxy, the gated ones included:
        # a proxy that refuses a request answers HTTP too, and 403 is what it
        # commonly answers for a host it blocks - the same status the hub uses
        # for conditions that were not accepted. This side cannot tell which of
        # the two answered, so somebody sent to go and accept conditions is
        # told a proxy stood in the way as well.
        if exc.code == 401:
            raise ModelError(
                f"{model.repo}: this Hugging Face token was not accepted - it is missing, expired or "
                f"belongs to another account{credentials.proxy_note()}",
                reason="token",
            ) from exc
        if exc.code == 403:
            raise ModelError(
                f"{model.repo} is gated: this account has not accepted the conditions at "
                f"https://hf.co/{model.repo}{credentials.proxy_note()}",
                reason="token",
            ) from exc
        raise ModelError(
            f"{model.repo}: the hub answered HTTP {exc.code}{credentials.proxy_note()}", reason="offline"
        ) from exc
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOSPC:
            # The `.part` is why there is no room. Keeping it on a disk with
            # nothing free helps nobody: the next try cannot finish it either,
            # and the machine stays full until somebody finds the file.
            part.unlink(missing_ok=True)
            raise ModelError(
                f"{model.repo}/{part.name}: the disk filled up while it was being written",
                reason="disk",
            ) from exc
        # A reset or a timeout keeps what arrived. This used to delete it, so
        # the resume `fetch_file` promises worked only for a body that ended
        # early without raising - which is not the drop a long download over a
        # flaky line is most likely to hit.
        raise ModelError(
            f"{model.repo}: could not be downloaded ({exc}){credentials.proxy_note()}", reason="offline"
        ) from exc


@contextlib.contextmanager
def _fetch_lock(folder: Path):
    """One writer per model folder, across processes: setup's download and a
    job's (TASK-107.04) used to meet in the same `.part` file. A lock file in
    the folder, held with the OS's own exclusive lock - `msvcrt.locking` on
    Windows, `fcntl.flock` elsewhere - and released when the block ends,
    including by a crash, because the OS drops it with the handle."""
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / ".fetch.lock", "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue  # LK_LOCK gives up after ~10 s; keep waiting
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def fetch_file(model: Model, rel: str, dest: Path, token: str | None, on_bytes: Callable[[int], None]) -> None:
    """One pinned file, streamed to ``dest``.

    Written to a neighbouring `.part` and moved into place only once whole, so
    an interrupted download never leaves a file that looks finished - the
    failure mode that would otherwise be indistinguishable from a good copy
    until a transcription produced nonsense.

    The `.part` is also what a retry resumes from. A connection that dropped at
    1.5 GB used to cost the whole 1.6 GB again, and reported itself as a pin
    mismatch - "it was deleted rather than used" - which reads as somebody
    having interfered with the download rather than as a dropped connection.

    It carries the revision it was begun for, because bytes are only a prefix
    of the file they were fetched for. Named per file alone, a `.part` outlived
    a re-pin and the next run appended one revision's bytes to another's: a
    file of exactly the right length and the wrong content, which failed its
    digest with that same sentence about being deleted rather than used.
    """
    url = f"{HUB}/{model.repo}/resolve/{model.revision}/{rel}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.{model.revision[:12]}.part")
    for stale in dest.parent.glob(f"{dest.name}*.part"):
        # Another revision's leftover, or one from before the revision was in
        # the name at all. Nothing will ever ask for either again, and a
        # gigabyte nobody looks at is not a thing to leave on somebody's disk.
        if stale != part:
            stale.unlink(missing_ok=True)
    for _attempt in range(RETRIES):
        resume_from = part.stat().st_size if part.exists() else 0
        if not _one_request(model, url, part, token, resume_from, on_bytes):
            part.replace(dest)
            return
    # The `.part` stays: the next run asks for the rest of it, not for all of
    # it, and on a connection that keeps dropping that is the difference
    # between finishing eventually and never finishing.
    raise ModelError(
        f"{model.repo}/{rel}: the connection dropped before the file was whole, "
        f"{RETRIES} times over{credentials.proxy_note()}",
        reason="offline",
    )


def room_for(wanted: Iterable[Model], base: Path) -> None:
    """Enough room on the volume the files land on, asked before the first byte.

    A download that fills the disk and fails at the last gigabyte has cost the
    connection and left the machine worse than it found it. Both numbers are
    known here, so the refusal can give them rather than say "could not be
    downloaded" about a connection that is working.
    """
    needed = sum(m.bytes_total for m in wanted)
    if not needed:
        return
    base.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(base).free
    if free < needed:
        raise ModelError(
            f"{human(needed)} is needed and {human(free)} is free on {base}; nothing was downloaded",
            reason="disk",
        )


def ensure(
    wanted: Iterable[str] | None = None,
    *,
    token: str | None = None,
    where: Path | None = None,
    backend: str | None = None,
    tier: str | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Fetch what is missing. Returns the repos that were downloaded.

    Naming no repositories means the ones this platform and tier load, the same
    set `missing()` reports - so "1.6 GB still to download" and what `--fetch`
    actually fetches are one answer. Naming them is the payload build's
    question and is answered literally.

    `on_progress(repo, done, total)` is called as bytes arrive, which is what
    lets a 1.6 GB download look like something happening rather than a frozen
    window.
    """
    base = where or root()
    token = token if token is not None else default_token()
    if wanted is None:
        todo = wanted_here(backend=backend, tier=tier)
    else:
        named = set(wanted)
        todo = [m for m in catalogue().values() if m.repo in named]
    # Gated first. Walked in catalogue order, the public gigabyte arrived
    # before the gated pipeline could refuse, so a sitting with no token spent
    # its download on weights and then ended in an error anyway.
    todo.sort(key=lambda m: not m.gated)
    # `where` and not `base`: a copy in the huggingface_hub cache is a copy
    # this installation can load, which is exactly what `status()` reports, and
    # ensure used to re-download it. `--dest` still means that folder only,
    # because then `where` is not None and the cache is not an answer.
    outstanding = [m for m in todo if not present(m, where=where, verify=True)]

    # Everything that can refuse for free refuses first: a token that is not
    # there and a disk that has no room both cost nothing to find out, and
    # finding out after 1.6 GB is the fault this ordering exists for.
    for model in outstanding:
        if model.gated and not token:
            raise ModelError(
                f"{model.repo} is gated and no Hugging Face token is set; "
                f"accept the conditions at https://hf.co/{model.repo} and save a token in Settings",
                reason="token",
            )
    room_for(outstanding, base)

    done: list[str] = []
    for model in outstanding:
        with _fetch_lock(base / model.folder):
            # Taken in turn (PR #9 review): setup and a job can both fetch one
            # model, and both resume into the same .part. Whoever waited finds
            # the work done and moves on.
            if present(model, where=base, verify=True):
                continue
            sent = 0
            total = model.bytes_total
            for rel, pin in model.files.items():
                target = base / model.folder / rel
                if target.exists() and digest(target) == pin["sha256"]:
                    sent += int(pin.get("size") or 0)
                    continue
                # `sent` counts whole files; `within` accumulates the chunks of the
                # one being written. Adding a raw chunk size to `sent` was the first
                # version and it made the bar go backwards mid-file, which is worse
                # than no bar: it reads as a download restarting.
                within = {"bytes": 0}

                def arrived(n: int, repo: str = model.repo, base: int = sent) -> None:
                    within["bytes"] += n
                    if on_progress:
                        on_progress(repo, min(base + within["bytes"], total), total)

                fetch_file(model, rel, target, token, arrived)
                found = digest(target)
                if found != pin["sha256"]:
                    # Deleted, not kept: a wrong weight file that stays is one
                    # nothing will ever look for again.
                    target.unlink(missing_ok=True)
                    raise ModelError(
                        f"{model.repo}/{rel} does not match its pin at revision {model.revision}; "
                        "it was deleted rather than used",
                        reason="mismatch",
                    )
                sent += int(pin.get("size") or 0)
                if on_progress:
                    on_progress(model.repo, min(sent, total), total)
            done.append(model.repo)

    # The credit goes beside every copy that is in this folder, not only beside
    # the ones this run wrote: a user who put the weights there by hand has the
    # same files and the same licence. A model that is only in the
    # huggingface_hub cache gets none, and that is the point - this app placed
    # no copy there, so there is nothing of its doing to attribute.
    for model in todo:
        if present(model, where=base):
            write_credit(model, base)
    return done


def write_credit(model: Model, base: Path) -> Path:
    """The attribution the licence asks for, next to the weights it covers."""
    path = base / model.folder / "LICENCE-AND-CREDIT.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{model.repo}\nrevision: {model.revision}\nlicence: {model.license}\n\n{model.credit}\n",
        encoding="utf-8",
    )
    return path


def default_token() -> str | None:
    """The Hugging Face token this machine has, wherever it keeps it.

    Including the settings row, which is the whole of TASK-089.04 for this
    command: `python -m scribe.models --fetch` is what the doctor's message
    tells the user to run *after* saving the token in Settings, and it used to
    read the environment only - so it answered "no Hugging Face token is set"
    at the token they had just saved. The library's database is opened here
    because this command has no connection of its own; not having one is
    simply no row.
    """
    with credentials.library_db() as conn:
        return credentials.resolve(conn, credentials.HUGGINGFACE).value


def human(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="scribe.models", description="Download the model weights MyScribe uses.")
    parser.add_argument("--fetch", action="store_true", help="download whatever is missing")
    parser.add_argument("--only", action="append", help="one repository (repeatable)")
    parser.add_argument("--dest", type=Path, default=None, help="where to put them (default: the data directory)")
    args = parser.parse_args(argv)

    from scribe import env

    env.load_dotenv()
    # None, not root(): a caller who named no directory is asking whether this
    # installation can use the model, and a copy in the huggingface_hub cache
    # answers that. `--dest` is the other question - assemble it *here* - and
    # there a cache copy somewhere else is not an answer.
    base = args.dest

    if not args.fetch:
        # Every catalogue row is printed, the ones this machine does not load
        # included: a listing that showed only the wanted rows could not be
        # read against `models.json`, and "MISSING" against weights nothing
        # here can load is what sent people to download 1.6 GB for nothing.
        for row in status(where=base):
            mark = "have" if row["here"] else ("MISSING" if row["wanted"] else "-")
            gate = " (needs your Hugging Face token)" if row["gated"] and not row["here"] else ""
            if row["wanted"]:
                note = ""
            elif not row["loads_here"]:
                note = " (not loaded on this platform)"
            else:
                note = f" (not loaded at the {DEFAULT_TIER} quality setting)"
            print(f"[{mark:>7}] {row['repo']:<45} {human(row['bytes']):>8}  {row['licence']}{gate}{note}")
        outstanding = sum(m.bytes_total for m in missing(where=base))
        print(f"\n{human(outstanding)} to download" if outstanding else "\nEverything is here.")
        return 0

    last = {"line": ""}

    def progress(repo: str, done_bytes: int, total: int) -> None:
        pct = 100 * done_bytes / total if total else 0
        line = f"  {repo:<45} {pct:5.1f}%  {human(done_bytes)}/{human(total)}"
        if line != last["line"]:
            print(line, end="\r", flush=True)
            last["line"] = line

    # `--dest` assembles a payload, and for a platform that is not necessarily
    # this one, so it fetches the whole catalogue. Without it the question is
    # what this machine loads, which is what `ensure(None)` answers.
    only = args.only or ([m.repo for m in catalogue().values()] if args.dest else None)

    try:
        got = ensure(only, where=base, on_progress=progress)
    except ModelError as exc:
        print(f"\n{exc}")
        return EXIT_CODES.get(exc.reason, 1)
    print("\n" + (f"downloaded: {', '.join(got)}" if got else "nothing to do; everything was already here"))
    return 0


if __name__ == "__main__":
    # `.env` may name the data directory, and this module imported
    # scribe.paths before anybody read the file: see paths.refresh(). Here and
    # not in main(), which a caller may run against paths of their own.
    from scribe import env

    env.bootstrap()
    paths.refresh()
    raise SystemExit(main())
