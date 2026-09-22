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
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import httpx2

from scribe import accel, credentials, db, env, models, ollama_setup, paths
from scribe.stages import diarize
from scribe.web import ai_ui, transcribe_dialog

CONTRACT = 2
"""The version of the document `--plan` prints and `--apply-stdin` reads.

Launcher and app ship in one payload (ADR-015), so a front-end that disagrees
is a mismatched install rather than an old client, and the number is here as a
constant rather than in a file of its own: where the payload keeps it is
TASK-089.11's to settle with the stamp.

A document that claims another number is refused with the usage code and
nothing of it is applied; one that claims no number at all is taken at the word
of the engine it is talking to. The *stamp*'s version gate - a start that
reopens a sitting when the stamp is older than the payload - is TASK-089.11's.
"""

STAMP = "setup.json"
"""Written into the data directory when a sitting ends, so the launcher asks
once rather than every start. It lists what was answered and what was skipped,
which is also what makes `--status` able to say what was chosen and when. Its
format, its gate and its migration are TASK-089.11's."""

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

CATALOGUE_TODAY = "today's catalogue, from scribe/models.json"

UNPINNED_HERE = (
    "the Whisper weights this platform loads are not pinned yet: they are "
    "downloaded inside the first transcription, with no progress shown (TASK-089.16)"
)

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
    skipped: list[str] = field(default_factory=list)


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
    holding the library. It can create a `-shm` where one is missing - a `.db`
    and a `-wal` without one is a half-copied or restored library rather than
    anything MyScribe leaves behind - and reading what the app has written is
    worth that.

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
    return rows


def _ollama(state: ollama_setup.State) -> dict:
    """The Ollama state, as data. `variables` are names and places, never a
    value: `credentials.Source` is built that way."""
    return {
        "state": state.state,
        "present": state.present,
        "binary": state.binary,
        "version": state.version,
        "chat_models": list(state.chat_models),
        "variables": [str(source) for source in state.variables],
        "note": ollama_setup.describe(state),
    }


NOT_MLX = "not-mlx"
"""What `transcriber()` answers where the MLX path is impossible. It is not a
backend name: which of cuda or cpu such a machine uses is the doctor's answer,
and asking costs a torch import."""


def transcriber() -> str:
    """Which transcriber this platform would load - asked so that a plan does
    not import torch to find out.

    `accel.transcription_backend()` asks CUDA first, and `cuda_available()`
    imports torch: measured here on 2026-09-22 at 4.9 s in a cold process,
    against 51 ms for all the rest of a plan. That is a heavy price for a
    question asked before anybody has typed anything, and `scribe.accel`'s own
    docstring keeps torch out of a process that only wants an answer (ADR-001);
    the doctor's `accel` line is a GPU check on purpose.

    mlx-whisper is the only platform-bound loader in today's catalogue and it
    exists on Apple Silicon alone (`accel.is_apple_silicon`), where there is no
    CUDA - so off that platform the backend is never `mlx`, which is all a plan
    has to know. On it, the real probe is asked and answers as it always did.
    """
    return accel.transcription_backend() if accel.mlx_available() else NOT_MLX


def loads_here(repo: str, backend: str) -> bool:
    """Would this platform's transcriber load these weights?

    The MLX conversion is loaded by mlx-whisper on Apple Silicon and by nothing
    else; Windows and Linux load Whisper through faster-whisper from the hub
    cache, whose weights this catalogue does not pin yet (TASK-089.16). The
    diarization pipeline is PyTorch and loads everywhere.
    """
    return backend == "mlx" if repo.startswith("mlx-community/") else True


def downloads() -> dict:
    """What this platform would download, and what it would not.

    Until TASK-089.16 re-pins the catalogue per platform this is today's
    catalogue and says so. An entry that does not load here carries no byte
    count at all rather than a number in a column headed "download": its size
    is real, but it is not a download this machine would make, and a number
    shown as one is how "1.6 GB" ended up in a dialog whatever was picked.
    """
    backend = transcriber()
    entries, total = [], 0
    for row in models.status():
        here_too = loads_here(row["repo"], backend)
        if here_too and not row["here"]:
            total += row["bytes"]
        entries.append(
            {
                "repo": row["repo"],
                "here": row["here"],
                "gated": row["gated"],
                "bytes": row["bytes"] if here_too else None,
                "loads_here": here_too,
                "note": "" if here_too else NOT_LOADED_HERE,
            }
        )
    return {
        "catalogue": CATALOGUE_TODAY,
        "backend": backend,
        "note": "" if backend == "mlx" else UNPINNED_HERE,
        "entries": entries,
        "total_bytes": total,
    }


