"""Is there an Ollama on this machine, and can it answer a question?

Robert's rule for the installer is one sentence - "Als Ollama er al is, dan
niets doen" - and until this module nothing could tell the two cases apart.
The only probe was `OllamaProvider.available()`, to which a refused connection
reads "Ollama is not running": the same answer for a machine that has never
had Ollama and for one where somebody stopped it on purpose to free VRAM. An
offer to install, built on that, would land on the second machine.

**Detection only.** Nothing here installs, downloads, pulls, starts, stops or
configures anything, and nothing here opens the database. The offer is
TASK-089.18. An Ollama that is present is left alone in every state (ADR-017),
so the one thing this module owes the rest of the app is an honest answer
about which state that is.

Four signals, and *absent* needs all four to agree (ADR-017's Must):

1. no `ollama` on the search path,
2. none at the locations Ollama's own installers use,
3. no answer at 127.0.0.1:11434,
4. no `OLLAMA_*` variable in the process, in `.env` or in the registry.

Any doubt is present. The asymmetry is deliberate and it is Robert's: a wrong
"present" costs one typed command, a wrong "absent" overwrites somebody's
install.

The third signal is only trustworthy because of TASK-089.05. httpx reads
HTTP(S)_PROXY when a client is built, and with those set to a closed port a
running Ollama read as "not running" for its whole 2 s budget - which is
precisely the reading an offer to install would be gated on. Every request
this module makes goes through `OllamaProvider`, whose client is built with
`trust_env=False`; there is no second HTTP client here, and a hand-rolled one
would bring that defect straight back.

The fourth is why this machine can never read absent, and that is correct: it
has `OLLAMA_MODELS` and three siblings set in HKCU. Names and sources are
carried, never values - `OLLAMA_MODELS` is only a folder, but a structure that
can hold a value ends up holding one that matters (`credentials`, the same
manner).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from scribe import credentials
from scribe.llm import base, ollama

BINARY = "ollama"

VARIABLE_PREFIX = "OLLAMA_"
"""What makes a variable Ollama's. A prefix and not a list: `OLLAMA_HOST`,
`OLLAMA_MODELS`, `OLLAMA_KEEP_ALIVE` and `OLLAMA_INSTALL_DIR` are four of
roughly thirty names the daemon reads, and a machine with any of them has an
Ollama somebody configured. `OLLAMA_INSTALL_DIR` is the one that also covers
the gap in the location table below: it is how `install.ps1:49` lets somebody
install elsewhere, and a table cannot know where."""

ABSENT = "absent"
INSTALLED_NOT_RUNNING = "installed_not_running"
RUNNING_NO_CHAT_MODEL = "running_no_chat_model"
READY = "ready"
UNKNOWN = "unknown"
"""The daemon answered but would not say what its models can do. Never READY
and never ABSENT: it counts as present wherever the state is used, which is
ADR-017's "cannot be read" row."""


@dataclass(frozen=True)
class State:
    """What is known about Ollama on this machine, and by construction not the
    value of any variable.

    These fields are printed by a doctor line and will be rendered by a setup
    sitting (ADR-015), so there is nothing in them that would hurt anywhere -
    the same rule `credentials.Found` keeps, for the same reason.
    """

    state: str
    binary: str = ""
    version: str = ""
    chat_models: tuple[str, ...] = ()
    variables: tuple[credentials.Source, ...] = ()

    @property
    def present(self) -> bool:
        """True for everything but absent, `unknown` included.

        The one question the install offer may ask (ADR-017): it is offered
        only when this is False.
        """
        return self.state != ABSENT


