"""First-run setup: the one engine that detects, asks what is open and writes.

The launcher prepares an environment and opens a browser. It never asked
anything, and two of the failures that cost the most on a clean machine came
straight out of that: the diarize stage needs a Hugging Face token that nothing
requested, and the weights are a separate download that nothing offered to
make (TASK-040.06).

The questions are data, not a form (ADR-015). `--plan` prints one JSON document
- what was found and where, never a value; the Ollama state; what this platform
would download; and only the questions that are still open, each with its
default, what skipping costs and where the same answer can be given later.
`--apply-stdin` reads one JSON document of answers back. Run bare on a terminal
the engine asks the same list itself. Every front-end - the Tk dialog, the
console, `install.py`, CI - renders that list and decides nothing.

Four answers were the whole of it until 2026-09-21, and each one is still
something the app cannot work out for itself. The number is what went: a
question exists once here, so adding one is adding a row to the table in
`_questions` rather than a field to a dialog nobody else can reuse, and how
many there are depends on the machine - detection is what makes the list
shorter (ADR-015, and the "four answers" sentence it revises).

A secret never rides on a command line, with no exception: the process list of
a machine shows it for as long as the child lives, and the shell keeps it in
its history afterwards. `--hf-token` is still recognised and is refused with a
sentence. A typed secret goes to its settings row and nowhere else - the row
Settings can show and clear - and a credential that was found somewhere is used
where it is and never copied.

The launcher is frozen and stdlib-only (ADR-011), so it cannot write a setting
row or read `.env` for itself - it runs this module in the app's environment
instead, the same way it runs the doctor. Everything here is also reachable
from Settings afterwards: an answer given once at the start must never be the
only place it can be given.

One stated exception, which TASK-089.11's criterion 4 allows by name: which
library to use (TASK-089.19). The settings rows live in the library's own
database, so switching libraries from inside the running app would change the
database under the process that serves it. It is answered again with
`--setup` and the Setup button, and in a clone with SCRIBE_DATA_DIR in `.env`
or `install.py --data-dir`; Settings > This machine says so
(`library.CHANGE_LIBRARY`).
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterator

import httpx2

from scribe import accel, applog, autostart, credentials, db, doctor, env, models, ollama_setup, paths
from scribe import library as libraries
from scribe.llm import ollama
from scribe.stages import diarize
from scribe.web import ai_ui, transcribe_dialog

CONTRACT = 5
"""The version of the document `--plan` prints and `--apply-stdin` reads, and
the number the stamp is measured against. Raise it whenever a question is added
to `_questions`.

Launcher and app ship in one payload (ADR-015), so a front-end that disagrees
is a mismatched install rather than an old client. It stays a constant in this
file rather than the `scribe/setup_contract.json` the spec proposes (section 2,
"a constant read as text would do as well"), and the launcher reads this line
as text the way it reads `scribe.__version__` (`app_version`): adding a
question means editing this file, so the number that has to rise lives beside
the change that forces it. A file of its own puts the bump away from its cause,
which is how two numbers drift apart.

A document that claims another number is refused with the usage code and
nothing of it is applied; one that claims no number at all is taken at the word
of the engine it is talking to. A stamp that claims a lower one reopens the
sitting for the questions this version added (`setup_needed` in the launcher).

3 since TASK-089.18: the Ollama install offer added `ollama_install`,
`ollama_new_model`, `ollama_models_dir` and `ollama_pull_resume`.

5 since TASK-089.19: `library` and `library_folder`, adopting a library that
already exists.
"""

STAMP = "setup.json"
"""Written into the data directory when a sitting ends, so the launcher asks
once rather than every start.

It holds the contract, when the sitting ended, and one state per question id -
never a chosen value: `states()` reads it, `_write_stamp` writes it, and the
launcher's gate parses those three keys with the standard library and nothing
else."""

STATES = ("not_needed", "skipped", "answered", "open")
"""What a sitting can record about one question, weakest first.

Weakest first is the precedence `_states` applies by writing them in this
order, and the order matters in two real cases. A token that was typed, saved
and then found by the resolver is `answered` and not `not_needed` - somebody
was asked. One the service refused is `open` although it was typed: nothing was
written, so the question is not settled.

`not_needed` is the weakest because it is the only one nobody did - it says the
answer was already on this machine. So it never counts as a question that was
put, which is what keeps `--plan --unasked-only` honest."""

PUT = ("answered", "skipped")
"""The two states that mean the question was put to somebody. A start asks what
is open and was never put; `--setup` asks everything that is open."""

TIERS = ("turbo", "max")

LATER = "later"
"""The provider answer that writes no row. No row means no provider (ADR-016):
nothing is sent until somebody has chosen, and the AI panel says so."""

CLOUD_WARNING = (
    "sends transcript text off this machine; recordings pinned private are always refused"
)

NO_KEY_CARD = (
    "Summary and Chat will show a no-key card until {label} has a key. "
    "Add one at Settings > AI providers > Save key."
)
"""What saving a cloud provider without a key costs, in words. Until this
sentence the answer was "saved: provider" and the first Summary became a failed
job on the board - the confirmed gap requirement 2 exists for."""

REFUSES_THE_FLAG = (
    "A token given on a command line can be read by any process that can list "
    "this machine's processes, and your shell writes it to its history. The "
    "hf-token flag is refused for that reason and nothing was written.\n"
    "Put the token in HF_TOKEN - in the environment or in .env, where setup now "
    "finds it by itself - or answer the hf_token question in the JSON document "
    "piped to --apply-stdin."
)
"""Why the flag is refused, rather than an argparse usage error: somebody with
a script that passes it has to be told where the token belongs, and a usage
error says only that the flag is unknown. Exit 2 either way - argparse's own
code for a usage error, which `mismatch` also uses."""

NOT_LOADED_HERE = "not loaded on this platform"

NOT_THIS_TIER = "not loaded at the chosen quality setting"

CATALOGUE_TODAY = "today's catalogue, from scribe/models.json"

TIER_CHOICES = [
    {"value": "turbo", "label": "Turbo", "note": "the default, and the measured fast path"},
    {
        "value": "max",
        "label": "Maximum",
        "note": "slower, and several times slower without a GPU - python -m scribe.doctor says which this machine has",
    },
]
"""The quality settings a first sitting chooses between.