def _questions(conn: sqlite3.Connection | None, state: ollama_setup.State, offer: dict) -> list[Question]:
    """The questions that are open on this machine, in the order they are asked.

    One predicate per question and no branch anywhere else: a question that was
    answered, or whose answer was found, is simply not in the list, which is
    what "detect before asking" means for a front-end that cannot decide.

    The numbers in the comments are the rows of the design spec's table
    (section 1); the questions this engine does not own - where everything
    goes, adopting a library, the watch folder, start at login - belong to
    TASK-089.14, .19, .20 and .21 and are not built here.
    """
    open_questions: list[Question] = []

    # 4. The Hugging Face token, when nothing on this machine has one and there
    #    is no local pipeline to fall back on.
    has_pipeline = diarize.local_weights_dir().joinpath("config.yaml").exists()
    if not credentials.resolve(conn, credentials.HUGGINGFACE).found and not has_pipeline:
        open_questions.append(
            Question(
                id="hf_token",
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
                id=f"llm_key_{name}",
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

    # 9. Which of an existing Ollama's models to use. Never an offer to pull
    #    one, and never for an Ollama this install put there (TASK-089.18).
    model_question = _ollama_model_question(conn, state, stored_provider)
    if model_question is not None:
        open_questions.append(model_question)

    # 10. Transcription quality, on a first sitting only.
    if not done():
        current_tier = _row(conn, transcribe_dialog.SETTING_TIER) or "turbo"
        open_questions.append(
            Question(
                id="default_tier",
                kind="choice",
                text="Transcription quality.",
                choices=[
                    {"value": "turbo", "label": "Turbo", "note": "the default, and the measured fast path"},
                    {"value": "max", "label": "Maximum", "note": "slower, and several times slower without a GPU - python -m scribe.doctor says which this machine has"},
                ],
                current=current_tier,
                default=current_tier,
                shown_if=None,
                if_skipped="Nothing is written; the stored default stays as it is.",
                answer_later="the transcribe dialog, or Settings > Transcription",
            )
        )

    # 11. Download the weights now, while somebody is watching.
    if offer["total_bytes"] > 0:
        open_questions.append(
            Question(
                id="fetch_models",
                kind="yes-no",
                text=f"Download the speech weights now ({models.human(offer['total_bytes'])})?",
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


def plan(conn: sqlite3.Connection | None, *, unasked_only: bool = False) -> dict:
    """What this machine has, and what is still open - one JSON-able document.

    Nothing here writes, downloads or checks a credential against its service:
    a plan is what a front-end draws before anybody has typed, so it reads the
    settings rows, the environment, `.env`, the registry, the login file, the
    catalogue and Ollama's own two reads (TASK-089.06), and stops there.

    `unasked_only` drops the questions a saved sitting already put - what a
    start asks for, against the `--setup` that asks everything. Which ids those
    are is read from the stamp, whose format is TASK-089.11's.
    """
    state = ollama_setup.state()
    offer = downloads()
    open_questions = _questions(conn, state, offer)
    if unasked_only:
        was_put = set(stamped().get("answered", []) or []) | set(stamped().get("skipped", []) or [])
        open_questions = [q for q in open_questions if q.id not in was_put]
    return {
        "contract": CONTRACT,
        "found": found_table(conn),
        "ollama": _ollama(state),
        "downloads": offer,
        "questions": [asdict(question) for question in open_questions],
    }


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


def apply(
    answers: Answers,
    conn: sqlite3.Connection,
    *,
    env_file: Path | None = None,
    on_progress=None,
    check: Callable[[str, str], Verdict] | None = None,
    announce: Callable[[str], None] | None = None,
) -> dict:
    """Write the answers down and do what they ask for. Returns a report.

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

    if answers.fetch_models:
        answered.append("fetch_models")
        try:
            report["downloaded"] = models.ensure(
                answers.wanted or None, token=diarize.hf_token(conn), on_progress=on_progress
            )
        except models.ModelError:
            # The sitting still ended, so it is still stamped: the questions
            # were put and answered, and a download that failed is a thing to
            # try again, not a reason to ask everything a second time at the
            # next start. The caller still gets the error and its exit code.
            _write_stamp(answers, report, answered)
            raise

    _write_stamp(answers, report, answered)
    return report


def _write_stamp(answers: Answers, report: dict, answered: list[str]) -> None:
    """The sitting ended, so it is recorded - a failed or skipped download
    included. What it lists is what was answered and what was skipped; the
    format, the gate and the migration are TASK-089.11's."""
    stamp_path().parent.mkdir(parents=True, exist_ok=True)
    stamp_path().write_text(
        json.dumps(
            {
                "contract": CONTRACT,
                "answered": answered,
                "skipped": list(answers.skipped),
                "provider": answers.provider,
                "tier": answers.tier,
                "hf_token": bool(answers.hf_token.strip()),
                "fetched": report["downloaded"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


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
        skipped=skipped,
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
    parser.add_argument("--apply-stdin", action="store_true", help="read one JSON document of answers from stdin")
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
            print(json.dumps(plan(conn), indent=2))
        return 0

    paths.ensure_dirs()
    conn = db.connect(paths.DB_PATH)
    try:
        db.migrate(conn)
        if args.status:
            print(json.dumps(needed(conn), indent=2))
            return 0

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
            answers = _sitting(conn)
            if answers is None:
                return 0  # nobody was asked, so there is nothing to apply

        try:
            report = apply(
                answers, conn, on_progress=Progress(), check=verify, announce=lambda line: print(line)
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
        for note in report["notes"]:
            print(note)
        if report["reopen"]:
            print("still open: " + ", ".join(report["reopen"]))
        return 0
    finally:
        conn.close()


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
