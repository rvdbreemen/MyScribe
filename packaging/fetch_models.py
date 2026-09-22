"""Put the shipped models in a payload, for an offline or enterprise build.

The default package ships *without* weights - they are a separate download the
app makes for itself (`scribe.models`), which keeps the artifact at about 55 MB
and means nothing gated is ever redistributed. This script is the opt-in for
the other case: a fleet of machines that will never reach huggingface.co, where
the weights have to travel inside the artifact.

It is deliberately a thin wrapper. The pins, the digest rule and the layout
live in `scribe/models.py` and its `models.json`, because the app needs all
three at runtime; duplicating them here is how a build machine and an
application come to disagree about what a model is.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scribe import models as app_models  # noqa: E402

Mismatch = app_models.ModelError


def ensure(dest_root: Path, *, token: str | None = None, only: str | None = None) -> dict:
    """Every pinned file present under ``dest_root`` and verified."""
    # Every catalogue entry, named: `ensure(None)` means "what this machine
    # loads" since TASK-089.16, and a payload is assembled for the platform it
    # ships to, not for the one it is built on.
    everything = [model.repo for model in app_models.catalogue().values()]
    app_models.ensure([only] if only else everything, token=token, where=dest_root)
    return {
        model.repo: {"revision": model.revision, "files": dict(model.files)}
        for model in app_models.catalogue().values()
        if (only is None or model.repo == only) and app_models.present(model, where=dest_root)
    }


def write_licences(dest: Path) -> list[Path]:
    """The credit each licence asks for, gathered where the build wants it."""
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for model in app_models.catalogue().values():
        path = dest / f"MODEL-{model.folder}-LICENSE.txt"
        path.write_text(
            f"{model.repo}\nrevision: {model.revision}\nlicence: {model.license}\n\n{model.credit}\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, required=True, help="where the models are assembled")
    parser.add_argument("--licences", type=Path, default=None, help="where to write the credits")
    parser.add_argument("--only", default=None, help="one repository, for a partial build")
    parser.add_argument("--token", default=None, help="Hugging Face token (default: HF_TOKEN)")
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    try:
        report = ensure(args.dest, token=token, only=args.only)
    except app_models.ModelError as exc:
        print(f"refusing to package: {exc} [{exc.reason}]", file=sys.stderr)
        return {"token": 3, "mismatch": 2}.get(exc.reason, 1)

    write_licences(args.licences or args.dest.parent / "licenses")
    for repo, got in report.items():
        print(f"[OK] {repo} @ {got['revision'][:12]}  {len(got['files'])} files verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
