"""Where this machine keeps its credentials, asked once and answered the same
way everywhere (TASK-089.04).

Requirement 7 of the installer is "detect before asking", and until this
module the detection disagreed with itself. The Hugging Face token had four
lookups. `diarize.hf_token` read the settings row and two environment
variables, `models.default_token` read the environment only, the doctor
called `hf_token(None)` and so never saw the row at all, and the settings
page was a fourth. A user who saved the token where the error told them to
then ran the command the doctor printed and was told there was no token.

So: one table of credentials, one order, and one answer. Three places were
missing entirely - the Windows registry (`base.windows_env`'s reason applies
to every credential, not only to the two LLM keys), the hub's own
`HUGGING_FACE_HUB_TOKEN`, and the login file `huggingface-cli login` writes -
and `.env` could not be told apart from the environment, so a stale variable
outranking a token just typed was invisible.

Two manners, because these are secrets.

* **A value is never shown.** `resolve()` is the only thing that carries one,
  behind a repr that does not. `find_all()` holds no value at all, so its
  rows are safe in a print, a log line and a JSON body by construction rather
  than by somebody remembering.
* **A found credential is used where it is, and never copied.** A token in
  Hugging Face's login file can be rotated by `huggingface-cli`; a copy
  written into `.env` would go stale and then outrank the real one, and
  `.env` is the less protected of the two files.

This module imports nothing from `scribe.llm` or `scribe.stages`: both of
them import it, and `huggingface_hub` is imported inside the login-file
reader so that asking about a token stays cheap (ADR-001).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

from scribe import db, env, paths

# The kinds of place a credential can live in. `Source.kind` is one of these;
# the strings the user sees are built from the whole Source, never from this.
SETTINGS = "settings"
ENVIRONMENT = "environment"
DOTENV = "dotenv"
REGISTRY = "registry"
LOGIN_FILE = "login-file"

HKCU = "HKEY_CURRENT_USER"
HKLM = "HKEY_LOCAL_MACHINE"


@dataclass(frozen=True)
class Source:
    """Where a value came from: the kind, the variable name where there is
    one, and the path or the hive where there is one.

    Structured rather than a sentence, because two consumers want two
    different sentences out of it: the settings page has printed
    `HF_TOKEN` and `OPENROUTER_TOKEN (Windows registry)` since before the
    hive was known (`short_source`), and the found table wants everything
    it knows (`str`).
    """

    kind: str
    name: str = ""
    where: str = ""

    def __str__(self) -> str:
        if self.kind == SETTINGS:
            return SETTINGS
        if self.kind == ENVIRONMENT:
            return f"{self.name} (environment)"
        if self.kind == DOTENV:
            return f"{self.name} ({self.where})"
        if self.kind == REGISTRY:
            hive = f", {self.where}" if self.where else ""
            return f"{self.name} (Windows registry{hive})"
        if self.kind == LOGIN_FILE:
            return f"the Hugging Face login file ({self.where})"
        return self.kind


@dataclass(frozen=True)
class Credential:
    """One credential and every name it may hide under.

    `env_vars` is the provider's own list, in the provider's order, and a
    test pins the two against each other so they cannot drift.
    `legacy_env_vars` is looked for after all of those - Hugging Face's
    `HUGGING_FACE_HUB_TOKEN` is the hub's spelling, not this app's, and a
    machine that has both should use the one this app documents.
    """

    name: str
    label: str
    setting_key: str
    env_vars: tuple[str, ...]
    legacy_env_vars: tuple[str, ...] = ()
    has_login_file: bool = False


HUGGINGFACE = Credential(
    name="huggingface",
    label="Hugging Face",
    setting_key="hf_token",
    env_vars=("HF_TOKEN", "HUGGINGFACE_TOKEN"),
    legacy_env_vars=("HUGGING_FACE_HUB_TOKEN",),
    has_login_file=True,
)
OPENROUTER = Credential(
    name="openrouter",
    label="OpenRouter",
    setting_key="llm_key_openrouter",
    env_vars=("OPENROUTER_TOKEN", "OPENROUTER_API_KEY"),
)
OPENAI = Credential(
    name="openai",
    label="OpenAI",
    setting_key="llm_key_openai",
    env_vars=("OPENAI_API_KEY",),
)

CREDENTIALS: dict[str, Credential] = {c.name: c for c in (HUGGINGFACE, OPENROUTER, OPENAI)}
"""Every credential this app looks for. The LLM ones are keyed by provider
name, which is how `base.api_key` finds the right row; a provider with no
`key_env_vars` - Ollama, which is loopback - has no entry and nothing to
find."""


# --- the three places a test cannot control -----------------------------------
#
# Each is a module-level function on purpose: tests/conftest.py stubs all
# three for every test, so this machine's registry, `.env` and login file can
# never answer one. Look them up through the module at the moment they are
# needed, never as a default argument, or the stub arrives too late.


def registry_hits(name: str) -> tuple[tuple[str, str], ...]:
    """Every `(hive, value)` the Windows registry holds for `name`, user hive
    first. Empty everywhere but Windows, and it never raises.

    Windows sets `setx`/System-Properties variables in the registry and only
    broadcasts them to *new* processes. A shell (or an app launched from one)
    that started before the variable was set has no such variable in
    `os.environ` and never will - which is exactly the state this machine is
    in for `OPENROUTER_TOKEN` and `HF_TOKEN`, both set machine-wide under
    HKLM. Reading the registry is what makes "the key is set on this machine"
    true from a process that predates it.

    Both hives are reported, not just the first, because the found table has
    to be able to say a credential is defined twice with two different values.
    """
    if sys.platform != "win32":
        return ()
    import winreg

    found: list[tuple[str, str]] = []
    for hive, handle_id, path in (
        (HKCU, winreg.HKEY_CURRENT_USER, r"Environment"),
        (HKLM, winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(handle_id, path) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str):
            found.append((hive, value))
    return tuple(found)


def windows_env(name: str) -> str | None:
    """The first registry value for `name`, or None. `base` re-exports this."""
    return next((value for _hive, value in registry_hits(name) if value.strip()), None)


def dotenv_values(path: str | Path | None = None) -> tuple[dict[str, str], Path]:
    """What the `.env` file holds, and which file that was.

    Parsed and deliberately *not* applied: this is a question, and a question
    that changed `os.environ` would be answered differently the second time it
    was asked. `env.dotenv_path` names the file, so the resolver cannot report
    a different file than `load_dotenv` reads.

    A file that is not there, or will not open, is simply nothing - the same
    rule `load_dotenv` keeps.
    """
    path = env.dotenv_path(path)
    try:
        return env.parse(path.read_text(encoding="utf-8-sig")), path
    except (OSError, UnicodeDecodeError):
        return {}, path


def login_file() -> Path:
    """The file `huggingface-cli login` writes the token to.

    `huggingface_hub` is imported here and not at the top: it pulls a good
    deal in, and every command that asks "is a token set" would pay for it.
    A hub too old to name the constant, or none installed at all, answers
    with a path that does not exist.
    """
    try:
        from huggingface_hub import constants

        return Path(constants.HF_TOKEN_PATH)
    except Exception:  # noqa: BLE001 - no hub is not an answer about the token
        return Path.home() / ".cache" / "huggingface" / "token"


def _read_login_file() -> tuple[Path, str, str]:
    """`(path, token, trouble)` - the login file read in place.

    A file whose permissions or encoding are wrong is reported and never
    raised: the doctor asks this on somebody else's machine, and a lookup
    that raised would turn a file mode into a traceback.
    """
    path = login_file()
    try:
        return path, path.read_text(encoding="utf-8").strip(), ""
    except FileNotFoundError:
        return path, "", ""
    except (OSError, UnicodeDecodeError):
        return path, "", f"the Hugging Face login file ({path}) is not readable"


# --- what the resolver answers ------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """A credential and where it came from - never the value in a repr.

    The one thing here that carries a value; `find_all` carries none.
    """

    value: str | None
    source: Source | None = None

    @property
    def found(self) -> bool:
        return bool(self.value)

    def __repr__(self) -> str:  # never the value: this ends up in tracebacks
        where = str(self.source) if self.source is not None else ""
        return f"Resolved(source={where!r}, found={self.found})"

    __str__ = __repr__


@dataclass(frozen=True)
class Found:
    """What is known about one credential, and by construction not its value.

    These rows are printed by a setup sitting, put in a log and rendered on a
    page, so there is nothing in them that would hurt anywhere.
    """

    credential: str
    label: str
    found: bool
    source: str
    also_in: tuple[str, ...]
    conflict: bool
    note: str = ""


def short_source(source: Source | None) -> str:
    """The source as the settings page has always printed it: the bare
    variable name, or `<NAME> (Windows registry)`, or `settings`.

    The resolver knows more than this - which hive, which `.env` file - and
    `find_all` is where that belongs. Widening the vocabulary here would
    change what `_settings_llm.html` shows for every key and what
    `base.api_key`'s callers have been reading since TASK-062. A `.env` hit
    reads as the bare variable name, which is what it was before the file
    could be told apart from the environment.

    The login file is the one place with no variable name at all, so it says
    what it is instead. It cannot reach `base.api_key`: only Hugging Face has
    a login file and it is not an LLM provider - but it does reach the
    settings page, which would otherwise print "In effect now: •••• from
    <code></code>" for a token `huggingface-cli login` wrote.
    """
    if source is None:
        return ""
    if source.kind == SETTINGS:
        return SETTINGS
    if source.kind == REGISTRY:
        return f"{source.name} (Windows registry)"
    if source.kind == LOGIN_FILE:
        return "the Hugging Face login file"
    return source.name


def one_hive(read: Callable[[str], str | None]) -> Callable[[str], tuple[tuple[str, str], ...]]:
    """A plain name-to-value registry reader as the hive-aware one.

    `base.api_key`'s `registry=` seam is a mapping's `.get` in every test that
    uses it, and those tests are older than the hive being named. A value
    from such a reader is from no hive in particular, and reads as the plain
    "(Windows registry)".
    """

    def hits(name: str) -> tuple[tuple[str, str], ...]:
        value = read(name)
        return (("", value),) if value else ()

    return hits


def credential(which: str | Credential) -> Credential | None:
    """The table entry for a name, or None for something with nothing to find."""
    return which if isinstance(which, Credential) else CREDENTIALS.get(which)


def _setting_row(conn: sqlite3.Connection | None, key: str) -> str:
    """The `setting` row for `key`, or "".

    No connection, no `setting` table, a database from before the migration
    that made one: all of them are "no row" and none of them is an error.
    `python -m scribe.models` on a machine that has never started the app
    still has to be able to ask about the environment.
    """
    if conn is None:
        return ""
    try:
        with db.LOCK:
            row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return ""
    return "" if row is None else (row[0] or "").strip()


def _sources(
    conn: sqlite3.Connection | None,
    cred: Credential,
    *,
    environ: Mapping[str, str],
    registry: Callable[[str], tuple[tuple[str, str], ...]],
    applied: frozenset[str],
    first_only: bool = False,
) -> tuple[list[tuple[Source, str]], str]:
    """Every place that holds a non-blank value for `cred`, in the order this
    app trusts them, and whatever went wrong while looking.

    `first_only` stops at the first place that has one, which is what
    `resolve` wants and `find_all` must not have: the found table's whole
    subject is the places that agree or disagree with the winner. Stopping
    matters because the places are not equally cheap - reading the login file
    imports `huggingface_hub` (ADR-001), and `python -m scribe.models`, the
    doctor and every render of the settings page ask this question. A
    settings row answers with one SELECT and no file, registry or hub at all.

    The order is the whole point, so it is one walk and not five callers:
    the settings row the user typed in the app; then, per variable name in
    the provider's order, the process environment, the `.env` file and the
    Windows registry with the user hive before the machine hive. The
    registry is per *variable* rather than appended after all of them,
    because the fixed order is the order of the names: a leftover
    `OPENROUTER_API_KEY` in the shell must not beat the machine's real
    `OPENROUTER_TOKEN`.

    "The environment snapshotted before `.env` is applied" is reconstructed
    rather than snapshotted. Once `load_dotenv` has run, `os.environ` cannot
    tell the file from the shell, and `env.applied()` is the only record of
    which names came from the file - so a name it applied is reported as
    `.env` and never as the environment.
    """
    found: list[tuple[Source, str]] = []
    trouble = ""

    row = _setting_row(conn, cred.setting_key)
    if row:
        found.append((Source(SETTINGS), row))
        if first_only:
            return found, trouble

    file_values, file_path = dotenv_values()
    for name in cred.env_vars + cred.legacy_env_vars:
        value = (environ.get(name) or "").strip()
        from_file = _spelled(file_values, name).strip()
        from_the_file = bool(value) and name in applied
        if value and not from_the_file:
            found.append((Source(ENVIRONMENT, name), value))
        if from_file or from_the_file:
            # `from_file or value`: the file was read again just now, and if
            # it has changed since load_dotenv ran it is still the file that
            # put this value in the environment.
            found.append((Source(DOTENV, name, str(file_path)), from_file or value))
        for hive, in_registry in registry(name):
            if in_registry.strip():
                found.append((Source(REGISTRY, name, hive), in_registry.strip()))
        # Per name rather than per place: the three places a *name* can live
        # were just looked at in their fixed order, so the first of them is
        # the winner whichever of the three it was.
        if first_only and found:
            return found, trouble

    if cred.has_login_file:
        path, token, trouble = _read_login_file()
        if token:
            found.append((Source(LOGIN_FILE, where=str(path)), token))

    return found, trouble


def _spelled(values: Mapping[str, str], name: str) -> str:
    """`name`'s value in a parsed `.env`, spelled the way the environment
    would spell it.

    Windows has one variable per name whatever the case - `hf_token=` in the
    file fills HF_TOKEN there - and `env.load_dotenv` honours that, so a
    reader that did not would report a file as holding nothing while the app
    read a token out of it.
    """
    if name in values:
        return values[name]
    if os.name != "nt":
        return ""
    return next((value for key, value in values.items() if key.upper() == name.upper()), "")


def resolve(
    conn: sqlite3.Connection | None,
    which: str | Credential,
    *,
    environ: Mapping[str, str] | None = None,
    registry: Callable[[str], tuple[tuple[str, str], ...]] | None = None,
    applied: frozenset[str] | None = None,
) -> Resolved:
    """The credential and where it came from. The first non-blank place wins.

    `Resolved(None)` - never `""` - when nothing has one: an empty string is
    a token as far as Hugging Face is concerned, and it answers 401 instead
    of serving the public copy anonymously.
    """
    cred = credential(which)
    if cred is None:
        return Resolved(None)
    found, _trouble = _sources(
        conn,
        cred,
        environ=os.environ if environ is None else environ,
        registry=registry_hits if registry is None else registry,
        applied=env.applied() if applied is None else applied,
        first_only=True,
    )
    return Resolved(found[0][1], found[0][0]) if found else Resolved(None)


def find_all(
    conn: sqlite3.Connection | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    registry: Callable[[str], tuple[tuple[str, str], ...]] | None = None,
    applied: frozenset[str] | None = None,
) -> list[Found]:
    """One row per credential: what answered, what else defines it, and
    whether the two disagree.

    The disagreement is the point. This machine holds one `HF_TOKEN` in HKLM
    and a different one in `.env`, and until this table nothing said so -
    which of the two is valid was tested by nobody.
    """
    rows: list[Found] = []
    for cred in CREDENTIALS.values():
        found, trouble = _sources(
            conn,
            cred,
            environ=os.environ if environ is None else environ,
            registry=registry_hits if registry is None else registry,
            applied=env.applied() if applied is None else applied,
        )
        winner, value = found[0] if found else (None, "")
        rest = found[1:]
        rows.append(
            Found(
                credential=cred.name,
                label=cred.label,
                found=bool(found),
                source=str(winner) if winner is not None else "",
                also_in=tuple(str(source) for source, _ in rest),
                conflict=any(other != value for _, other in rest),
                note=trouble,
            )
        )
    return rows


@contextlib.contextmanager
def library_db() -> Iterator[sqlite3.Connection | None]:
    """This installation's database for a command that was handed none, or
    None when there is not one to open.

    `python -m scribe.models` and the doctor's diarization check run outside
    the app and so had no way to see a token saved in Settings - which is the
    loop this task exists to close: save it where the error says, run the
    command the doctor prints, be told there is no token.

    Every way of not having a database - no file, no directory, one another
    process is holding - is None and never an error: this is a question about
    a credential, not about the database. `paths.DB_PATH` by name rather than
    `db.connect()`'s default, which also renames a legacy `scribe.db`; a
    lookup does not move files.
    """
    conn = None
    try:
        if paths.DB_PATH.exists():
            conn = db.connect(paths.DB_PATH)
    except (sqlite3.Error, OSError):
        conn = None
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()
