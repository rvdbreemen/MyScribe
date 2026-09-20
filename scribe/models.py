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

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from scribe import paths

HERE = Path(__file__).resolve().parent
HUB = "https://huggingface.co"
CHUNK = 1 << 20

# The diarization pipeline, named here because `folder` and the doctor both
# need to know which entry is the gated one. The *transcription* model is
# deliberately not named in this file: ADR-004 puts that name in
# `stages/transcribe.py` and nowhere else, and its own test greps the package
# to keep it that way. `models.json` pins it as data, the way `tools.json`
# pins a binary version.
DIARIZE = "pyannote/speaker-diarization-community-1"


class ModelError(RuntimeError):
    """A fetch that could not finish, with `reason` naming which kind it was.

    Three kinds, because three different things have to be done about them:
    `offline` (nothing to do here but try again on a connection), `token` (the
    gate has not been accepted, or the token is not this account's) and
    `mismatch` (the file that arrived is not the file that was pinned).
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
        )
        for name, spec in raw.items()
        if not name.startswith("_")
    }


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


def missing(*, where: Path | None = None, verify: bool = False) -> list[Model]:
    return [m for m in catalogue().values() if not present(m, where=where, verify=verify)]


def status(*, where: Path | None = None) -> list[dict]:
    """What the setup screen and the doctor print: one row per model."""
    return [
        {
            "repo": m.repo,
            "here": present(m, where=where),
            "at": str(hub_snapshot(m) or ((where or root()) / m.folder)),
            "bytes": m.bytes_total,
            "gated": m.gated,
            "licence": m.license,
        }
        for m in catalogue().values()
    ]


def fetch_file(model: Model, rel: str, dest: Path, token: str | None, on_bytes: Callable[[int], None]) -> None:
    """One pinned file, streamed to ``dest``.

    Written to a neighbouring `.part` and moved into place only once whole, so
    an interrupted download never leaves a file that looks finished - the
    failure mode that would otherwise be indistinguishable from a good copy
    until a transcription produced nonsense.
    """
    url = f"{HUB}/{model.repo}/resolve/{model.revision}/{rel}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as out:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                out.write(block)
                on_bytes(len(block))
    except urllib.error.HTTPError as exc:
        part.unlink(missing_ok=True)
        if exc.code in (401, 403):
            raise ModelError(
                f"{model.repo} is gated: accept the conditions at https://hf.co/{model.repo} "
                "with the same account, and give MyScribe that account's token",
                reason="token",
            ) from exc
        raise ModelError(f"{model.repo}: the hub answered HTTP {exc.code}", reason="offline") from exc
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise ModelError(f"{model.repo}: could not be downloaded ({exc})", reason="offline") from exc
    part.replace(dest)


def ensure(
    wanted: Iterable[str] | None = None,
    *,
    token: str | None = None,
    where: Path | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Fetch what is missing. Returns the repos that were downloaded.

    `on_progress(repo, done, total)` is called as bytes arrive, which is what
    lets a 1.6 GB download look like something happening rather than a frozen
    window.
    """
    base = where or root()
    token = token if token is not None else default_token()
    done: list[str] = []
    for model in catalogue().values():
        if wanted is not None and model.repo not in set(wanted):
            continue
        if present(model, where=base, verify=True):
            continue
        if model.gated and not token:
            raise ModelError(
                f"{model.repo} is gated and no Hugging Face token is set; "
                f"accept the conditions at https://hf.co/{model.repo} and save a token in Settings",
                reason="token",
            )
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
        write_credit(model, base)
        done.append(model.repo)
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
    for name in ("HF_TOKEN", "HUGGINGFACE_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


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
        for row in status(where=base):
            mark = "have" if row["here"] else "MISSING"
            gate = " (needs your Hugging Face token)" if row["gated"] and not row["here"] else ""
            print(f"[{mark:>7}] {row['repo']:<45} {human(row['bytes']):>8}  {row['licence']}{gate}")
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

    try:
        got = ensure(args.only, where=base, on_progress=progress)
    except ModelError as exc:
        print(f"\n{exc}")
        return {"token": 3, "mismatch": 2}.get(exc.reason, 1)
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
