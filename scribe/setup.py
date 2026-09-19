"""First-run setup: the few answers that decide whether this machine works.

The launcher prepares an environment and opens a browser. It never asked
anything, and two of the failures that cost the most on a clean machine came
straight out of that: the diarize stage needs a Hugging Face token that nothing
requested, and the weights are a separate download that nothing offered to
make (TASK-040.06).

Four answers, and no more than four. Each one is a thing the app cannot work
out for itself and would otherwise discover at the worst moment:

* the Hugging Face token, without which speaker separation cannot start;
* which provider answers questions about a transcript, because sending a
  private recording to a cloud model is not a default anybody should inherit;
* the model tier, so a small machine is not committed to a large download;
* whether to fetch the weights now, while somebody is watching, rather than
  inside the first job.

The launcher is frozen and stdlib-only (ADR-011), so it cannot write a setting
row or read `.env` for itself - it runs this module in the app's environment
instead, the same way it runs the doctor. Everything here is also reachable
from Settings afterwards: an answer given once at the start must never be the
only place it can be given.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from scribe import db, env, models, paths
from scribe.stages import diarize
from scribe.web import ai_ui, transcribe_dialog

STAMP = "setup.json"
"""Written into the data directory when setup finishes, so the launcher asks
once rather than every start. Its contents are the answers, which is also what
makes `--status` able to say what was chosen and when."""

TIERS = ("turbo", "max")


@dataclass(frozen=True)
class Answers:
    hf_token: str = ""
    provider: str = ""
    tier: str = ""
    diarize: bool | None = None
    fetch_models: bool = False
    wanted: list[str] = field(default_factory=list)


def stamp_path() -> Path:
    return paths.DATA_DIR / STAMP


def done() -> bool:
    return stamp_path().exists()


def needed(conn: sqlite3.Connection | None = None) -> dict:
    """What setup would still ask about, as data a caller can render.

    Deliberately a question about *this machine now*, not about whether setup
    has been run: a token can be removed and weights deleted, and the honest
    answer then is that they are missing again.
    """
    env.load_dotenv()
    token = diarize.hf_token(conn)
    absent = models.status()
    return {
        "asked_before": done(),
        "hf_token": bool(token),
        "provider": (ai_ui.setting_get(conn, ai_ui.PROVIDER_SETTING) if conn else "") or "",
        "models": absent,
        "to_download": sum(row["bytes"] for row in absent if not row["here"]),
        "diarization_possible": bool(token) or diarize.local_weights_dir().joinpath("config.yaml").exists(),
    }


def write_token(token: str, *, env_file: Path | None = None) -> Path:
    """Put the token in `.env`, which is where the app reads it from.

    The settings row would do as well and Settings writes that one, but the
    launcher's first run happens before there is a browser to type into, and
    `.env` is the file the per-user home already carries (ADR-011).

    Rewritten in place rather than appended: a second run must not leave two
    `HF_TOKEN=` lines with different values, where the last one silently wins.
    """
    path = env_file or Path(env.os.environ.get(env.PATH_VARIABLE) or env.DEFAULT_PATH)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith("HF_TOKEN=") and not replaced:
            out.append(f"HF_TOKEN={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"HF_TOKEN={token}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def apply(answers: Answers, conn: sqlite3.Connection, *, env_file: Path | None = None,
          on_progress=None) -> dict:
    """Write the answers down and do what they ask for. Returns a report."""
    report: dict = {"wrote": [], "downloaded": []}

    if answers.hf_token.strip():
        write_token(answers.hf_token.strip(), env_file=env_file)
        ai_ui.setting_put(conn, diarize.SETTING_TOKEN, answers.hf_token.strip())
        report["wrote"].append("hf_token")

    if answers.provider:
        if answers.provider not in ai_ui.llm.PROVIDERS:
            raise ValueError(f"unknown provider {answers.provider!r}")
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, answers.provider)
        report["wrote"].append("provider")

    if answers.tier or answers.diarize is not None:
        current = transcribe_dialog.read_defaults(conn)
        tier = answers.tier or current.tier
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r}")
        wanted_diarize = current.diarize if answers.diarize is None else answers.diarize
        # A pydantic model, not a dataclass: `scribe.options.TranscribeOptions`
        # is the one shape the dialog, the API and this share, and copying it
        # its own way keeps its validation in play.
        updated = current.model_copy(update={"tier": tier, "diarize": wanted_diarize})
        transcribe_dialog.save_defaults(conn, updated)
        report["wrote"].append("defaults")

    if answers.fetch_models:
        token = answers.hf_token.strip() or diarize.hf_token(conn)
        report["downloaded"] = models.ensure(answers.wanted or None, token=token, on_progress=on_progress)

    stamp_path().parent.mkdir(parents=True, exist_ok=True)
    stamp_path().write_text(
        json.dumps(
            {
                "provider": answers.provider,
                "tier": answers.tier,
                "hf_token": bool(answers.hf_token.strip()),
                "fetched": report["downloaded"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scribe.setup", description=__doc__.split("\n\n")[0])
    parser.add_argument("--status", action="store_true", help="print what setup would ask about, as JSON")
    parser.add_argument("--hf-token", default="", help="Hugging Face token for speaker separation")
    parser.add_argument("--provider", default="", help="who answers questions about a transcript")
    parser.add_argument("--tier", default="", choices=("", *TIERS), help="which transcription model")
    parser.add_argument("--diarize", dest="diarize", action="store_true", default=None)
    parser.add_argument("--no-diarize", dest="diarize", action="store_false")
    parser.add_argument("--fetch-models", action="store_true", help="download the weights now")
    parser.add_argument("--only", action="append", default=[], help="one model repo (repeatable)")
    args = parser.parse_args(argv)

    env.load_dotenv()
    paths.ensure_dirs()
    conn = db.connect(paths.DB_PATH)
    try:
        db.migrate(conn)
        if args.status:
            print(json.dumps(needed(conn), indent=2))
            return 0

        def progress(repo: str, done_bytes: int, total: int) -> None:
            pct = 100 * done_bytes / total if total else 0
            print(f"  {repo:<45} {pct:5.1f}%  {models.human(done_bytes)}/{models.human(total)}", end="\r", flush=True)

        try:
            report = apply(
                Answers(
                    hf_token=args.hf_token,
                    provider=args.provider,
                    tier=args.tier,
                    diarize=args.diarize,
                    fetch_models=args.fetch_models,
                    wanted=args.only,
                ),
                conn,
                on_progress=progress,
            )
        except models.ModelError as exc:
            print(f"\n{exc}")
            return {"token": 3, "mismatch": 2}.get(exc.reason, 1)
        except ValueError as exc:
            print(str(exc))
            return 1
        print("\nsaved: " + (", ".join(report["wrote"]) or "nothing"))
        if report["downloaded"]:
            print("downloaded: " + ", ".join(report["downloaded"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