def install_locations(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Where Ollama's own installers put the binary, on this platform.

    Read out of Ollama's install scripts on 2026-09-21; both were fetched again
    that day and are byte-identical to the copies
    `docs/superpowers/specs/2026-09-20-installer-evidence/ollama-install-scripts.txt`
    records, so its line numbers still hold.

    * Windows, `install.ps1:115-118`: `%LOCALAPPDATA%\\Programs\\Ollama`.
      Verified twice - `shutil.which` resolves exactly that folder on this
      machine.
    * macOS, `install.sh:66-83`: `/Applications/Ollama.app`, the binary inside
      it, and a symlink at `/usr/local/bin/ollama`. From the script only;
      nobody here has a Mac, so these are unverified until TASK-089's bundled
      macOS sitting (G9).
    * Linux, `install.sh:159-175`: whichever of `/usr/local/bin`, `/usr/bin`,
      `/bin` is on PATH gets the symlink, so all three are listed. From the
      script only; Robert's WSL is what would verify them.

    Not listed, because no source was read for any of them: Homebrew, a Snap, a
    Flatpak, a distribution package, a hand-built copy. A path nobody can point
    at a line for is a guess, and a guess here reads as an Ollama that is not
    there. All of them are still caught by the other three signals - which is
    the whole reason absent needs four.

    `%LOCALAPPDATA%` comes from the environment that was asked about, so a test
    that hands over a fake one is answered about that machine and not this one.
    """
    found = os.environ if environ is None else environ
    if sys.platform == "win32":
        local = (found.get("LOCALAPPDATA") or "").strip()
        return (Path(local) / "Programs" / "Ollama" / "ollama.exe",) if local else ()
    if sys.platform == "darwin":
        return (
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
            Path("/usr/local/bin/ollama"),
        )
    return (Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"), Path("/bin/ollama"))


def variables(environ: Mapping[str, str] | None = None) -> tuple[credentials.Source, ...]:
    """Every place an `OLLAMA_*` variable is configured: the name and where it
    was found, never what is in it.

    The three places a variable can live, in the order `credentials._sources`
    walks them, and for its reason: the process environment, the `.env` file
    and the Windows registry. A name in two of them is two rows, because that
    is a machine saying the same thing twice and a reader should see it.

    A blank value in the environment or in the file configures nothing and is
    not a row. The registry is not filtered that way, because this never reads
    a registry value at all - the enumerator hands back names.
    """
    found = os.environ if environ is None else environ

    rows = [
        credentials.Source(credentials.ENVIRONMENT, name)
        for name in sorted(found)
        if name.upper().startswith(VARIABLE_PREFIX) and (found.get(name) or "").strip()
    ]

    # Through the module, never as a default argument: tests/conftest.py stubs
    # both of these and a default binds at import, before the stub exists.
    file_values, file_path = credentials.dotenv_values()
    rows += [
        credentials.Source(credentials.DOTENV, name, str(file_path))
        for name in sorted(file_values)
        if name.upper().startswith(VARIABLE_PREFIX) and (file_values[name] or "").strip()
    ]
    rows += [
        credentials.Source(credentials.REGISTRY, name, hive)
        for hive, name in credentials.registry_names(VARIABLE_PREFIX)
    ]
    return tuple(rows)


def state(
    *,
    host: str = ollama.DEFAULT_HOST,
    locations: Sequence[Path] | None = None,
    environ: Mapping[str, str] | None = None,
) -> State:
    """Which of the five states this machine is in.

    Nothing is started, stopped, installed or written, and no database is
    opened: this takes no connection precisely so that a run of it on a real
    machine is trivially read-only (criterion 7).

    `/api/tags` alone decides whether Ollama is answering, and the version is
    decoration asked for afterwards. Two endpoints deciding one question would
    leave a daemon that answers one and not the other in two states at once -
    and `/api/tags` is the call whose payload the rest of the answer is built
    from anyway, so it is the one that has to succeed.

    `locations` and `environ` are seams a test injects; `host` is one too, and
    it is what lets tests/test_proxy.py point a real socket at a real server
    and check that every request still arrives on 127.0.0.1.
    """
    found_env = os.environ if environ is None else environ
    known = install_locations(found_env) if locations is None else locations

    # `path=` from the same environment the rest of this asked about, so one
    # seam answers for one machine: `shutil.which` with no path reads
    # `os.environ` whatever `environ` said, and a test that handed over an
    # empty machine would still be answered by this one's PATH.
    binary = shutil.which(BINARY, path=found_env.get("PATH", "")) or ""
    if not binary:
        binary = next((str(path) for path in known if Path(path).exists()), "")
    found_vars = variables(found_env)

    provider = ollama.OllamaProvider(host=host)
    try:
        try:
            chat = provider.chat_models()
            answered = True
        except base.NothingAnswered:
            chat, answered = None, False
        except base.LlmError:
            # Something on that port answered, just not with anything this can
            # read - an HTTP 500 is an answer. Present, and never ready:
            # ADR-017's "cannot be read". Only the silence above is absence.
            chat, answered = None, True
        version = provider.version() if answered else ""
    finally:
        provider.close()

    if not answered:
        kind = INSTALLED_NOT_RUNNING if (binary or found_vars) else ABSENT
    elif chat is None:
        kind = UNKNOWN
    elif chat:
        kind = READY
    else:
        kind = RUNNING_NO_CHAT_MODEL

    return State(
        state=kind,
        binary=binary,
        version=version,
        chat_models=tuple(chat or ()),
        variables=found_vars,
    )


def describe(found: State) -> str:
    """One line saying where that state leaves the user.

    Here rather than in the doctor because the setup sitting will want the same
    sentence (ADR-015: the engine carries the state, a front-end renders it),
    and two copies of a sentence drift. It says what is true and never what
    MyScribe would do about it - the offer is TASK-089.18's, and in three of
    these states there is no offer at all.
    """
    where = f" at {found.binary}" if found.binary else ""
    version = f" {found.version}" if found.version else ""

    if found.state == ABSENT:
        return "not installed on this machine"
    if found.state == INSTALLED_NOT_RUNNING:
        seen = where or (f" configured by {found.variables[0].name}" if found.variables else "")
        return f"installed{seen} but not answering; start it if you want local AI"
    if found.state == RUNNING_NO_CHAT_MODEL:
        return (
            f"Ollama{version} is running but has no chat model pulled: "
            f"run `ollama pull {ollama.OllamaProvider.default_model}`"
        )
    if found.state == READY:
        # `state()` never builds a ready State with no names, but this takes
        # any State - a setup front-end holds one it was handed (ADR-015) - and
        # a sentence that ends in "running with " would be this line's bug, not
        # its caller's.
        names = ", ".join(found.chat_models)
        return f"Ollama{version} is running with {names}" if names else f"Ollama{version} is running"
    return f"Ollama{version} is answering but will not say what its models can do"