Written once because two questions need them: the one that asks, and the one
that offers the download the answer decides the size of."""

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
"""OpenRouter's free key check: an authenticated request here costs nothing and
is answered 401 for a key that is missing, invalid or disabled (ADR-015, Open
Questions). Read from OpenRouter's documentation and probed without a key on
2026-09-20; never yet run with a real one."""


@dataclass(frozen=True)
class Answers:
    """What somebody answered. Every field's empty value means "not answered",
    which is what makes a skip write nothing."""

    hf_token: str = ""
    provider: str = ""
    tier: str = ""
    diarize: bool | None = None
    fetch_models: bool = False
    wanted: list[str] = field(default_factory=list)
    llm_keys: dict[str, str] = field(default_factory=dict)
    ollama_model: str = ""
    # The install offer (TASK-089.18). Three states for the two yes-no answers:
    # None was not answered, False is a No that is still an answer - it is
    # stamped, so the next start does not ask again - and True is the yes.
    ollama_install: bool | None = None
    ollama_new_model: str = ""
    ollama_models_dir: bool | None = None
    ollama_pull_resume: bool | None = None
    skipped: list[str] = field(default_factory=list)
    # 12. the watch folder (TASK-089.20): a full path, or "" for nothing asked.
    watch_folder: str = ""
    # TASK-089.22: None is not answered, False a No that was given.
    start_at_login: bool | None = None
    # TASK-089.19: "new", the data directory of a library to adopt, or
    # "named" with the folder somebody typed or browsed; "" is not answered.
    library: str = ""
    library_folder: str = ""


@dataclass(frozen=True)
class Question:
    """One question, as the data every front-end renders.

    `shown_if` is the only condition a front-end interprets (ADR-015): "show
    this when the answer to question X is Y". It is filled only while question
    X is in the same plan - a field conditioned on a question nobody is shown
    could never appear - and is null otherwise.
    """

    id: str
    kind: str
    text: str
    choices: list[dict]
    current: str
    default: str | None
    shown_if: dict | None
    if_skipped: str
    answer_later: str


@dataclass(frozen=True)
class Verdict:
    """What a credential check came back with. `ok` false with `refused` false
    is "could not be asked", which is not a verdict about the credential."""

    ok: bool
    why: str = ""
    refused: bool = False


def stamp_path() -> Path:
    return paths.DATA_DIR / STAMP


def done() -> bool:
    return stamp_path().exists()


def stamped() -> dict:
    """What the last sitting recorded, or {} when there was none.

    Defensive about what it reads: a stamp from before this contract, or one
    somebody edited, is a file this must not raise on - the only thing it
    decides here is which questions were already put.
    """
    try:
        found = json.loads(stamp_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def states() -> dict[str, str]:
    """What the last sitting did with each question, whatever shape it left.

    One shape is written and three are read. This writer's own, which carries
    `ended`; the one 0.5.x wrote, which `_before_the_contract` maps; and
    anything else - unreadable, not a document, or a contract with no `ended` -
    which is nobody's stamp and states nothing. The launcher's gate reads the
    same file and answers the last case by showing the sitting.
    """
    found = stamped()
    questions = found.get("questions")
    if found.get("ended") is not None and isinstance(questions, dict):
        return {str(asked): str(state) for asked, state in questions.items()}
    if "contract" in found:
        return {}
    return _before_the_contract(found)


def _before_the_contract(found: dict) -> dict[str, str]:
    """A stamp from before there were question ids in one, read as contract 1.

    Four fields were written and each stands for the question that filled it: a
    non-empty `provider` for question 5, a non-empty `tier` for 10, `hf_token`
    true for 4, a non-empty `fetched` for 11 (spec section 2). Everything else
    was never asked, which is what gets somebody who finished the old setup the
    questions this version added and nothing they already answered.

    A stamp the bare-run bug left holds four empty values and so counts nothing
    as answered - which is the right answer for it: nobody was asked anything.
    Counting nothing is not the same as asking everything again: `default_tier`
    is a first-sitting question and `_questions` offers it only while no stamp
    exists at all, so it does not come back through any door once one does. The
    tier stays reachable from Settings and the transcribe dialog, which is why
    `done()` was left as it is (TASK-089.11, criterion 3).
    """
    filled = {
        "llm_provider": bool(str(found.get("provider") or "").strip()),
        "default_tier": bool(str(found.get("tier") or "").strip()),
        "hf_token": found.get("hf_token") is True,
        "fetch_models": bool(found.get("fetched")),
    }
    return {question: "answered" for question, was_answered in filled.items() if was_answered}


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
        # `wanted`: the rows this platform and tier load. Without it a Windows
        # machine was told it still had gigabytes of Apple weights to fetch
        # (TASK-089.16).
        "to_download": sum(row["bytes"] for row in absent if row["wanted"] and not row["here"]),
        "diarization_possible": bool(token) or diarize.local_weights_dir().joinpath("config.yaml").exists(),
    }


# --- the plan -----------------------------------------------------------------------


@contextlib.contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection | None]:
    """The library opened so that answering a question cannot change it.

    `db.connect` is not usable here: it sets `journal_mode=WAL`, which is a
    write, and a plan that migrates or touches the library it was asked about
    is what criterion 2 of TASK-089.09 forbids.

    `immutable=1` is not tidiness. Measured on this machine (SQLite 3.45.3,
    spec section 0, and again on 2026-09-22): a plain `mode=ro` open of a
    cleanly closed WAL database *creates* a `-shm` of 32,768 bytes and an empty
    `-wal` beside it and leaves them there, while `mode=ro&immutable=1` creates
    nothing.

    Immutable is a promise that the file will not change while it is open, so
    SQLite may read past the write-ahead log - and everything committed since
    the last checkpoint is in that log. The promise is therefore made only when
    no `-wal` stands beside the database. Where one does, a writer has been or
    still is at work and the plain read-only open is the one that reads the
    truth: that is the branch the launcher plans in, with the running app
    holding the library. Measured on this machine on 2026-09-22: on that
    branch SQLite writes the wal-index. It rewrites an existing `-shm`, and
    creates one where it is missing - a `.db` and a `-wal` without one is a
    half-copied or restored library rather than anything MyScribe leaves
    behind. The database and the log itself are not touched either way.

    That is the whole of what this open costs, and it buys the only reading
    that is true: `immutable=1` on a library with a `-wal` answers with the
    state before the last checkpoint. Measured the same day on a library whose
    schema version and provider row were committed after one, it reported
    `user_version` 0 and the superseded row. A report that quietly says v0
    about a working library is worse than a wal-index that was rewritten.

    No database is None and never an error: a first run has none, and every
    question is then open. A library from before the 2026-09-06 rename reads as
    no library as well: adopting `scribe.db` means renaming it, which a plan
    may not do (paths.adopt_legacy_db; the door that adopts is TASK-089.19's).
    """
    if not path.exists():
        yield None
        return
    wal = path.with_name(path.name + "-wal")
    uri = path.resolve().as_uri() + ("?mode=ro" if wal.exists() else "?mode=ro&immutable=1")
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        # A file that cannot be opened at all reads as no library. The yield
        # below is deliberately outside this handler: with it inside, an error
        # raised by the caller's own `with` body arrived here too and was
        # answered with a second yield, which contextlib turns into "generator
        # didn't stop after throw()" - the real error lost, the close skipped.
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()


def _row(conn: sqlite3.Connection | None, key: str) -> str:
    """One settings row, or "" - including when there is no database yet."""
    if conn is None:
        return ""
    try:
        return (ai_ui.setting_get(conn, key) or "").strip()
    except sqlite3.Error:  # a library from before this table existed
        return ""


# --- 12. the watch folder (TASK-089.20) -------------------------------------------


def _watch_folders(conn: sqlite3.Connection | None) -> list[str]:
    """The folders this library watches, as a person reads them, or [] -
    including when there is no database yet.

    Every row and not only the enabled ones: a folder somebody switched off in
    Settings is still a decision made there, and a question that came back
    for it would be asking them to make it again. A switched-off row says so.
    """
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT path, enabled FROM watch_folder ORDER BY id").fetchall()
    except sqlite3.Error:  # a library from before this table existed
        return []
    return [str(row["path"]) + ("" if row["enabled"] else " (off)") for row in rows]


def label_of(provider: str) -> str:
    """A provider's name as a person reads it. `Provider` carries a registry
    name and no label, and the credential table already holds the spellings
    Settings shows - so the one place that has them is the one asked."""
    known = credentials.CREDENTIALS.get(provider)
    return known.label if known else provider.capitalize()


def _cloud_credentials() -> list[str]:
    """The providers that need a key, in a fixed order.

    Read off the provider classes rather than listed here: a cloud provider
    added to `PROVIDERS` cannot then be forgotten, which is the rule
    `privacy.assert_allowed` already keeps with the same attribute.
    """
    return [
        name
        for name, cls in sorted(ai_ui.llm.PROVIDERS.items())
        if not cls.is_local and name in credentials.CREDENTIALS
    ]


def found_table(conn: sqlite3.Connection | None = None) -> list[dict]:
    """What was found on this machine and where - never a value.

    Two kinds of row share one list because two front-ends render them as one
    table, and `kind` is what tells them apart: a credential row carries the
    keys `credentials.Found` has, a proxy row the host and port it points at
    with any `user:password@` already stripped (TASK-089.05). Coercing a proxy
    into the credential key set would have made "found in" mean two things.
    """
    rows = [
        {
            "kind": "credential",
            "name": row.credential,
            "label": row.label,
            "found": row.found,
            "source": row.source,
            "also_in": list(row.also_in),
            "conflict": row.conflict,
            "note": row.note,
        }
        for row in credentials.find_all(conn)
    ]
    rows += [
        {
            "kind": "proxy",
            "name": row.name,
            "label": row.name,
            "found": True,
            "source": row.source,
            "also_in": [],
            "conflict": False,
            "note": "",
            "host": row.host,
        }
        for row in credentials.proxies()
    ]
    # 12. the watch folder (TASK-089.20): a re-run shows the folders that are
    #     watched instead of asking. One row, the paths in `source`, and the
    #     credential key set so the generic renderers print it as they do a
    #     found credential.
    watched = _watch_folders(conn)
    if watched:
        rows.append(
            {
                "kind": "watch_folder",
                "name": "watch_folder",
                "label": "Watch folders",
                "found": True,
                "source": ", ".join(watched),
                "also_in": [],
                "conflict": False,
                "note": "",
            }
        )
    return rows


def _ollama(
    state: ollama_setup.State,
    *,
    marker: dict | None = None,
    standing: bool = False,
    offer: dict | None = None,
    refused: str = "",
) -> dict:
    """The Ollama state, as data. `variables` are names and places, never a
    value: `credentials.Source` is built that way.

    Beside the sentence, what a front-end renders for an Ollama that is left
    alone (R2): the copyable pull command when it runs without a chat model,
    whether a Check again applies, and the marker's standing - `lapsed` is a
    file that no longer counts, which `apply` removes because a plan writes
    nothing. `offer` is the install plan, and it is null in every state but
    absent (TASK-089.18). `refused` is the one sentence an offer that could
    not be built ends on - GitHub unreachable, rate-limited, or a release whose
    two digests disagree (TASK-095) - and it goes into the note every
    front-end already prints, with a Check again, rather than into a new key.
    """
    check_again = bool(refused) or state.state in (
        ollama_setup.INSTALLED_NOT_RUNNING,
        ollama_setup.RUNNING_NO_CHAT_MODEL,
        ollama_setup.UNKNOWN,
    )
    note = ollama_setup.describe(state)
    if refused:
        # One sentence, like every other note `describe()` returns.
        note = f"{note}, and MyScribe could not offer to install it: {refused}"
    return {
        "state": state.state,
        "present": state.present,
        "binary": state.binary,
        "version": state.version,
        "chat_models": list(state.chat_models),
        "variables": [str(source) for source in state.variables],
        "note": note,
        "check_again": check_again,
        "pull_command": (
            f"ollama pull {ollama_setup.DEFAULT_MODEL}"
            if state.state == ollama_setup.RUNNING_NO_CHAT_MODEL
            else ""
        ),
        "marker": {
            "standing": standing,
            "lapsed": ollama_setup.marker_path().exists() and not standing,
            "model": str((marker or {}).get("model") or ""),
        },
        "offer": offer,
    }


def _offer(state: ollama_setup.State) -> tuple[dict | None, str]:
    """The install plan with the model choices and the room, or None - and
    the sentence it was refused with, or "".

    Built only in state absent, and only then does `accel.memory()` import
    torch - a plan on a machine that has an Ollama stays the four-second plan
    it was (ADR-001, and the risk the task names). Only then, too, is GitHub
    asked for Ollama's newest release (TASK-095): the one network request a
    plan makes, and `plan` calls this only when the question will be put. A
    release that cannot be reached or trusted is no offer and one sentence;
    nothing is written, because a plan writes nothing. The room is measured at
    Ollama's default folder for the default model, which is the Enter answer;
    `apply` measures again for the model that was chosen, and again before the
    pull (criterion 10).
    """
    if state.state != ollama_setup.ABSENT:
        return None, ""
    try:
        offer = ollama_setup.install_plan()
    except ollama_setup.InstallError as exc:
        return None, str(exc)
    memory = accel.memory()
    offer["memory_bytes"] = memory
    offer["threshold_bytes"] = ollama_setup.GEMMA_THRESHOLD_BYTES
    offer["threshold_is_an_estimate"] = True
    offer["choices"] = ollama_setup.choices(memory, accel.is_apple_silicon())
    needed = ollama_setup.MODEL_BYTES[ollama_setup.DEFAULT_MODEL]
    default_dir = ollama_setup.default_models_dir()
    offer["models_dir"] = str(default_dir)
    offer["room"] = ollama_setup.room(default_dir, needed)
    with_us = ollama_setup.models_dir_with_myscribe()
    offer["with_myscribe"] = str(with_us)
    offer["room_with_myscribe"] = ollama_setup.room(with_us, needed)
    return offer, ""


NOT_MLX = models.NOT_MLX
"""Re-exported, not a second copy: `scribe.models` owns the answer to "which
transcriber does this platform load", because the doctor needs it too and two
spellings of one question is how the doctor came to ask for weights the plan
already knew this machine could not open (TASK-089.16)."""


def downloads(tier: str = "") -> dict:
    """What this platform would download at this quality setting, and what it
    would not.

    An entry that this machine does not fetch carries no byte count at all
    rather than a number in a column headed "download": its size is real, but
    it is not a download this machine would make, and a number shown as one is
    how "1.6 GB" ended up in a dialog whatever was picked.

    Every catalogue row is still named, the ones that are not fetched included,
    so the offer can be read against `models.json` - which is the half of this
    that `models.status()` keeps true.
    """
    backend = models.backend_here()
    tier = tier or models.DEFAULT_TIER
    entries, total = [], 0
    for row in models.status(backend=backend, tier=tier):
        if row["wanted"] and not row["here"]:
            total += row["bytes"]
        entries.append(
            {
                "repo": row["repo"],
                "here": row["here"],
                "gated": row["gated"],
                "bytes": row["bytes"] if row["wanted"] else None,
                # Two questions, so two keys. `loads_here` is the platform one
                # the contract has carried since TASK-089.09 (design spec 3.1:
                # "false for the MLX entry off Apple Silicon") and `wanted`
                # adds the quality setting. Folded into one key, the offer said
                # `loads_here` false about the repository this machine loads -
                # true about the tier and false about the platform, which is
                # not what the name asks.
                "loads_here": row["loads_here"],
                "wanted": row["wanted"],
                "note": "" if row["wanted"] else (NOT_LOADED_HERE if not row["loads_here"] else NOT_THIS_TIER),
            }
        )
    return {
        "catalogue": CATALOGUE_TODAY,
        "backend": backend,
        "note": "",
        "entries": entries,
        "total_bytes": total,
    }


def question_of(credential: str) -> str:
    """The id of the question that asks for one credential.

    The Hugging Face token is asked as `hf_token` and every LLM key as
    `llm_key_<provider>`. Both spellings are built here rather than written out
    at each end, so that the stamp cannot invent a spelling the plan does not
    use. It says nothing about which ids reach the stamp: `_states` records
    `not_needed` for every credential this machine has, and `_questions` asks
    for a key only while its provider is in play, so a machine with an OpenAI
    key and Ollama chosen stamps an `llm_key_openai` nobody was asked. That is
    informational and inert - `not_needed` is not in `PUT`, so it suppresses no
    question, and the gate reads `contract` and `ended` only.
    """
    return "hf_token" if credential == credentials.HUGGINGFACE.name else f"llm_key_{credential}"


YES_NO = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]


def _questions(
    conn: sqlite3.Connection | None,
    state: ollama_setup.State,
    offer: dict,
    *,
    install_offer: dict | None = None,
    marker: dict | None = None,
    by_hand: bool = False,
) -> list[Question]:
    """The questions that are open on this machine, in the order they are asked.

    One predicate per question and no branch anywhere else: a question that was
    answered, or whose answer was found, is simply not in the list, which is
    what "detect before asking" means for a front-end that cannot decide.

    The numbers in the comments are the rows of the design spec's table
    (section 1); the questions this engine does not own - where everything
    goes, and adopting a library - belong to TASK-089.14 and .19 and are not
    built here.
    """
    open_questions: list[Question] = []

    # 4. The Hugging Face token, when nothing on this machine has one and there
    #    is no local pipeline to fall back on.
    has_pipeline = diarize.local_weights_dir().joinpath("config.yaml").exists()
    if not credentials.resolve(conn, credentials.HUGGINGFACE).found and not has_pipeline:
        open_questions.append(
            Question(
                id=question_of(credentials.HUGGINGFACE.name),
                kind="secret",
                text="Hugging Face token, so that MyScribe can recognise speakers.",
                choices=[],
                current="",
                default=None,
                shown_if=None,
                if_skipped=(
                    "Nothing is saved. Transcription is unaffected: a job that asks for "
                    "speakers ends with its transcript and a note (TASK-089.08)."
                ),
                answer_later="Settings > Transcription > Hugging Face token",
            )
        )

    # 5. Who answers questions about a transcript. Only while no row says so;
    #    a re-run shows the stored value and never resets it.
    stored_provider = _row(conn, ai_ui.PROVIDER_SETTING)
    if not stored_provider:
        open_questions.append(_provider_question(state))

    # 6. The key for a cloud provider that has none. While question 5 is open
    #    this is one question per cloud provider, each shown under its own
    #    choice; once a provider is stored it is the one that provider needs.
    for name in _cloud_credentials():
        if stored_provider and stored_provider != name:
            continue
        if credentials.resolve(conn, name).found:
            continue
        cred = credentials.CREDENTIALS[name]
        open_questions.append(
            Question(
                id=question_of(name),
                kind="secret",
                text=f"API key for {cred.label}.",
                choices=[],
                current="",
                default=None,
                shown_if=None if stored_provider else {"question": "llm_provider", "equals": name},
                if_skipped=NO_KEY_CARD.format(label=cred.label),
                answer_later="Settings > AI providers > Save key",
            )
        )

    # 7, 8, 8a. Ollama is not on this machine: install it, which model, and
    #    where its models go when the default volume is too small - and, by
    #    hand only, the one exception to "left alone": the pull into
    #    MyScribe's own install (TASK-089.18, ADR-017).
    open_questions += _ollama_questions(state, stored_provider, install_offer, marker, by_hand)

    # 9. Which of an existing Ollama's models to use. Never an offer to pull
    #    one, and never for an Ollama this install put there (TASK-089.18).
    model_question = _ollama_model_question(conn, state, stored_provider)
    if model_question is not None:
        open_questions.append(model_question)

    # 10. Transcription quality, on a first sitting only.
    tier_open = not done()
    if tier_open:
        current_tier = _row(conn, transcribe_dialog.SETTING_TIER) or models.DEFAULT_TIER
        open_questions.append(
            Question(
                id="default_tier",
                kind="choice",
                text="Transcription quality.",
                choices=[dict(choice) for choice in TIER_CHOICES],
                current=current_tier,
                default=current_tier,
                shown_if=None,
                if_skipped="Nothing is written; the stored default stays as it is.",
                answer_later="the transcribe dialog, or Settings > Transcription",
            )
        )

    # 11. Download the weights now, while somebody is watching.
    #
    #     For the tier that will be in play, and while question 10 is open that
    #     is not the stored one - the quality is answered in this same sitting.
    #     Offered against the stored tier alone, a first sitting that picked
    #     Maximum on a machine whose turbo weights were already here was asked
    #     nothing at all, and the larger model then arrived inside the first
    #     transcription with no progress shown: the fault this task exists to
    #     remove, one tier over (TASK-089.16).
    if tier_open:
        totals = [(choice["label"], downloads(choice["value"])["total_bytes"]) for choice in TIER_CHOICES]
    else:
        totals = [("", offer["total_bytes"])]
    to_offer = [(label, total) for label, total in totals if total > 0]
    if to_offer:
        # One number while it is the same number whatever is chosen, which is
        # every sitting that is not choosing; one number per choice otherwise,
        # because a single one would be wrong for the choice it is not for.
        if len(to_offer) == len(totals) and len({total for _, total in to_offer}) == 1:
            sizes = models.human(to_offer[0][1])
        else:
            sizes = ", ".join(f"{models.human(total)} for {label}" for label, total in to_offer)
        open_questions.append(
            Question(
                id="fetch_models",
                kind="yes-no",
                text=f"Download the speech weights now ({sizes})?",
                choices=[{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}],
                current="",
                default="yes",
                shown_if=None,
                if_skipped=(
                    "The first transcription downloads them inside the job, with no "
                    "progress shown."
                ),
                answer_later="python -m scribe.setup --fetch-models",
            )
        )

    # 12. A folder to watch, while no row says one is (TASK-089.20). Skip is
    #     the default; the row it writes is the one Settings writes, after
    #     the same four refusals, so a refused path reopens it rather than
    #     ending the sitting.
    if not _watch_folders(conn):
        open_questions.append(
            Question(
                id="watch_folder",
                kind="text",
                text="Is there a folder MyScribe should watch for new recordings? Give its full path.",
                choices=[],
                current="",
                default=None,
                shown_if=None,
                if_skipped="Nothing is watched; recordings come in through the transcribe dialog only.",
                answer_later="Settings > Watch folders",
            )
        )

    # 13. Start MyScribe when you log in (TASK-089.22). A thin layer over
    #     `scribe.autostart`: asked only while the OS holds no entry - a re-run
    #     shows the one it holds in the found table instead - and only where
    #     there is something safe to register. Default No: a program that
    #     loads a model and holds VRAM is not something to find running by
    #     surprise (R3).
    entry = _login_entry()
    if entry is not None and not entry.on and entry.command:
        open_questions.append(
            Question(
                id="start_at_login",
                kind="yes-no",
                text="Start MyScribe when you log in? Watch folders and feeds only work while it runs.",
                choices=[{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}],
                current="",
                default="no",
                shown_if=None,
                if_skipped=(
                    "Nothing is written. Watch folders and feeds only work while MyScribe "
                    "runs, so until you start it by hand nothing is watched."
                ),
                answer_later="Settings > Start at login",
            )
        )

    return open_questions


def _provider_question(state: ollama_setup.State) -> Question:
    """Question 5, with the default the machine earns.

    Ollama is the Enter default only when it is ready to answer; otherwise the
    default is "decide later", which writes no row - and no row means no
    provider (ADR-016), so nothing is sent until somebody chooses. A cloud
    provider is never what Enter accepts, and every cloud choice carries what
    choosing it means for the recording.
    """
    choices = []
    for name, cls in sorted(ai_ui.llm.PROVIDERS.items()):
        note = ollama_setup.describe(state) if name == "ollama" else ""
        if not cls.is_local:
            note = CLOUD_WARNING
        choices.append({"value": name, "label": label_of(name), "note": note})
    choices.append(
        {
            "value": LATER,
            "label": "Decide later",
            "note": "nothing is written, and nothing is sent until you choose",
        }
    )
    return Question(
        id="llm_provider",
        kind="choice",
        text="Who answers questions about a transcript?",
        choices=choices,
        current="",
        default="ollama" if state.state == ollama_setup.READY else LATER,
        shown_if=None,
        if_skipped=(
            "No provider is chosen, so nothing is sent: the AI panel says "
            "\"choose a provider\" and the automatic speaker-naming pass waits."
        ),
        answer_later="Settings > AI providers",
    )


def _install_text(offer: dict) -> str:
    """Question 7's text: everything ADR-017's Must says is shown before the
    question - the artifact's URL, size and sha256, the exact command line,
    where it installs - plus the two sentences a Windows user is owed (no
    administrator; Ollama updates itself afterwards), and what a yes means for
    question 5."""
    shown = offer["shown"]
    if offer["platform"] == "win32":
        how = (
            f"check that sum and that the signer is {offer['signer']}, then run exactly: {shown[0]}. "
            f"It installs into {offer['install_dir']} for your account - no administrator is needed - "
            "and Ollama updates itself afterwards."
        )
    elif offer["platform"] == "darwin":
        how = (
            f"check that sum, then open it: {shown[0]}. Drag Ollama into {offer['install_dir']}, start it, "
            "then press Check again. Nobody has run this on a Mac yet."
        )
    else:
        how = (
            "and show you these four commands to run yourself, because Ollama's script needs root and can "
            "install drivers, so MyScribe does not run it: " + "; ".join(shown) + f". It installs into "
            f"{offer['install_dir']}. Then press Check again."
        )
    return (
        "Ollama is not on this machine. Install it, and let it answer questions about a transcript? "
        f"MyScribe would download {offer['name']} {offer['tag']} from {offer['url']} "
        f"({offer['bytes']:,} bytes, sha256 {offer['sha256']}), {how} "
        "MyScribe will then use this Ollama unless you chose another provider above."
    )


def _ollama_questions(
    state: ollama_setup.State,
    stored_provider: str,
    install_offer: dict | None,
    marker: dict | None,
    by_hand: bool,
) -> list[Question]:
    """Rows 7, 8 and 8a of the spec's table, and the G6 exception.

    7 exists only in state absent, and only while the provider is Ollama or
    undecided - a stored cloud provider is a machine that chose. Its `shown_if`
    is None on purpose while question 5 is open: the one condition a front-end
    interprets is "equals one value" (ADR-015), and this offer is for Ollama
    *or* undecided, so the text says what a yes means instead (spec row 7). 8
    hangs under 7. 8a exists only when Ollama's default folder is short of the
    default model, and never otherwise (G8). The resume question exists only
    by hand - `--setup`, never a start - with a marker that still counts and a
    daemon that answers without a chat model; its default is No, so nothing is
    ever pulled by itself (G6).
    """
    asked: list[Question] = []
    if (
        install_offer is not None
        and state.state == ollama_setup.ABSENT
        and stored_provider in ("", ai_ui.llm.PROVIDERS["ollama"].name)
    ):
        asked.append(
            Question(
                id="ollama_install",
                kind="yes-no",
                text=_install_text(install_offer),
                choices=[dict(choice) for choice in YES_NO],
                current="",
                default="no",
                shown_if=None,
                if_skipped=(
                    "Nothing is downloaded and nothing runs. Recordings pinned private cannot use AI "
                    "until a local provider exists."
                ),
                answer_later=(
                    "the same offer at the next start or with --setup, for as long as Ollama is absent; "
                    f"or install it yourself from {install_offer['vendor_page']}"
                ),
            )
        )
        asked.append(
            Question(
                id="ollama_new_model",
                kind="choice",
                text="Which model should the new Ollama get?",
                choices=[dict(choice) for choice in install_offer["choices"]],
                current="",
                default=ollama_setup.DEFAULT_MODEL,
                shown_if={"question": "ollama_install", "equals": "yes"},
                if_skipped="Ollama is installed with no model; the report gives the copyable pull command.",
                answer_later=(
                    "--setup while this install's marker stands, as a question whose default is No; "
                    "or `ollama pull <model>`"
                ),
            )
        )
        room = install_offer["room"]
        if not room["enough"]:
            variable = (
                " A yes sets OLLAMA_MODELS for your account before the installer starts."
                if install_offer["platform"] == "win32"
                else " A yes shows the command that sets OLLAMA_MODELS; MyScribe writes no setting of Ollama's here."
            )
            asked.append(
                Question(
                    id="ollama_models_dir",
                    kind="yes-no",
                    text=(
                        f"Ollama's default model folder {room['path']} has {models.human(room['free'])} free "
                        f"({room['free']:,} bytes) and {ollama_setup.DEFAULT_MODEL} needs "
                        f"{models.human(room['needed'])} ({room['needed']:,} bytes). Keep Ollama's models with "
                        f"MyScribe instead, at {install_offer['with_myscribe']}?{variable}"
                    ),
                    choices=[dict(choice) for choice in YES_NO],
                    current="",
                    default="yes",
                    shown_if={"question": "ollama_install", "equals": "yes"},
                    if_skipped="The offer ends with both numbers: nothing is installed and nothing is pulled.",
                    answer_later="the same offer at the next start or with --setup, for as long as Ollama is absent",
                )
            )
    if by_hand and marker is not None and state.state == ollama_setup.RUNNING_NO_CHAT_MODEL:
        model = str(marker["model"])
        asked.append(
            Question(
                id="ollama_pull_resume",
                kind="yes-no",
                text=(
                    f"MyScribe installed this Ollama ({marker['version']} at {marker['binary']}) and the "
                    f"pull of {model} it was asked for did not finish. Pull {model} into it now?"
                ),
                choices=[dict(choice) for choice in YES_NO],
                current="",
                default="no",
                shown_if=None,
                if_skipped=f"Nothing is pulled and Ollama is left alone; the copyable command is `ollama pull {model}`.",
                answer_later=f"--setup again while this marker stands; or `ollama pull {model}`",
            )
        )
    return asked


def _ollama_model_question(
    conn: sqlite3.Connection | None, state: ollama_setup.State, stored_provider: str
) -> Question | None:
    """Question 9, and the four conditions that keep it out.

    It exists for one machine: an Ollama that was already here and is running,
    that MyScribe is to use, that has chat models but not the one MyScribe asks
    for by default. Anything else - another provider, an Ollama that is not
    ready, a model already chosen, the default already pulled, no chat model at
    all - is not a question, it is either settled or somebody else's task. Its
    default is skip: never save a model nobody chose (TASK-054).
    """
    if stored_provider != ai_ui.llm.PROVIDERS["ollama"].name:
        return None
    if state.state != ollama_setup.READY:
        return None
    if _row(conn, ai_ui.MODEL_SETTING_PREFIX + "ollama"):
        return None
    wanted = ai_ui.llm.PROVIDERS["ollama"].default_model
    if wanted in state.chat_models or not state.chat_models:
        return None
    return Question(
        id="llm_model_ollama",
        kind="choice",
        text=f"Which of your Ollama models should MyScribe use? {wanted} is not pulled.",
        choices=[{"value": name, "label": name, "note": ""} for name in state.chat_models],
        current="",
        default=None,
        shown_if=None,
        if_skipped=(
            f"MyScribe keeps asking Ollama for {wanted}; the AI panel names the "
            "missing model. Nothing in Ollama is changed either way."
        ),
        answer_later="Settings > AI providers > model",
    )


# --- start at login (TASK-089.22) ------------------------------------------------------


def _login_entry() -> autostart.Entry | None:
    """The login entry as the OS holds it right now, or None when the OS would
    not say.

    Read where it is needed, as a credential is: `_questions` decides whether
    to ask and `plan` shows the state, and one more read of one value costs
    less than a parameter through `_questions`, whose signature a test pins.
    An OS that will not answer costs this question and its row, never the
    plan - a plan is what a front-end draws before anybody has typed, and
    every other question on it is still worth asking.
    """
    try:
        return autostart.status()
    except (OSError, ValueError):
        return None


def _login_row(entry: autostart.Entry) -> dict:
    """The found row for the login entry, so a re-run shows the current state
    instead of asking (criterion 4).

    The keys every other row has, so `render` and the launcher print it as
    they print a credential, plus the three Settings shows: `on`, `where` and
    the command exactly as the OS holds it - or, while it is off, exactly what
    switching it on would write.
    """
    return {
        "kind": "login",
        "name": "start_at_login",
        "label": "Start at login",
        "found": entry.on,
        "source": entry.where if entry.on else "",
        "also_in": [],
        "conflict": False,
        "note": entry.command,
        "on": entry.on,
        "where": entry.where,
        "command": entry.command,
    }


def _yes_no(value) -> bool | None:
    """A yes-no answer as a document carries it - a boolean, or the word a
    front-end took from the choices - and None for anything else, which is
    "not answered" and writes nothing."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("yes", "true", "1"):
            return True
        if word in ("no", "false", "0"):
            return False
    return None


def plan(conn: sqlite3.Connection | None, *, unasked_only: bool = False,
         named: list[Path] | None = None) -> dict:
    """What this machine has, and what is still open - one JSON-able document.

    Nothing here writes, downloads or checks a credential against its service:
    a plan is what a front-end draws before anybody has typed, so it reads the
    settings rows, the environment, `.env`, the registry, the login file, the
    catalogue and Ollama's own two reads (TASK-089.06), and stops there - with
    one exception since TASK-095: when Ollama is absent, no provider row says
    otherwise and the install question will be put, it asks GitHub which
    Ollama release is newest (two small GETs, never an installer). A start
    whose sitting already put that question asks nothing.

    `unasked_only` drops the questions a saved sitting already put - what a
    start asks for, against the `--setup` that asks everything. Answered and
    skipped both count as put: somebody was asked and said what they wanted,
    and a question skipped on purpose that came back at every start would be
    the nagging this flag exists to end. A credential that was merely found is
    `not_needed` and was never put, so it is still asked once it goes.
    """
    state = ollama_setup.state()
    # The marker is read, never written, here: a plan that saw it lapse says so
    # in the document, and `apply` is what removes the file (TASK-089.18).
    marker = ollama_setup.read_marker()
    standing = ollama_setup.valid_marker(state, marker)
    stored_provider = _row(conn, ai_ui.PROVIDER_SETTING)
    # Put before anything is built: a start (`unasked_only`) drops a question
    # a sitting already put, and an offer for a question nobody will be asked
    # would be a request to GitHub for nothing (TASK-095).
    was_put = {id for id, state_of in states().items() if state_of in PUT} if unasked_only else set()
    install_offer, refused = None, ""
    if stored_provider in ("", ai_ui.llm.PROVIDERS["ollama"].name) and "ollama_install" not in was_put:
        install_offer, refused = _offer(state)
    # The tier decides which Whisper repository is offered, so the offer is
    # made for the one this library is set to rather than for the default
    # (TASK-089.16): choosing the largest model and then downloading the turbo
    # weights is what AC5 of TASK-040.06 claimed was already true.
    offer = downloads(_row(conn, transcribe_dialog.SETTING_TIER) or "")
    open_questions = _questions(
        conn, state, offer,
        install_offer=install_offer,
        marker=marker if standing else None,
        # A start asks with `unasked_only`; `--setup` asks everything, and
        # only that sitting may offer the pull into MyScribe's own install (G6).
        by_hand=not unasked_only,
    )
    # TASK-089.19: the library question comes first - every other answer is
    # written into the library it picks.
    open_questions = _library_questions(conn, named or []) + open_questions
    if unasked_only:
        open_questions = [q for q in open_questions if q.id not in was_put]
    found = found_table(conn)
    # TASK-089.22: the login entry as the OS holds it, so a re-run shows the
    # state instead of asking. Not in `found_table`, which is the credentials view.
    entry = _login_entry()
    if entry is not None:
        found.append(_login_row(entry))
    return {
        "contract": CONTRACT,
        "found": found,
        "ollama": _ollama(state, marker=marker, standing=standing, offer=install_offer, refused=refused),
        "downloads": offer,
        "questions": [asdict(question) for question in open_questions],
    }


# --- 2. an existing library (TASK-089.19) ---------------------------------------------


NEW_LIBRARY = "new"
NAMED_LIBRARY = "named"

RESULT_VARIABLE = "MYSCRIBE_SETUP_RESULT"
"""Where the launcher wants to hear what an adoption decided: a file it names
for the setup child and reads when the child has gone.

A file rather than a line on stdout because one of the launcher's two doors
never reads stdout: the console door hands the terminal to this engine. One
channel for both doors. Without the variable nobody is listening - a clone,
`install.py`, a bare run - and the engine writes SCRIBE_DATA_DIR into `.env`
itself, which is the documented manual route (`.env.example`)."""

ADOPT_CONSENT = (
    "Choosing it is the yes: this version migrates it in place - nothing is copied or moved - "
    "and an older MyScribe must not open it afterwards."
)

OTHER_ANSWERS_WAIT = (
    "The other answers in this sitting were not saved: they belong to the library you use, "
    "and are asked again for it."
)


def _is_release() -> bool:
    """Is this the payload of a release rather than a clone?

    A release's `app/` has no `.git`, and cannot see a clone's library by
    itself, so it asks the library question even when nothing was found. A
    clone asks only when it found one.
    """
    return not (env.REPO_DIR / ".git").exists()


def _recordings(conn: sqlite3.Connection | None) -> int:
    """How many recordings the target library holds; 0 with no database.
    "Holds a library" is this number, not the file: every apply creates an
    empty, migrated database first."""
    if conn is None:
        return 0
    try:
        return int(conn.execute("SELECT COUNT(*) FROM media WHERE trashed_at IS NULL").fetchone()[0])
    except sqlite3.Error:
        return 0


def _blind_spot_sentence() -> str:
    return (
        f"A MyScribe serving it on port {DEFAULT_PORT} is refused. One on a port nobody named - "
        "this repository's own --port 4299 among them - cannot be seen from here, so stop it first."
    )


def _library_note(found: libraries.Library) -> str:
    """What one found library is, and what choosing it means - criteria 1,
    4, 5, 7 and 11 in the note under its choice. The choice is the explicit
    yes, so everything the yes has to be told is here."""
    refused = libraries.newer_than_this_app(found) or libraries.job_running(found)
    facts = (f"{found.recordings} recording{'' if found.recordings == 1 else 's'}, "
             f"{models.human(found.size)} on disk ({found.size:,} bytes), found as {found.where}.")
    if refused:
        return f"{facts} {refused} It cannot be chosen now."
    said = [facts]
    if found.legacy:
        said.append("A library from before the 2026-09-06 rename: choosing it renames scribe.db to myscribe.db.")
    said.append(ADOPT_CONSENT)
    if found.never_asked:
        said.append(
            f"{found.never_asked} diarized recording{'' if found.never_asked == 1 else 's'} "
            f"{'has' if found.never_asked == 1 else 'have'} never been asked who is speaking; "
            "choosing a cloud provider later sends each of them once. Nothing else about the library "
            "is sent or changed."
        )
    else:
        said.append("No diarized recording is waiting to be asked who is speaking.")
    said.append(_blind_spot_sentence())
    return " ".join(said)


def _library_questions(conn: sqlite3.Connection | None, named: list[Path]) -> list[Question]:
    """Question 2 of the spec's table, and the folder it may ask for.

    Asked while the target holds no recordings - a start never offers to
    swap a library somebody is using - and only when there is something to
    say: a library found elsewhere, or a release, which cannot see a clone's
    library and so always offers to name one. `named` is a folder somebody
    typed in an earlier pass: it is looked at read-only and listed like a
    found one, so its numbers are shown before anybody says yes to it.
    """
    if _recordings(conn):
        return []
    found = libraries.found(paths.DATA_DIR, named)
    if not found and not _is_release():
        return []
    choices = [{
        "value": NEW_LIBRARY,
        "label": "Start a new library",
        "note": f"at {paths.DATA_DIR}; nothing found elsewhere is touched",
    }]
    choices += [{"value": str(lib.data_dir), "label": str(lib.data_dir), "note": _library_note(lib)} for lib in found]
    choices.append({
        "value": NAMED_LIBRARY,
        "label": "I already have one: name its folder",
        "note": ("a release cannot see a library inside a clone by itself. MyScribe looks at the "
                 "folder first, changes nothing, and shows what it found before you choose it."),
    })
    where = "; ".join(f"{lib.data_dir} ({lib.recordings} recordings)" for lib in found)
    text = (
        "A MyScribe library already exists on this machine. Use it, start a new one, or name another folder?"
        if found else
        "Do you already have a MyScribe library? Name its folder, or start a new one."
    )
    return [
        Question(
            id="library",
            kind="choice",
            text=text,
            choices=choices,
            current="",
            default=NEW_LIBRARY,
            shown_if=None,
            if_skipped=(
                f"A new library is started at {paths.DATA_DIR}."
                + (f" Left untouched, and still there: {where}." if where else "")
            ),
            answer_later=libraries.CHANGE_LIBRARY,
        ),
        Question(
            id="library_folder",
            kind="text",
            text=("Which folder holds it? The folder with myscribe.db (or scribe.db) in it. "
                  "The other answers below are asked again for that library."),
            choices=[],
            current="",
            default=None,
            shown_if={"question": "library", "equals": NAMED_LIBRARY},
            if_skipped="Nothing is looked at, and a new library is started.",
            answer_later=libraries.CHANGE_LIBRARY,
        ),
    ]


def _refuse_named(folder: str) -> str:
    """Why a named folder is not one to look at, or "". The location
    question's refusals that apply to a library: a share, and a path that is
    not whole. SQLite's WAL does not work over a network filesystem."""
    text = folder.strip().strip('"')
    if not text:
        return "No folder was named."
    if text.startswith("\\\\") or text.startswith("//"):
        return (f"{text} is on a network share, and a library cannot live there: MyScribe keeps it in "
                "SQLite with a write-ahead log, which does not work over a network filesystem.")
    if not Path(text).is_absolute():
        return f"{text} is not a full path. Give the whole path, the drive or the mount point included."
    return ""


def _tell_the_door(result: dict) -> str:
    """Hand what an adoption decided to whoever runs this engine, and say
    how it was handed. The launcher writes it into its pointer file; with no
    launcher listening, SCRIBE_DATA_DIR goes into `.env` (see RESULT_VARIABLE)."""
    listening = os.environ.get(RESULT_VARIABLE, "").strip()
    if listening:
        Path(listening).write_text(json.dumps(result), encoding="utf-8")
        return ""
    data_dir = result.get("data_dir")
    if not data_dir:
        return ""
    written = env.write_env(libraries.VARIABLE, str(data_dir))
    return (f"{libraries.VARIABLE}={data_dir} was written to {written}: MyScribe started from this "
            "checkout uses that library from now on.")


def _library_door(answers: Answers, *, port: int, health: Callable[[int], dict | None] | None = None,
                  others: bool = False) -> int:
    """A document that chose a library other than a new one: look, refuse or
    adopt - and write nothing else.

    Everything the rest of the document answers is written into the library
    in use, and after an adoption that is another library than the one this
    process was started on. So nothing else is applied and no stamp is
    written: the door plans again against the library that is now in use, and
    that library's own stamp decides what is still open.
    """
    health = health or _health

    def reopen(sentence: str) -> int:
        print(sentence)
        print("still open: library")
        return 0

    if answers.library == NAMED_LIBRARY:
        refused = _refuse_named(answers.library_folder)
        if refused:
            return reopen(refused)
        folder = Path(answers.library_folder.strip().strip('"'))
        found = libraries.inspect(folder, "the folder you named")
        if found is None:
            return reopen(f"There is no MyScribe library in {folder}: it holds no myscribe.db or scribe.db. "
                          "Nothing was changed.")
        _tell_the_door({"library": str(folder)})
        print(f"MyScribe looked at {folder} and changed nothing: {_library_note(found)}")
        print("Choose it in the list to use it.")
        if others:
            print(OTHER_ANSWERS_WAIT)
        return 0

    found = libraries.inspect(Path(answers.library), "the folder chosen")
    if found is None:
        return reopen(f"There is no MyScribe library in {answers.library}. Nothing was changed.")
    if _is_release() and not os.environ.get(RESULT_VARIABLE, "").strip():
        # Run by hand in a release's environment: the launcher forces its own
        # SCRIBE_DATA_DIR, so `.env` would point at nothing afterwards, and a
        # migrated library nothing uses is the worst of both.
        return reopen(f"Nothing was changed: in a release only the launcher can point {libraries.APP_NAME} "
                      "at another library. Start MyScribe with --setup and choose it there.")
    refused = (
        libraries.newer_than_this_app(found)
        or libraries.job_running(found)
        or libraries.served(found, health(port), port)
    )
    if refused:
        return reopen(refused)
    data_dir = libraries.adopt(found)
    said = [f"MyScribe now uses the library at {data_dir} ({found.recordings} recordings), migrated in place."]
    handed = _tell_the_door({"data_dir": str(data_dir)})
    if handed:
        said.append(handed)
    if others:
        said.append(OTHER_ANSWERS_WAIT)
    for line in said:
        print(line)
    return 0


# --- the proof --------------------------------------------------------------------------


DEFAULT_PORT = 4242
"""The port `python -m scribe` serves on. The number rather than an import:
taking it from `scribe.__main__` would pull uvicorn and FastAPI into a child
whose whole job is to measure. The launcher keeps its own copy for a related
reason (ADR-011), so this is the third and last."""

HEALTH_TIMEOUT = 2.0
"""How long an app that is up gets to answer. This is loopback: a MyScribe that
is running answers in milliseconds, and one that needs longer than this is not
something the proof can say anything true about."""

LOCK_PATH = Path(__file__).resolve().parent.parent / "uv.lock"


@dataclass(frozen=True)
class Gate:
    """May this process load a model, and if not, why not.

    ADR-001's Must is that GPU work runs only inside `scribe.runner` children,
    at most one at a time, and its Exceptions read "None". ADR-015 records the
    reading this rests on: with nothing answering on the port and no job
    running, nothing else holds the card, and a one-shot command that measures
    in its own process is the existing precedent - `python -m scribe.doctor`
    from a terminal. Every branch below is about whether that condition holds,
    and no branch widens it.
    """

    may_load: bool
    reason: str = ""
    answered: dict | None = None
    job_id: int | None = None
    asked_a_port: bool = True


def _a_job_is_running(conn: sqlite3.Connection | None) -> bool:
    """Is anything already working in this library?

    The cheap half of the guard, and it needs no port: a runner child holding
    the card is a row in this database whatever port its app listens on. Asked
    first for that reason.
    """
    if conn is None:
        return False
    try:
        row = conn.execute("SELECT id FROM job WHERE status='running' LIMIT 1").fetchone()
    except sqlite3.Error:  # a library from before this table existed
        return False
    return row is not None


def _health(port: int) -> dict | None:
    """GET /health on the loopback, or None when nothing answers.

    `trust_env=False`, unconditionally and for the reason TASK-089.05 measured:
    with a proxy variable set and no NO_PROXY, a loopback GET goes to the proxy
    and times out, and the launcher's own single-instance check answered "no
    app" about an app that was serving. A wrong answer here would load a model
    beside a running transcription, which is the failure this whole gate is
    about.
    """
    try:
        with httpx2.Client(timeout=HEALTH_TIMEOUT, trust_env=False) as client:
            answer = client.get(f"http://127.0.0.1:{port}/health")
    except Exception:  # noqa: BLE001 - every way of not answering is "nothing there"
        return None
    if answer.status_code != 200:
        return None
    try:
        body = answer.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _serves_this_library(answered: dict) -> bool:
    """Does the app that answered serve the library this proof has open?

    `data_dir` is one of the two fields `/health` gains in TASK-089.17. Until
    that lands every MyScribe answers without them, so this returns False for
    every app there is today - and that is the right answer, because an answer
    without the fields is doubt (ADR-015), and doubt loads nothing.

    Compared after `normcase(realpath())` on both sides, the way TASK-089.17
    compares paths: one directory reached by two spellings is one library.
    """
    theirs = str(answered.get("data_dir") or "").strip()
    if not theirs:
        return False
    return os.path.normcase(os.path.realpath(theirs)) == os.path.normcase(
        os.path.realpath(paths.DATA_DIR)
    )


def _queue_doctor_job() -> int:
    """Queue the GPU checks as a `doctor` job, the way Settings does.

    The single write criterion 2 allows, and only on this branch: the app that
    answered serves this library, so the row lands where the runner that will
    claim it is looking, and the result comes back in the `doctor_last` setting
    the page reads. Queued into a library no app is serving, the row would sit
    there until somebody happened to start one.

    `scribe.web.settings` is imported here and not at the top - it is a page
    module, and the other four doors of this engine have no business paying for
    it - and `queue_gpu_checks` is reused rather than respelled, so "one doctor
    job at a time" stays one rule in one place.

    `jobs.enqueue` also appends a `job.enqueued` line to the library's own log.
    That is part of the same exception rather than a hole in it: the app that
    is serving holds that file open already, and a queue that leaves no trace
    in the log is worse than one that does.
    """
    from scribe.web import settings as settings_ui

    conn = db.connect(paths.DB_PATH)
    try:
        job_id, _ = settings_ui.queue_gpu_checks(conn)
        return job_id
    finally:
        conn.close()


def _gate(conn: sqlite3.Connection | None, port: int, health: Callable[[int], dict | None]) -> Gate:
    """Decide once whether a model may be loaded here, for every line that would."""
    if _a_job_is_running(conn):
        return Gate(may_load=False, reason="not tested (a job is running)", asked_a_port=False)

    answered = health(port)
    if answered is None:
        return Gate(may_load=True)

    if _serves_this_library(answered):
        job_id = _queue_doctor_job()
        return Gate(
            may_load=False,
            reason=(
                f"not tested yet - queued as doctor job {job_id}; the result appears "
                "under Settings > This machine"
            ),
            answered=answered,
            job_id=job_id,
        )
    return Gate(
        may_load=False,
        reason=f"not tested (a MyScribe is running on port {port})",
        answered=answered,
    )


def _uv_version() -> str:
    """`uv --version`, or "" when there is no uv. Never raises: this is a line
    in a report, not a dependency of it."""
    found = shutil.which("uv")
    if not found:
        return ""
    try:
        done = subprocess.run(
            [found, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (done.stdout or "").strip().splitlines()
    return lines[0] if done.returncode == 0 and lines else ""


def _lock_digest() -> str:
    """The first twelve characters of `uv.lock`'s sha256, or "".

    Twelve, because this is for somebody holding two reports side by side and
    asking whether the same stack was installed. All sixty-four would push
    everything else off the line, and verifying a download is not what it is
    for.
    """
    try:
        return hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


def _environment() -> doctor.Check:
    """Which python, which uv, which lockfile - what decides, between them,
    everything that is installed here (ADR-012).

    Information and never a failure. No uv on PATH means the launcher used its
    own copy, and a missing `uv.lock` means this is not a checkout; both are
    worth printing and neither is a broken machine.
    """
    said = [f"python {sys.version.split()[0]} at {sys.executable}"]
    said.append(_uv_version() or "uv not on PATH")
    digest = _lock_digest()
    said.append(f"uv.lock sha256 {digest}" if digest else "no uv.lock beside this package")
    return doctor.Check(name="environment", ok=True, detail="; ".join(said))


def _with_provenance(check: doctor.Check) -> doctor.Check:
    """The same line, saying which copy answered.

    `check_ffmpeg` reports a version string, which says what ran and not which
    of the ffmpegs on this machine it was - and on a machine where the wrong
    one is first on PATH that is the whole question.
    """
    where = shutil.which(check.name)
    if not check.ok or not where:
        return check
    return replace(check, detail=f"{check.detail} (found at {where})")


def _credential_lines(conn: sqlite3.Connection | None) -> list[doctor.Check]:
    """One line per credential and per proxy: what was found and where.

    `found_table` is the list `--plan` prints and Settings renders, and it is
    built so that a value cannot be in it (`credentials.Found`) - which is why
    the report can carry it at all.

    Optional, every one: a machine with no OpenAI key is not a broken machine.
    """
    lines = []
    for row in found_table(conn):
        if row["kind"] == "proxy":
            detail = f"{row['host']} (from {row['source']})"
        elif row["found"]:
            detail = f"found in {row['source']}"
            if row["also_in"]:
                detail += ", also in " + ", ".join(row["also_in"])
            if row["conflict"]:
                detail += " - and they do not agree"
        else:
            detail = "not found on this machine"
        lines.append(
            doctor.Check(name=row["name"], ok=bool(row["found"]), optional=True, detail=detail)
        )
    return lines


PROVIDER_QUESTION = "Ask {label} for one word now? One request, and it costs money [y/N]: "


def _confirm(question: str) -> bool:
    """A y/N question whose answer is No unless somebody types otherwise.

    Never asked without a terminal: an unattended install that blocked on input
    would hang, and one that took silence for yes would spend money nobody
    agreed to (criterion 6).
    """
    if not at_a_terminal():
        return False
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _provider_word(conn: sqlite3.Connection | None, provider: str) -> doctor.Check:
    """One word from the chosen provider, through the probe Settings uses.

    `llm.selftest.probe` and not a request of this module's own: that is the
    one place that sends two module constants and nothing of the user's, and a
    second spelling would be a second thing to keep honest. It stores nothing
    here - `store_result` is the settings page's call, and this report writes
    no rows.
    """
    from scribe.llm import selftest

    label = label_of(provider)
    if conn is None:
        return doctor.Check(
            name="ai-provider",
            ok=False,
            tested=False,
            optional=True,
            detail=f"{label}: no library here to read the key from",
        )
    result = selftest.probe(conn, provider_name=provider)
    if result.ok:
        detail = f"{label} ({result.model}) answered in {result.elapsed_s:.1f}s"
    else:
        detail = f"{label} ({result.model}): {result.detail}"
    return doctor.Check(name="ai-provider", ok=result.ok, optional=True, detail=detail)


def _provider_line(
    conn: sqlite3.Connection | None,
    gate: Gate,
    probe: Callable[[sqlite3.Connection | None, str], doctor.Check],
    confirm: Callable[[str], bool],
) -> doctor.Check:
    """Does the chosen AI provider answer - asked only where asking is free or
    agreed to.

    Three states, one rule each. No provider row is ADR-016's legitimate state:
    nothing is chosen, so nothing is sent. A cloud provider costs money, so it
    is asked for its word only on an explicit yes whose default is No. A local
    one costs nothing but runs on the same card as the transcription, so it
    waits on the same gate.

    Optional in every state, which is what lets the install pass without it:
    this line says what the machine's AI is doing, and no recording depends on
    it.
    """
    provider = _row(conn, ai_ui.PROVIDER_SETTING)
    if not provider:
        return doctor.Check(
            name="ai-provider",
            ok=True,
            optional=True,
            detail="no provider chosen; nothing is sent until somebody chooses (ADR-016)",
        )

    known = ai_ui.llm.PROVIDERS.get(provider)
    if known is None:
        # A row written by a newer MyScribe, or a provider dropped from
        # `PROVIDERS` since. `llm.provider_class` raises `ValueError` for a
        # name it does not have, by design - so the state is read here, before
        # any question is asked, and this line degrades into a sentence the
        # way every other line in this report does.
        return doctor.Check(
            name="ai-provider",
            ok=False,
            tested=False,
            optional=True,
            detail=f"{provider}: not a provider this version knows",
            fix_hint="Settings > AI lists the ones it does.",
        )

    label = label_of(provider)
    if known.is_local:
        if not gate.may_load:
            return doctor.Check(
                name="ai-provider",
                ok=False,
                tested=False,
                optional=True,
                detail=f"{label}: {gate.reason}",
            )
    elif not confirm(PROVIDER_QUESTION.format(label=label)):
        return doctor.Check(
            name="ai-provider",
            ok=False,
            tested=False,
            optional=True,
            detail=f"{label}: configured, not tested",
            fix_hint="Settings > AI has a test button, or answer yes the next time this asks.",
        )
    return probe(conn, provider)


def _gpu_runtime_line(gate: Gate) -> doctor.Check:
    """Which torch, which CUDA, which card - asked only where asking is free.

    `check_gpu_runtime` is a member of `GPU_CHECKS`, and that tuple is this
    repository's own word for "not in this process": `checks(include_gpu=False)`
    drops it, and the settings page queues it as a `doctor` job rather than
    calling it. It sits one level below `check_accelerators`, which answers
    with `torch.cuda.is_available()` and reaches no device; this one asks for
    `get_device_name(0)` and `get_device_properties(0)`, which go through
    torch's lazy init and open a CUDA context on device 0. Small beside a
    model, and still work on a card something else may be holding, which
    ADR-001 keeps inside a runner child.

    Gated and not dropped, because the answer is not lost: `check_gpu_runtime`
    is in the doctor job the match branch queues, and the reason says where it
    will appear.

    Optional only while it is untested. A card that is present and unreachable
    is a required failure and stays one (criterion 5); a card nobody was
    allowed to ask about is already counted, by the `transcription` line above
    that was stopped for the same reason. One withholding, one place in the
    verdict.
    """
    if not gate.may_load:
        return doctor.Check(
            name="gpu-runtime", ok=False, tested=False, optional=True, detail=gate.reason
        )
    return doctor.check_gpu_runtime()


def _transcription_line(gate: Gate, smoke: Callable[[], doctor.Check]) -> doctor.Check:
    """The one line the exit code turns on.

    Required on purpose. Criterion 3 says the exit code counts a queued
    transcription as not tested, so a proof run beside a running app exits 1 -
    the report saying "nobody measured this here", which is exactly what it is
    for and not a failure of the machine.
    """
    if not gate.may_load:
        return doctor.Check(
            name="transcription",
            ok=False,
            tested=False,
            detail=gate.reason,
            fix_hint=(
                ""
                if gate.job_id
                else "Run `python -m scribe.doctor` once nothing else is holding the card."
            ),
        )
    measured = smoke()
    return doctor.Check(
        name="transcription",
        ok=measured.ok,
        detail=measured.detail,
        fix_hint=measured.fix_hint,
    )


def serve_check(data_dir: Path, *, timeout: float = 90.0) -> doctor.Check:
    """Start a MyScribe on ``data_dir``, ask /health, stop it.

    The whole point of the scratch directory: the app's own startup migrates
    the database, reconciles job rows, sweeps recordings and - through
    `finalize.sweep_speaker_passes` - queues an LLM job for every diarized
    recording that never had a speaker pass. None of that may happen to a real
    library because somebody asked for a report, so it happens to an empty
    folder that is thrown away afterwards.

    A port of 0 asks the operating system for one nobody is using, so this
    never disturbs an app on 4242 - or on any other port somebody chose.

    `--no-supervisor` and `--no-browser`: nothing here queues work and nothing
    should open a window. The child is stopped in a `finally`, and killed if it
    will not stop, because a proof that leaves a server behind is worse than no
    proof.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    environment = dict(os.environ, SCRIBE_DATA_DIR=str(data_dir))
    # An `.env` naming another library would send this child there, which is
    # the one thing this check exists to avoid.
    environment.pop("SCRIBE_ENV_FILE", None)
    environment["SCRIBE_ENV_FILE"] = str(data_dir / ".env")
    child = subprocess.Popen(
        [sys.executable, "-m", "scribe", "--port", str(port), "--no-supervisor", "--no-browser"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = time.monotonic()
    try:
        while time.monotonic() - started < timeout:
            if child.poll() is not None:
                return doctor.Check(
                    name="app",
                    ok=False,
                    detail=f"it did not start (exit {child.returncode})",
                    fix_hint=f"Run it yourself and read the error: python -m scribe --port {port}",
                )
            answered = _health(port)
            if answered is not None:
                took = time.monotonic() - started
                version = str(answered.get("version") or "")
                return doctor.Check(
                    name="app",
                    ok=True,
                    detail=(f"started on an empty folder and answered /health in {took:.1f}s"
                            + (f" (version {version})" if version else "")),
                )
            time.sleep(0.25)
        return doctor.Check(
            name="app",
            ok=False,
            detail=f"it did not answer /health within {timeout:.0f}s",
            fix_hint="Start it by hand and read the lines it prints.",
        )
    finally:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def _app_line(gate: Gate, port: int,
              serve: Callable[[Path], doctor.Check] | None = None) -> doctor.Check:
    """Does a MyScribe serve - and this run does not start one to find out.

    Criterion 2 allows a serve check only on a scratch `SCRIBE_DATA_DIR`,
    because the app's startup migrates the database, reconciles job rows and -
    `finalize.sweep_speaker_passes` - queues an LLM job for every diarized
    recording that never had a speaker pass. None of that may happen to a real
    library because somebody asked for a report. So this line reports the app
    that is already answering, and otherwise names the command that starts one
    where it can do no harm.

    Optional: an install that ends with nothing serving is not a broken
    install, it is an install.
    """
    if gate.answered is not None:
        version = str(gate.answered.get("version") or "")
        seen = f"/health already answers on port {port}"
        return doctor.Check(
            name="app",
            ok=True,
            optional=True,
            detail=seen + (f" (version {version})" if version else ""),
        )
    if not gate.asked_a_port:
        return doctor.Check(
            name="app",
            ok=False,
            tested=False,
            optional=True,
            detail="not tested (a job is running; no port was asked)",
        )
    if serve is None:
        return doctor.Check(
            name="app",
            ok=False,
            tested=False,
            optional=True,
            detail=f"not tested: nothing answers on port {port}",
            fix_hint=(
                "Ask one that can do no harm: point SCRIBE_DATA_DIR at an empty directory, then "
                f"`python -m scribe --port {port} --no-supervisor --no-browser`."
            ),
        )
    # Nothing is serving and no job is running, so one is started where it can
    # do no harm: an empty directory of its own, on a port the operating system
    # picked, thrown away afterwards (criterion 2).
    with tempfile.TemporaryDirectory(prefix="myscribe-prove-") as scratch:
        return serve(Path(scratch))


def _blind_spot(port: int) -> doctor.Check:
    """What this proof cannot see, said rather than implied (criterion 4).

    The job-row check covers any port for *this* library; `/health` covers the
    port it was given. Between the two sits a MyScribe on another port serving
    this same library with nothing queued - and this repository's own
    instructions document exactly that arrangement (`--port 4299`).

    The second sentence is the other end of the same doubt. `_health` reads
    anything that is not a 200 with a JSON object as nothing there, which
    opens the gate: an app whose `/health` is broken while it transcribes
    would be missed. Said here rather than turned into a third state, because
    on that port anything else answering really does mean no app.
    """
    return doctor.Check(
        name="blind-spot",
        ok=False,
        tested=False,
        optional=True,
        detail=(
            f"a MyScribe on another port than {port} serving this library was not looked for; "
            "a job it is running is seen, an idle one holding the card is not. An answer on "
            f"{port} that is not a MyScribe /health reads here as nothing there"
        ),
    )


def _log_line(gate: Gate) -> doctor.Check:
    """Where the application log lives - named, and written to on one branch.

    Named, because somebody reading a failed install needs the path. Not
    written by this report, which is what lets it sit under a data directory
    criterion 2 leaves alone - except where the gate queued a doctor job:
    `jobs.enqueue` appends a `job.enqueued` line to it. That write is part of
    criterion 2's exception, and it belongs in the report rather than in a
    docstring, because the alternative is a line that says "not written" on
    the one branch where something was.

    There is no separate install log anywhere in this repository. TASK-089.09
    read "mirrors the report into the install log" as the launcher teeing this
    child's output into one; that tee does not exist - `run_setup` hands each
    line to a status callback and opens no file - so this line names the
    application log and claims nothing about a second one.
    """
    if gate.job_id:
        return doctor.Check(
            name="log",
            ok=True,
            detail=f"{applog.path()} (the queued job's job.enqueued line is written here)",
        )
    return doctor.Check(name="log", ok=True, detail=f"{applog.path()} (named here, not written)")


def _announce(label: str) -> None:
    """Say which check has started, on stderr.

    stderr so that `--prove > report.txt` keeps exactly the report it had, and
    plain lines with no carriage returns or ANSI, because legacy conhost is
    still what a lot of these machines open. It matters for one check above
    all: a cold transcription downloads 1.6 GB, and a terminal that prints
    nothing for two minutes reads as a hang.
    """
    print(f"checking {label}", file=sys.stderr, flush=True)


def _watched() -> bool:
    """Is anybody looking at this run? `sys.stderr`, not stdin: the report may
    be piped somewhere while a person watches the progress."""
    return at_a_terminal(sys.stderr)


def _take(measured: dict[str, doctor.Check], name: str) -> doctor.Check:
    """One of the doctor's lines, by name.

    A KeyError here means a check was renamed and this report has silently
    lost a line it promises. Loud is the right failure for that.
    """
    return measured.pop(name)


def prove(
    conn: sqlite3.Connection | None,
    *,
    port: int = DEFAULT_PORT,
    smoke: Callable[[], doctor.Check] | None = None,
    health: Callable[[int], dict | None] | None = None,
    provider_probe: Callable[[sqlite3.Connection | None, str], doctor.Check] | None = None,
    confirm: Callable[[str], bool] | None = None,
    on_start: Callable[[str], None] | None = None,
    serve: Callable[[Path], doctor.Check] | None = None,
) -> list[doctor.Check]:
    """The report an install ends on: what this machine is, measured.

    An install that ends on "done" proves nothing, and until now no code path
    ran the doctor after setup at all. This is that path, and two things it
    must not do are what shaped it.

    **It does not write to the library it reports on.** Not tidiness: a proof
    run after a `git pull`, with an older app still serving, would otherwise
    migrate the live database out from under it. So the doctor's checks run in
    `read_only` mode, `conn` is the immutable open and never `db.connect`, and
    the one exception is the job row queued for an app that is provably serving
    this library. What it reads of the library - the schema version, the
    credential rows, the provider - it reads without moving a byte.

    **It does not load a model beside something that might be using the card.**
    ADR-001 keeps GPU work in runner children, one at a time, and the 9B and
    12B models both failed to start here while 12.7 GB of the 16 GB card was
    in use. So `Gate` decides once, and every line that would load a model asks
    it: a running job row, or an app that answers, and the transcription reads
    "not tested" with the reason. With nothing answering and nothing running,
    the smoke measures in this process, which is what `python -m scribe.doctor`
    has always done from a terminal (ADR-015 records that reading).

    The four probes are arguments for one reason: without them a test of the
    gate would have to load a real model to prove that it did not.

    Returns the report as `doctor.Check` objects - the doctor's own shape, not
    a second one - for `doctor.render` to print and `doctor.exit_code` to
    judge.
    """
    smoke = smoke or doctor.check_gpu_smoke_read_only
    health = health or _health
    provider_probe = provider_probe or _provider_word
    confirm = confirm or _confirm

    measured = {
        check.name: check
        for check in doctor.checks(include_gpu=False, on_start=on_start, read_only=True)
    }
    # Criterion 1's order, and the pops are what make it one order rather than
    # two: a check taken out here cannot also appear in the middle block.
    report = [_environment(), _with_provenance(_take(measured, "ffmpeg")), _with_provenance(_take(measured, "ffprobe"))]
    accel_line = _take(measured, "accel")
    ollama_line = _take(measured, "ollama")
    report += list(measured.values())
    report.append(accel_line)
    # Decided here, before the first line that would touch the card. Every
    # line from this point down asks it.
    gate = _gate(conn, port, health)
    report.append(_gpu_runtime_line(gate))
    report += _credential_lines(conn)
    report.append(ollama_line)

    # Execution order is not report order, and here they differ on purpose. A
    # local provider answers on the same card, and ollama keeps that model
    # resident for five minutes (`KEEP_ALIVE`), so asking it first would leave
    # VRAM occupied while the smoke loads beside it - the collision measured
    # in scribe/llm/ollama.py. The smoke is measured first; the two lines are
    # appended in criterion 1's order.
    transcription = _transcription_line(gate, smoke)
    report.append(_provider_line(conn, gate, provider_probe, confirm))
    report.append(transcription)
    report.append(_app_line(gate, port, serve or serve_check))
    report.append(_blind_spot(port))
    report.append(_log_line(gate))
    return report


# --- checking a typed credential ------------------------------------------------------


def _client(transport):
    """The client the checks go out on. `httpx2` because that is the pinned one
    this app already uses, and no `trust_env=False`: these are remote hosts, and
    the proxy bypass is for the loopback only (TASK-089.05)."""
    return httpx2.Client(transport=transport, timeout=15.0)


def verify_huggingface(token: str, *, transport=None) -> Verdict:
    """Would the Hub serve the gated pipeline to this token? One HEAD, no weights.

    401 and 403 are the two answers that mean "not for you", and the fix
    differs: the first is a token the Hub does not know, the second a token it
    knows that has not accepted the conditions - so the second carries the URL
    where they are accepted. Anything else is not a verdict about the token and
    says so instead of blaming it.

    Setup's own check rather than the doctor's shared sentence
    (`doctor._gated_repo_reachable`): that one belongs to TASK-089.12, and this
    one has two sentences to give where it has one.
    """
    repo = diarize.DEFAULT_PIPELINE
    url = f"https://huggingface.co/{repo}/resolve/main/config.yaml"
    try:
        with _client(transport) as client:
            answer = client.head(url, headers={"Authorization": f"Bearer {token}"})
    except httpx2.HTTPError as exc:
        return Verdict(False, f"the Hub could not be asked about this token ({exc.__class__.__name__})")
    if answer.status_code == 401:
        return Verdict(False, "token not recognised", refused=True)
    if answer.status_code == 403:
        return Verdict(
            False,
            f"conditions not accepted: accept them at https://hf.co/{repo} and try again",
            refused=True,
        )
    if answer.is_success or answer.is_redirect:
        return Verdict(True)
    return Verdict(False, f"the Hub answered HTTP {answer.status_code}, which says nothing about the token")


def verify_openrouter(key: str, *, transport=None) -> Verdict:
    """OpenRouter's free key check.

    An authenticated request to the key endpoint costs nothing and answers 401
    for a key that is missing, invalid or disabled, so a bad key is turned away
    without the paid one-word probe. Read from OpenRouter's documentation and
    probed without a key on 2026-09-20 (ADR-015, Open Questions); a run with a
    real key, valid or revoked, is the proof still owed.
    """
    try:
        with _client(transport) as client:
            answer = client.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"})
    except httpx2.HTTPError as exc:
        return Verdict(False, f"OpenRouter could not be asked about this key ({exc.__class__.__name__})")
    if answer.status_code == 401:
        return Verdict(False, "key not recognised, or disabled", refused=True)
    if answer.is_success:
        return Verdict(True)
    return Verdict(False, f"OpenRouter answered HTTP {answer.status_code}, which says nothing about the key")


def verify_openai(key: str, *, provider=None) -> Verdict:
    """The authenticated model list, which is what the settings page already
    asks for: a key OpenAI rejects raises before anything is saved."""
    from scribe.llm import base

    made = provider if provider is not None else ai_ui.llm.PROVIDERS["openai"](api_key=key)
    try:
        made.models()
    except base.AuthError as exc:
        return Verdict(False, str(exc), refused=True)
    except base.LlmError as exc:
        return Verdict(False, f"OpenAI could not be asked about this key ({exc})")
    finally:
        with contextlib.suppress(Exception):
            made.close()
    return Verdict(True)


CHECKS: dict[str, Callable[[str], Verdict]] = {
    "huggingface": verify_huggingface,
    "openrouter": verify_openrouter,
    "openai": verify_openai,
}
"""One check per credential, by the name `scribe.credentials` uses. A
credential with no entry is written as typed: there is nothing to ask."""


def verify(name: str, value: str) -> Verdict:
    check = CHECKS.get(name)
    return check(value) if check else Verdict(True)


# --- applying the answers ---------------------------------------------------------


def _validate(answers: Answers) -> None:
    """Every answer, before the first write.

    All of them at once and ahead of everything, because half an applied
    sitting is worse than a refused one: a token used to be written and the
    provider checked afterwards, so a typing mistake in `--provider` left a
    token in the library with an exit code that said nothing was saved.
    """
    if answers.provider and answers.provider != LATER and answers.provider not in ai_ui.llm.PROVIDERS:
        raise ValueError(f"unknown provider {answers.provider!r}")
    if answers.tier and answers.tier not in TIERS:
        raise ValueError(f"unknown tier {answers.tier!r}")
    for name in answers.llm_keys:
        if name not in credentials.CREDENTIALS:
            raise ValueError(f"unknown provider {name!r}")
    if answers.ollama_model and "\n" in answers.ollama_model:
        raise ValueError("a model name is one line")
    if answers.ollama_new_model and answers.ollama_new_model not in ollama_setup.MODEL_BYTES:
        # Only what the offer listed: a tag nobody was shown is not a thing
        # a new Ollama pulls on somebody's word.
        raise ValueError(
            f"unknown model {answers.ollama_new_model!r}: the offer lists {', '.join(ollama_setup.MODEL_BYTES)}"
        )


def apply(
    answers: Answers,
    conn: sqlite3.Connection,
    *,
    env_file: Path | None = None,
    on_progress=None,
    check: Callable[[str, str], Verdict] | None = None,
    announce: Callable[[str], None] | None = None,
    on_event: Callable[[bool], None] | None = None,
    probe: Callable[[sqlite3.Connection], doctor.Check] | None = None,
) -> dict:
    """Write the answers down and do what they ask for. Returns a report.

    `on_event` is told when a third-party installer starts and ends, and
    `probe` is the one-word check announced after a model was pulled into an
    Ollama MyScribe installed (TASK-089.18); both are seams for the same
    reason `check` is one.

    A typed secret goes to its settings row and nowhere else. That reverses two
    recorded things knowingly (TASK-040.06's criterion #2 and the reason the
    old `write_token` gave): the row is what Settings can show and clear and
    what every lookup reads first through the resolver (TASK-089.04), and a
    secret copied to `.env` as well would be a second copy in the less
    protected of the two files - which then goes stale.

    `check` is the seam that asks a service whether a typed credential is any
    good, and it is a parameter rather than a default so that a caller who
    already has a verdict - or a test - reaches no network by accident. The
    doors pass it; `main` is one.

    `env_file` is kept because the callers and their tests name it, and because
    a future answer may still belong in `.env`; no secret goes there.
    """
    _validate(answers)
    report: dict = {"wrote": [], "downloaded": [], "reopen": [], "notes": []}
    answered: list[str] = []

    # 2. the library (TASK-089.19). Only "new" and a skip reach this far - a
    #    document that chose a library is `_library_door`'s - and either way a
    #    library found elsewhere is left as it is and said where (criterion 3).
    if answers.library == NEW_LIBRARY or "library" in answers.skipped:
        if answers.library == NEW_LIBRARY:
            answered.append("library")
        elsewhere = libraries.found(paths.DATA_DIR)
        if elsewhere:
            report["notes"].append(
                "Left untouched, and still there: "
                + "; ".join(f"{lib.data_dir} ({lib.recordings} recordings)" for lib in elsewhere)
                + ". " + libraries.CHANGE_LIBRARY
            )

    def accepted(name: str, value: str, question: str) -> bool:
        """A typed credential, checked before it is saved. A refused one is not
        written and its question comes back in `reopen`."""
        if check is None:
            return True
        if announce is not None:
            announce(f"checking the {credentials.CREDENTIALS[name].label} credential with one request")
        verdict = check(name, value)
        if verdict.ok:
            return True
        report["notes"].append(f"{question}: {verdict.why}")
        if verdict.refused:
            report["reopen"].append(question)
            return False
        return True  # not an answer about the credential: saved, and said so

    token = answers.hf_token.strip()
    if token and accepted("huggingface", token, "hf_token"):
        ai_ui.setting_put(conn, diarize.SETTING_TOKEN, token)
        report["wrote"].append("hf_token")
        answered.append("hf_token")

    for name, raw in sorted(answers.llm_keys.items()):
        value = raw.strip()
        question = f"llm_key_{name}"
        if value and accepted(name, value, question):
            ai_ui.setting_put(conn, credentials.CREDENTIALS[name].setting_key, value)
            report["wrote"].append(question)
            answered.append(question)

    if answers.provider and answers.provider != LATER:
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, answers.provider)
        report["wrote"].append("provider")
        answered.append("llm_provider")
        cloud = not ai_ui.llm.PROVIDERS[answers.provider].is_local
        # What was written, not what was offered: a key the service refused is
        # not saved and its question reopens, and reading the offer instead
        # kept the card back in exactly that case - the likelier of the two
        # ways to end up with a provider and no key.
        has_key = (
            f"llm_key_{answers.provider}" in report["wrote"]
            or credentials.resolve(conn, answers.provider).found
        )
        if cloud and not has_key:
            report["notes"].append(NO_KEY_CARD.format(label=label_of(answers.provider)))

    if answers.ollama_model:
        ai_ui.setting_put(conn, ai_ui.MODEL_SETTING_PREFIX + "ollama", answers.ollama_model)
        report["wrote"].append("llm_model_ollama")
        answered.append("llm_model_ollama")

    # After the provider row, so that a cloud choice made in this sitting is
    # the row the pull leaves alone; before the weights, so that a yes to
    # Ollama is honoured even when that download then fails (TASK-089.18).
    _apply_ollama(answers, conn, report, answered, announce, on_progress, on_event, probe)

    # The tier and the speakers default are written one row at a time. Through
    # `save_defaults` they went in one executemany with the language, so
    # answering the tier wrote a `default_diarize` nobody answered - a row
    # every upload rewrites and seven call sites read (ADR-015, Must Not).
    if answers.tier:
        transcribe_dialog.save_tier(conn, answers.tier)
        report["wrote"].append("defaults")
        answered.append("default_tier")
    if answers.diarize is not None:
        transcribe_dialog.save_diarize(conn, answers.diarize)
        if "defaults" not in report["wrote"]:
            report["wrote"].append("defaults")
        answered.append("default_diarize")

    # 12. the watch folder (TASK-089.20). The row Settings writes, through the
    #     function Settings calls, with the defaults as they stand after the
    #     tier above was written - and nothing is ingested here: the watcher
    #     thread the app starts takes the folder in. `scribe.web.settings` is
    #     a page module and is imported where it is needed, as
    #     `_queue_doctor_job` does. A refusal is a sentence in the notes and
    #     the question comes back; the FastAPI exception stops here, and only
    #     its sentence goes on.
    if answers.watch_folder:
        from fastapi import HTTPException
        from scribe.web import settings as settings_ui

        try:
            settings_ui.add_watched(conn, answers.watch_folder, transcribe_dialog.read_defaults(conn))
        except HTTPException as refused:
            report["notes"].append(f"watch_folder: {refused.detail}")
            report["reopen"].append("watch_folder")
        else:
            report["wrote"].append("watch_folder")
            answered.append("watch_folder")
    # TASK-089.22: start at login. A No is an answer too and is recorded as
    # one, or it would come back at every start (criterion 3). A Yes makes the
    # one call the Settings switch makes - `autostart.enable()` with no
    # arguments - so both doors register the identical item (criterion 2). An
    # OS that refuses, or a machine with nothing to start, reopens the question
    # and the sitting still ends.
    if answers.start_at_login is not None:
        answered.append("start_at_login")
        if answers.start_at_login:
            try:
                entry = autostart.enable()
            except (autostart.NothingToStart, OSError, ValueError) as refused:
                report["notes"].append(f"start_at_login: {refused}")
                report["reopen"].append("start_at_login")
            else:
                report["wrote"].append("start_at_login")
                report["notes"].append(f"MyScribe starts when you log in: {entry.where}")

    if answers.fetch_models:
        answered.append("fetch_models")
        try:
            report["downloaded"] = models.ensure(
                answers.wanted or None,
                token=diarize.hf_token(conn),
                tier=answers.tier or _row(conn, transcribe_dialog.SETTING_TIER) or "",
                on_progress=on_progress,
            )
        except models.ModelError:
            # The sitting still ended, so it is still stamped: the questions
            # were put, and a download that failed is a thing to try again, not
            # a reason to ask everything a second time at the next start. The
            # question itself is open rather than answered - nothing was
            # fetched - and `--plan` therefore offers it again. The caller
            # still gets the error and its exit code.
            report["reopen"].append("fetch_models")
            _write_stamp(answers, report, answered, conn)
            raise

    _write_stamp(answers, report, answered, conn)
    return report


def _ollama_probe(conn: sqlite3.Connection) -> doctor.Check:
    """The one-word probe after a pull, under TASK-089.13's rule: a local
    provider answers on the same card as a transcription, so it waits on the
    same gate `--prove` uses, and reads "not tested" with the reason while an
    app answers or a job runs."""
    gate = _gate(conn, DEFAULT_PORT, _health)
    if not gate.may_load:
        return doctor.Check(name="ai-provider", ok=False, tested=False, optional=True, detail=gate.reason)
    return _provider_word(conn, "ollama")


def _both_numbers(room: dict, model: str) -> str:
    """What a folder that is too small says: both numbers, with their bytes."""
    return (
        f"{room['path']} has {models.human(room['free'])} free ({room['free']:,} bytes) and {model} needs "
        f"{models.human(room['needed'])} ({room['needed']:,} bytes)."
    )


def _apply_ollama(
    answers: Answers,
    conn: sqlite3.Connection,
    report: dict,
    answered: list[str],
    announce: Callable[[str], None] | None,
    on_progress,
    on_event: Callable[[bool], None] | None,
    probe: Callable[[sqlite3.Connection], doctor.Check] | None,
) -> None:
    """Rows 7-8a and the G6 exception, applied - or refused.

    The engine detects again here rather than trusting the document: a yes
    written for a machine that was absent when the plan was drawn, and has an
    Ollama by the time the answers arrive, installs nothing (ADR-017). A
    marker the plan saw lapse is removed here, because a plan writes nothing.
    A No is recorded like any other answer, so the next start does not ask
    again; the offer itself comes back only while Ollama is absent.
    """
    say = announce if announce is not None else (lambda line: None)
    probe = _ollama_probe if probe is None else probe
    state = ollama_setup.state()
    marker = ollama_setup.read_marker()
    standing = ollama_setup.valid_marker(state, marker)
    if ollama_setup.marker_path().exists() and not standing:
        ollama_setup.remove_marker()

    for question, given in (
        ("ollama_install", answers.ollama_install),
        ("ollama_models_dir", answers.ollama_models_dir),
        ("ollama_pull_resume", answers.ollama_pull_resume),
    ):
        if given is not None:
            answered.append(question)
    if answers.ollama_new_model:
        answered.append("ollama_new_model")

    if answers.ollama_install:
        if state.state != ollama_setup.ABSENT:
            report["notes"].append(
                f"Ollama is on this machine now ({ollama_setup.describe(state)}) and is left alone: "
                "nothing was installed."
            )
        else:
            _install_ollama(answers, conn, report, say, on_progress, on_event, probe)
    elif answers.ollama_pull_resume:
        if standing and marker is not None and state.state == ollama_setup.RUNNING_NO_CHAT_MODEL:
            _pull_into_own(
                str(marker["model"]), conn, report, say, on_progress, probe,
                models_dir=str(marker.get("models_dir") or ""),
            )
        elif standing and marker is not None:
            report["notes"].append(ollama_setup.not_answering_yet_sentence(str(marker["model"])))
        else:
            report["notes"].append(
                "No pull: that Ollama is left alone. The copyable command is "
                f"`ollama pull {ollama_setup.DEFAULT_MODEL}`."
            )


def _install_ollama(
    answers: Answers,
    conn: sqlite3.Connection,
    report: dict,
    say: Callable[[str], None],
    on_progress,
    on_event: Callable[[bool], None] | None,
    probe: Callable[[sqlite3.Connection], doctor.Check],
) -> None:
    """The yes, in the order the plan showed it: room, the artifact and its
    checks, the installer, the poll, the room again, the pull.

    Both room checks are for the model that was chosen, not the default the
    plan measured for (criterion 10). A folder that is too small ends the
    whole offer with both numbers and installs nothing, as Robert decided
    (row 8a, G8); on macOS and Linux the variable stays a sentence with the
    command, so `models_dir` reaches `install` on Windows only.
    """
    # The plan and this apply are two processes, so the release is asked for
    # again here (TASK-095): what is installed is what this names below, with
    # its own two digests, and a release published in between is the one
    # installed - ADR-021 (Proposed) records that window.
    try:
        plan = ollama_setup.install_plan()
    except ollama_setup.InstallError as exc:
        report["notes"].append(f"{exc}. Nothing was downloaded and nothing was installed.")
        report["reopen"].append("ollama_install")
        return
    model = answers.ollama_new_model or ollama_setup.DEFAULT_MODEL
    needed = ollama_setup.MODEL_BYTES[model]
    models_dir: Path | None = None
    at_default = ollama_setup.room(ollama_setup.default_models_dir(), needed)
    if not at_default["enough"]:
        if answers.ollama_models_dir is not True:
            report["notes"].append(_both_numbers(at_default, model) + " Nothing was installed and nothing was pulled.")
            report["reopen"].append("ollama_install")
            return
        with_us = ollama_setup.models_dir_with_myscribe()
        there = ollama_setup.room(with_us, needed)
        if not there["enough"]:
            report["notes"].append(_both_numbers(there, model) + " Nothing was installed and nothing was pulled.")
            report["reopen"].append("ollama_install")
            return
        if plan["platform"] == "win32":
            models_dir = with_us
        else:
            report["notes"].append(ollama_setup.models_dir_sentence(plan["platform"], with_us))

    say(f"downloading {plan['name']} of Ollama {plan['tag']} from {plan['url']} "
        f"({plan['bytes']:,} bytes, sha256 {plan['sha256']}, the same in the release's sha256sum.txt)")
    outcome = ollama_setup.install(
        plan, models_dir=models_dir, model=model, on_progress=on_progress, on_line=say, on_event=on_event,
    )
    report["notes"].append(outcome.sentence)
    if not outcome.installed:
        # A failure, or the Mac's guided path: by MyScribe's own test Ollama
        # is still absent, so the offer comes back at the next sitting.
        report["reopen"].append("ollama_install")
        return

    provider = ollama.OllamaProvider(conn)
    try:
        say(f"waiting up to {int(ollama_setup.VERSION_WAIT_S)} s for Ollama to answer /api/version")
        version = ollama_setup.wait_for_version(provider)
        if not version:
            report["notes"].append(ollama_setup.not_answering_yet_sentence(model))
            return
        where = models_dir if models_dir is not None else ollama_setup.default_models_dir()
        again = ollama_setup.room(where, needed)
        if not again["enough"]:
            report["notes"].append(
                _both_numbers(again, model) + " Nothing was pulled; the pull is offered again in a --setup sitting."
            )
            return
        _pull_into_own(
            model, conn, report, say, on_progress, probe,
            provider=provider, models_dir=str(models_dir) if models_dir is not None else "",
        )
    finally:
        provider.close()


def _pull_into_own(
    model: str,
    conn: sqlite3.Connection,
    report: dict,
    say: Callable[[str], None],
    on_progress,
    probe: Callable[[sqlite3.Connection], doctor.Check],
    *,
    provider: ollama.OllamaProvider | None = None,
    models_dir: str = "",
) -> None:
    """The one pull ADR-017 allows: into the Ollama MyScribe installed, of the
    one model that was agreed to. On success the rows the yes meant -
    `llm_model_ollama`, and `llm_provider` only where no row says otherwise -
    the marker goes, because that Ollama has now been seen ready, and the
    one-word probe is announced. Where the models landed is checked, never
    assumed (criterion 10)."""
    own = provider is None
    provider = ollama.OllamaProvider(conn) if provider is None else provider
    try:
        say(f"pulling {model} into Ollama")
        try:
            ollama_setup.pull(provider, model, on_progress=on_progress)
        except ollama_setup.PullError as exc:
            report["notes"].append(
                f"{exc}. The pull is offered again in a --setup sitting, or run `ollama pull {model}` yourself."
            )
            return
    finally:
        if own:
            provider.close()

    ai_ui.setting_put(conn, ai_ui.MODEL_SETTING_PREFIX + "ollama", model)
    report["wrote"].append("llm_model_ollama")
    if not _row(conn, ai_ui.PROVIDER_SETTING):
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "ollama")
        report["wrote"].append("provider")
    ollama_setup.remove_marker()
    if models_dir:
        if ollama_setup.models_landed(Path(models_dir)):
            report["notes"].append(f"{model} landed in {models_dir}, where OLLAMA_MODELS points.")
        else:
            report["notes"].append(
                f"OLLAMA_MODELS was set to {models_dir} for your account, but {model} did not land there: "
                "the Ollama the installer started did not read the variable. The model is in Ollama's own "
                "folder; an Ollama started later from the Start menu reads the variable, and the next pull "
                "goes where it points."
            )
    check = probe(conn)
    say(f"{label_of('ollama')}: {check.detail}")


def _states(answers: Answers, report: dict, answered: list[str], conn: sqlite3.Connection) -> dict[str, str]:
    """What this sitting did with each question, on top of what earlier ones did.

    The four sources are written weakest first, so a later line wins: that is
    the precedence `STATES` names, in the one place it is applied. What an
    earlier sitting recorded is the base, because a stamp is the record of
    everything that was ever put - a re-run that touches one question must not
    erase the rest (spec section 2, "carries the known states over").

    `not_needed` is `setdefault` for the same reason: a credential that is on
    this machine is worth recording where nothing is known about the question,
    and must never overwrite an answer somebody gave. It is read off
    `credentials.find_all`, which costs no network and no Ollama probe.
    """
    states_now = dict(states())
    for row in credentials.find_all(conn):
        if row.found:
            states_now.setdefault(question_of(row.credential), "not_needed")
    for question in answers.skipped:
        states_now[question] = "skipped"
    for question in answered:
        states_now[question] = "answered"
    for question in report["reopen"]:
        states_now[question] = "open"
    return states_now


def _write_stamp(answers: Answers, report: dict, answered: list[str], conn: sqlite3.Connection) -> None:
    """The sitting ended, so it is recorded - a failed or skipped download
    included.

    States and nothing else. `provider`, `tier`, `hf_token` and `fetched` were
    in the stamp until TASK-089.11 and went with the shape: this file is what
    the gate reads, and the gate has no business knowing anybody's provider.
    What was chosen lives in the settings rows, where Settings can show it.

    `ended` is what tells a stamp of this writer's from anything else with a
    `contract` in it - a half-finished sitting, an editor, a future field. The
    gate treats one without it as no stamp at all and asks again, which is the
    safe way round (spec section 2).

    It goes down in one piece - a temp file beside it, then `os.replace` (spec
    section 2). A truncating write would empty the file before it has the new
    text, and under this contract that file is the only record of what somebody
    skipped on purpose and of what the migration carried over: losing it costs
    those states for good, not one extra sitting.
    """
    stamp_path().parent.mkdir(parents=True, exist_ok=True)
    written = stamp_path().with_name(stamp_path().name + ".tmp")
    written.write_text(
        json.dumps(
            {
                "contract": CONTRACT,
                "ended": time.time(),
                "questions": _states(answers, report, answered, conn),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(written, stamp_path())


def from_document(document: dict) -> Answers:
    """The answers of one `--apply-stdin` document.

    A null answer is a deliberate skip and is recorded as one; an absent id was
    never shown and is not mentioned. Neither writes anything, which is what
    makes an empty document an unattended install that leaves the machine as it
    was. An answer this engine does not know is ignored rather than refused -
    a proxy answer, for one, which no question ever asks (TASK-089.05).
    """
    given = document.get("answers")
    if not isinstance(given, dict):
        given = {}
    skipped = sorted(key for key, value in given.items() if value is None)

    def text(key: str) -> str:
        value = given.get(key)
        return value.strip() if isinstance(value, str) else ""

    provider = text("llm_provider")
    keys = {
        name: text(f"llm_key_{name}")
        for name in credentials.CREDENTIALS
        if text(f"llm_key_{name}")
    }
    def yes_no(key: str) -> bool | None:
        """A yes-no answer in three states: a yes, a No that is still an
        answer, and nothing said. A word that is neither is nothing said."""
        value = given.get(key)
        if value is True:
            return True
        if value is False:
            return False
        if isinstance(value, str):
            word = value.strip().lower()
            if word in ("yes", "true", "1"):
                return True
            if word in ("no", "false", "0"):
                return False
        return None

    fetch = given.get("fetch_models")
    return Answers(
        hf_token=text("hf_token"),
        provider=provider,
        tier=text("default_tier"),
        # `default_diarize` is deliberately not read: no question asks it and a
        # document that carries one writes nothing (ADR-015, Must Not). The
        # typed flag is the one writer setup keeps.
        diarize=None,
        fetch_models=fetch is True or (isinstance(fetch, str) and fetch.strip().lower() in ("yes", "true", "1")),
        llm_keys=keys,
        ollama_model=text("llm_model_ollama"),
        ollama_install=yes_no("ollama_install"),
        ollama_new_model=text("ollama_new_model"),
        ollama_models_dir=yes_no("ollama_models_dir"),
        ollama_pull_resume=yes_no("ollama_pull_resume"),
        skipped=skipped,
        watch_folder=text("watch_folder"),  # 12. the watch folder (TASK-089.20)
        # TASK-089.22: a null stays a skip through `skipped`; "no" is an answer.
        start_at_login=_yes_no(given.get("start_at_login")),
        library=text("library"),  # TASK-089.19
        library_folder=text("library_folder"),
    )


# --- the doors --------------------------------------------------------------------


def at_a_terminal(stream=None) -> bool:
    """Is somebody really at a terminal, or is this a child with no stdin?

    `isatty()` alone is not the question on Windows, and the difference is not
    academic: the NUL device is a character device and answers True, and NUL is
    exactly what `subprocess.DEVNULL` hands a child - which is what the
    launcher gives this one (ADR-011's `run_streaming`). Measured here on
    2026-09-22: `python -m scribe.setup < /dev/null` answered `isatty()` True,
    walked into the asker and died on `EOFError`, having printed a question
    nobody could answer. A console handle answers `GetConsoleMode`; NUL does
    not.

    A stream with no file descriptor - a test's, a captured one - is taken at
    its word. Anything else that cannot be asked is read as "no terminal",
    because that path asks nothing, writes no stamp and says how to answer,
    while the other one hangs or raises.
    """
    stream = stream if stream is not None else sys.stdin
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform != "win32":
        return True
    try:
        fileno = stream.fileno()
    except Exception:  # noqa: BLE001 - a stream without one is a test's, and honest
        return True
    try:
        import ctypes
        import msvcrt

        mode = ctypes.c_ulong()
        handle = msvcrt.get_osfhandle(fileno)
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:  # noqa: BLE001 - no verdict is the quiet path, not the hanging one
        return False


class Progress:
    """What a download looks like from outside.

    One JSON line per whole percent when stdout is a pipe - a front-end parses
    those, and a line per chunk would be thousands for one file - and a bar
    with speed and what is left when somebody is watching a terminal.
    """

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.tty = at_a_terminal(self.stream)
        self.shown: dict[str, int] = {}
        self.started: dict[str, float] = {}

    def __call__(self, repo: str, done_bytes: int, total: int) -> None:
        percent = int(100 * done_bytes / total) if total else 0
        if not self.tty:
            if self.shown.get(repo) == percent:
                return
            self.shown[repo] = percent
            line = {"event": "progress", "repo": repo, "percent": percent,
                    "bytes": done_bytes, "total": total}
            print(json.dumps(line), file=self.stream, flush=True)
            return
        began = self.started.setdefault(repo, time.monotonic())
        taken = max(time.monotonic() - began, 0.001)
        speed = done_bytes / taken
        left = (total - done_bytes) / speed if speed > 0 and total else 0
        filled = int(percent / 5)
        bar = "#" * filled + "-" * (20 - filled)
        print(
            f"  {repo:<45} [{bar}] {percent:3d}%  {models.human(int(speed))}/s  {int(left):4d}s left",
            end="\r",
            file=self.stream,
            flush=True,
        )

    def installer(self, running: bool) -> None:
        """One line when a third-party installer starts and one when it ends.

        JSON on a pipe, which is what the launcher reads to refuse Quit in
        between (TASK-089.18, criterion 14); prose on a terminal. A pipe whose
        reader has gone - a launcher that died on the Ctrl-C the installer must
        survive - is not a reason to stop: the line is dropped and the install
        goes on to write its marker.
        """
        if self.tty:
            line = "Ollama's installer is running; Ctrl-C waits for it." if running else "Ollama's installer has finished."
        else:
            line = json.dumps({"event": "installer", "running": running})
        with contextlib.suppress(OSError):
            print(line, file=self.stream, flush=True)


def render(document: dict) -> str:
    """The plan as a person reads it: what was found, then what is open.

    The same list the Tk dialog draws, printed. It exists here because a
    machine with no window and no terminal still has to be told what was found
    and what it would have been asked.
    """
    lines = ["Found on this machine"]
    for row in document["found"]:
        if row["kind"] == "proxy":
            lines.append(f"  {row['label']:<22} {row['host']} ({row['source']})")
            continue
        where = row["source"] if row["found"] else "not found"
        lines.append(f"  {row['label']:<22} {where}")
        if row["also_in"]:
            note = "; also " + ", ".join(row["also_in"])
            if row["conflict"]:
                note += " - which defines a different value, not used"
            lines.append(f"  {'':<22} {note.lstrip('; ')}")
        if row["note"]:
            lines.append(f"  {'':<22} note: {row['note']}")
    ollama = document["ollama"]
    lines.append(f"  {'Ollama':<22} {ollama['note']}")

    lines.append("Still open")
    if not document["questions"]:
        lines.append("  nothing: everything this engine asks about is answered or found")
    for question in document["questions"]:
        lines.append(f"  {question['id']:<22} {question['text']}")
        default = question["default"] or "skip"
        lines.append(f"  {'':<22} default: {default}. Skipping: {question['if_skipped']}")
        lines.append(f"  {'':<22} later: {question['answer_later']}")
    return "\n".join(lines)


ASKS_AGAIN = 3
"""How often a word that is on no list is asked for again before the shown
default is taken. Bounded rather than endless: somebody at a terminal gets
another try at a typing mistake, and a script feeding one cannot spin for as
long as anybody lets it."""


def _listed(question: dict, typed: str) -> str | None:
    """The choice a typed word names, or None when it names none of them.

    Matched against the `value`, which is what the console printed and what
    gets written, and case does not count - somebody who read "Ollama" in a
    dialog and typed it here has answered. A label that is not also its value
    ("Maximum" for `max`) is not one of the answers offered and is asked again
    rather than guessed at. A question with no choices takes the word as typed.
    """
    choices = question.get("choices") or []
    if not choices:
        return typed
    wanted = typed.casefold()
    for choice in choices:
        if str(choice["value"]).casefold() == wanted:
            return str(choice["value"])
    return None


def _answer_to(question: dict, *, stream, read) -> str | None:
    """One question, asked until the answer is one this engine can apply.

    A word that is on no list used to be taken as typed: `Max` then reached
    `_validate` as a tier and raised, and `llm_model_ollama`, which nothing
    validates, was written as a model Ollama has never heard of - the "never
    save a model nobody chose" the question's own skip exists for.
    """
    default = question["default"]
    prompt = f"[{default or 'skip'}] (s to skip): "
    for _ in range(ASKS_AGAIN):
        if question["kind"] == "secret":
            print(f"  skipping: {question['if_skipped']}", file=stream)
            typed = getpass.getpass(prompt)
        else:
            print(prompt, end="", file=stream, flush=True)
            typed = read()
        typed = (typed or "").strip()
        if typed.lower() == "s":
            return None
        if not typed:
            return default or None
        chosen = _listed(question, typed)
        if chosen is not None:
            return chosen
        print("  not one of the answers offered - they are listed above.", file=stream)
    print(f"  taking {default or 'skip'}.", file=stream)
    return default or None


def ask(questions: list[dict], *, out=None, read=input) -> dict:
    """The console asker, over the same list every other door renders.

    Enter takes the shown default, `s` skips, and a secret is read with
    `getpass` so it is not echoed and does not reach the terminal's scrollback.
    A question with no default is one where skipping is the default, which is
    every secret: nothing typed writes nothing.
    """
    stream = out if out is not None else sys.stdout
    answers: dict[str, str | None] = {}
    for question in questions:
        print(question["text"], file=stream)
        for choice in question["choices"]:
            note = f" - {choice['note']}" if choice.get("note") else ""
            print(f"  {choice['value']}{note}", file=stream)
        answers[question["id"]] = _answer_to(question, stream=stream, read=read)
    return answers


def _answered_anything(args: argparse.Namespace) -> bool:
    """Did the command line carry an answer? A run that did not is a sitting,
    and a sitting with nobody at the terminal asks nothing at all."""
    return bool(
        args.provider or args.tier or args.diarize is not None or args.fetch_models or args.only
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scribe.setup", description=__doc__.split("\n\n")[0])
    parser.add_argument("--plan", action="store_true", help="print what was found and what is open, as JSON")
    parser.add_argument(
        "--unasked-only",
        action="store_true",
        help="with --plan: leave out the questions the last sitting already put",
    )
    parser.add_argument("--apply-stdin", action="store_true", help="read one JSON document of answers from stdin")
    parser.add_argument(
        "--prove",
        action="store_true",
        help="measure this machine and print one report; writes nothing to the library",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=(f"with --prove, and when adopting a library: the port a running MyScribe would "
              f"answer on (default {DEFAULT_PORT})"),
    )
    parser.add_argument(
        "--library",
        action="append",
        default=[],
        type=Path,
        help="with --plan: a folder somebody named, looked at read-only and listed as a library",
    )
    parser.add_argument("--status", action="store_true", help="print what setup would ask about, as JSON")
    # Recognised so that the refusal can be a sentence rather than an argparse
    # usage error; never read. The ADR-015 tripwire flags this line and the
    # refusal by their shape, which its Enforcement paragraph expects.
    parser.add_argument("--hf-token", default="", help="refused: a token belongs in HF_TOKEN or in --apply-stdin")
    parser.add_argument(
        "--provider",
        default="",
        choices=("", LATER, *sorted(ai_ui.llm.PROVIDERS)),
        help="who answers questions about a transcript",
    )
    parser.add_argument("--tier", default="", choices=("", *TIERS), help="which transcription model")
    parser.add_argument("--diarize", dest="diarize", action="store_true", default=None)
    parser.add_argument("--no-diarize", dest="diarize", action="store_false")
    parser.add_argument("--fetch-models", action="store_true", help="download the weights now")
    parser.add_argument("--only", action="append", default=[], help="one model repo (repeatable)")
    args = parser.parse_args(argv)

    # Before anything else, and before `.env` is even read: a refused flag must
    # leave the machine untouched, and the value is never repeated back.
    if args.hf_token:
        print(REFUSES_THE_FLAG, file=sys.stderr)
        return 2

    env.load_dotenv()

    # A plan writes nothing: not the directories, not a migration, not a
    # journal-mode pragma. Everything below this branch may write, so the
    # branch is above the line where that starts.
    if args.plan:
        with read_only(paths.DB_PATH) as conn:
            print(json.dumps(plan(conn, unasked_only=args.unasked_only, named=args.library), indent=2))
        return 0

    # A proof writes nothing either, so it branches here for the same reason -
    # everything below `ensure_dirs` may write, and a directory that merely
    # appeared already fails criterion 2 of TASK-089.13.
    if args.prove:
        with read_only(paths.DB_PATH) as conn:
            report = prove(conn, port=args.port, on_start=_announce if _watched() else None)
        print(doctor.render(report))
        return doctor.exit_code(report)

    if args.status:
        paths.adopt_legacy_db()
        paths.ensure_dirs()
        conn = db.connect(paths.DB_PATH)
        try:
            db.migrate(conn)
            print(json.dumps(needed(conn), indent=2))
            return 0
        finally:
            conn.close()

    # The answers are read before anything is written (TASK-089.19): a
    # document that adopts another library must not first create and migrate
    # an empty one here, and one that is refused leaves the machine as it was.
    if args.apply_stdin:
        try:
            document = json.loads(sys.stdin.read() or "{}")
        except ValueError as exc:
            print(f"the answers were not one JSON document: {exc}", file=sys.stderr)
            return 1
        document = document if isinstance(document, dict) else {}
        claimed = document.get("contract")
        if claimed is not None and claimed != CONTRACT:
            # A number that disagrees is a mismatched install, not an old
            # client (ADR-015): launcher and app ship in one payload. The
            # number the document claims is not repeated back - it is a
            # value out of somebody else's file.
            print(
                f"these answers were written for another contract; this engine "
                f"speaks {CONTRACT} and nothing was applied.",
                file=sys.stderr,
            )
            return 2
        answers = from_document(document)
    elif _answered_anything(args):
        answers = Answers(
            provider=args.provider,
            tier=args.tier,
            diarize=args.diarize,
            fetch_models=args.fetch_models,
            wanted=args.only,
        )
    else:
        # A plan writes nothing, so the sitting is drawn from the read-only
        # open; the apply below is what creates and migrates.
        with read_only(paths.DB_PATH) as looked:
            answers = _sitting(looked)
        if answers is None:
            return 0  # nobody was asked, so there is nothing to apply

    # 2. a library chosen or named (TASK-089.19): nothing else is written.
    if answers.library and answers.library != NEW_LIBRARY:
        return _library_door(answers, port=args.port, others=_answers_besides_library(answers))

    # A `scribe.db` from before the 2026-09-06 rename is adopted before this
    # door opens the database by name: `db.connect` adopts only when it is
    # given no path, and this one is given one, so a first `python -m
    # scribe.setup` after an upgrade created an empty `myscribe.db` beside the
    # old library and orphaned it for good (reproduced in TASK-089.19's notes).
    paths.adopt_legacy_db()
    paths.ensure_dirs()
    conn = db.connect(paths.DB_PATH)
    try:
        db.migrate(conn)
        progress = Progress()
        try:
            report = apply(
                answers, conn, on_progress=progress, on_event=progress.installer, check=verify,
                announce=lambda line: print(line),
            )
        except models.ModelError as exc:
            print(f"\n{exc}")
            return models.EXIT_CODES.get(exc.reason, 1)
        except ValueError as exc:
            print(str(exc))
            return 1
        print("\nsaved: " + (", ".join(report["wrote"]) or "nothing"))
        if report["downloaded"]:
            print("downloaded: " + ", ".join(report["downloaded"]))
        for note in report["notes"]:
            print(note)
        if report["reopen"]:
            print("still open: " + ", ".join(report["reopen"]))
        return 0
    finally:
        conn.close()


def _answers_besides_library(answers: Answers) -> bool:
    """Did the document answer anything but the library question? Those
    answers wait: they belong to the library that ends up in use."""
    bare = Answers(library=answers.library, library_folder=answers.library_folder, skipped=answers.skipped)
    return asdict(answers) != asdict(bare)


def _sitting(conn: sqlite3.Connection) -> Answers | None:
    """A run that carried no answers: ask, or say what would have been asked.

    Returns what was answered, for `main` to apply, or None when nothing was
    asked. It hands the answers back rather than applying them itself because
    `main` is the one place that turns a failure into a sentence and an exit
    code: applying them here left the likeliest first run of all - Enter
    through the questions, which accepts the download, with no token for the
    gated weights - ending in a traceback and exit 1 where every other door
    says what happened and exits 3.

    With nobody at the terminal nothing is asked and no stamp is written.
    Stamping there is the failure that costs the most - the launcher never asks
    again, and the questions were never put.
    """
    document = plan(conn)
    if not at_a_terminal():
        print(render(document))
        print("\nnothing was asked: this is not a terminal. Answer with "
              "--apply-stdin, or run this from a terminal.")
        return None
    try:
        given = ask(document["questions"])
        # TASK-089.19: a folder somebody named is looked at, and the library
        # question is asked again with it in the list - so its numbers are on
        # the screen before the yes (criterion 7). Bounded like a typing
        # mistake is.
        named: list[Path] = []
        for _ in range(ASKS_AGAIN):
            if given.get("library") != NAMED_LIBRARY:
                break
            folder = str(given.get("library_folder") or "").strip().strip('"')
            refused = _refuse_named(folder)
            if not refused and libraries.inspect(Path(folder)) is None:
                refused = f"There is no MyScribe library in {folder}."
            if refused:
                print(f"  {refused}")
            else:
                named.append(Path(folder))
            again = plan(conn, named=named)
            given.update(ask([q for q in again["questions"] if q["id"] in ("library", "library_folder")]))
    except EOFError:
        # A terminal that turned out to have nobody at it after all. Nothing is
        # written and no stamp: the questions were not put.
        print("\nnothing was asked: there was nobody to answer.")
        return None
    return from_document({"contract": CONTRACT, "answers": given})


if __name__ == "__main__":
    # Here and not in main(): this module imported scribe.paths before `.env`
    # was read, so the directory the file names has to be caught up with
    # (paths.refresh) - and only a command may do that, never a caller of
    # main() who has pointed paths somewhere of their own.
    env.bootstrap()
    paths.refresh()
    raise SystemExit(main())
