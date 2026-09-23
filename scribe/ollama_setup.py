"""Is there an Ollama on this machine, and can it answer a question?

Robert's rule for the installer is one sentence - "Als Ollama er al is, dan
niets doen" - and until this module nothing could tell the two cases apart.
The only probe was `OllamaProvider.available()`, to which a refused connection
reads "Ollama is not running": the same answer for a machine that has never
had Ollama and for one where somebody stopped it on purpose to free VRAM. An
offer to install, built on that, would land on the second machine.

**Detection first, and the offer below it.** `state()` and `describe()`
install, download, pull, start, stop and configure nothing, and nothing here
opens the database. The install offer (TASK-089.18) lives in the second half
of this file, from `install_plan` down, and it is gated on one thing: the
state this half reports is `absent`. An Ollama that is present is left alone
in every state (ADR-017), so the one thing the detector owes the rest of the
app is an honest answer about which state that is.

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

import argparse
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

import httpx2

from scribe import credentials, paths
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


def install_locations(
    environ: Mapping[str, str] | None = None, platform: str | None = None
) -> tuple[Path, ...]:
    """Where Ollama's own installers put the binary, on `platform` (this one
    unless another is named).

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
    The platform is a parameter for the same reason: an install plan for
    Windows names Windows's folder wherever it is built. Until TASK-089.24 it
    read `sys.platform` here, so a Windows plan built on a Mac named
    `/Applications` - found by the first CI run on macOS, where a test then
    wrote a fake binary into the runner's real /Applications.
    """
    found = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        local = (found.get("LOCALAPPDATA") or "").strip()
        return (Path(local) / "Programs" / "Ollama" / "ollama.exe",) if local else ()
    if platform == "darwin":
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


def _binary(
    environ: Mapping[str, str],
    locations: Sequence[Path] | None = None,
    platform: str | None = None,
) -> str:
    """The `ollama` binary on this machine, or "".

    `path=` from the same environment the rest of the answer is built from, so
    one seam answers for one machine: `shutil.which` with no path reads
    `os.environ` whatever `environ` said, and a test that handed over an empty
    machine would still be answered by this one's PATH. The known locations
    come second, for an Ollama that was installed but never put on PATH.
    """
    known = install_locations(environ, platform) if locations is None else locations
    found = shutil.which(BINARY, path=environ.get("PATH", "")) or ""
    if not found:
        found = next((str(path) for path in known if Path(path).exists()), "")
    return found


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
    binary = _binary(found_env, known)
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


# --- the install offer (TASK-089.18) ------------------------------------------------
#
# Everything from here down installs, and ADR-017 is its rule: only when
# `state()` reads absent, only what was shown first, only from the pinned and
# verified artifact, and never a start, a stop or a pull into an Ollama that
# was already there. `scribe.setup` decides *whether* any of it runs, from the
# state and the answers; this half knows *how*. Every seam - `subprocess.Popen`,
# `download_client`, `verify_signer`, `set_user_variable` - is a module
# attribute looked up at call time, so a test that plants a raiser proves the
# absence of a download or a process for a whole sitting.


RELEASE_PATH = Path(__file__).with_name("ollama_release.json")
"""The pin. Under `scribe/` because that is what the payload ships
(`packaging/build_payload.py`, APP_PATHS); its owner is the release step in
`docs/RELEASING.md` and the `--check-pin` step in CI (ADR-017, M7)."""

VENDOR_PAGE = "https://ollama.com/download"
"""Where every failure of the pinned download ends: named in a sentence, and
never fetched by MyScribe. There is no second URL (ADR-017, Must Not)."""

MARKER = "ollama_setup.json"
"""Written beside the stamp, in the data directory, only after an installer
MyScribe ran has finished successfully (M1). Its one job is to let a `--setup`
sitting offer the unfinished pull of the one model that was agreed to in the
same yes (G6); `valid_marker` says when it still counts (G7)."""

MARKER_FIELDS = ("tag", "version", "binary", "model", "installed")
"""A marker missing any of these is doubt, and doubt drops it."""

DEFAULT_MODEL = ollama.OllamaProvider.default_model
BIGGER_MODEL = "gemma4:12b"

GEMMA_THRESHOLD_BYTES = 23_622_320_128
"""22 GiB, compared in bytes against what the card reports. AN ESTIMATE
modelled on one measured card (W4): Robert's nominal 16 GB card reports
17,179,344,896 bytes with torch, a hair under 16 GiB, and on it the 9B and
the 12B both failed to start beside a transcription (`scribe/llm/ollama.py`,
`default_model`). A threshold at 16 would flip on the unit, and one at 20 would
do the same one class up if a 20 GB card reports the same way - nobody has
measured one. 22 sits at no card's nominal size, so a 24 GB card passes and a
16 or 20 GB card fails whether the number is later read as GiB or as decimal
GB. `choices()` and the sitting both say it is an estimate."""

VERSION_WAIT_S = 120.0
"""How long `GET /api/version` is polled after an install. A timeout reads
"installed, not answering yet", never failure: whether the silent installer
starts the daemon is verified by nobody (criterion 8, 13)."""

POLL_S = 2.0

PULL_TIMEOUT_S = 120.0
"""Per read, between two NDJSON lines of a pull; Ollama reports progress far
more often than that while it is downloading."""

CHUNK = 1 << 20
"""One mebibyte per read of the installer: 1.57 GB in about 1,500 writes, and
never the whole file in memory (criterion 4)."""

DOWNLOADS = "downloads"
MODELS_FOLDER = "ollama-models"

SIGNER_PATTERN = re.compile(r"(^|, )O=Ollama Inc\.(,|$)")
"""install.ps1:84-87 of the pinned release, re-read on 2026-09-23: the subject
must carry `O=Ollama Inc.` between commas, "to prevent 'O=Not Ollama Inc.'
from matching". The certificate on the pinned installer itself has not been
read by anybody here; if it reads differently the check fails safe."""

SIGNER_SUBJECT = "O=Ollama Inc."

VARIABLE = "OLLAMA_MODELS"

INSTALLER_WAITS = "Ollama's installer is still running; waiting for it to finish before anything stops."


class InstallError(RuntimeError):
    """A download or a check that ended on the vendor's page. Its text is the
    whole sentence a sitting prints, proxy clause included."""


class PullError(RuntimeError):
    """A pull Ollama reported as failed, or one that ended without its final
    `success` line. Its text carries Ollama's own words."""


@dataclass(frozen=True)
class Outcome:
    """What `install` did. `ok` is "the step MyScribe owns finished" - on Linux
    that step is showing the commands, so `ok` is False there and the sentence
    is the commands. `installed` is "a marker was written": Windows only, after
    exit 0 and a binary at the known path."""

    ok: bool
    installed: bool
    sentence: str
    marker: dict | None = None
    variable: str = ""


def release(path: Path | None = None) -> dict:
    return json.loads((RELEASE_PATH if path is None else Path(path)).read_text(encoding="utf-8"))


MODEL_BYTES: dict[str, int] = {name: int(spec["bytes"]) for name, spec in release()["models"].items()}
"""The two models the offer knows, with the byte counts the reference
machine's Ollama reports for them (read only, 2026-09-20 and 2026-09-23)."""


def _platform_key(platform: str) -> str:
    """How the pin spells a platform; anything that is not Windows or macOS is
    Linux, which is what `install_locations` already assumes."""
    return platform if platform in ("win32", "darwin") else "linux"


def _ended(what: str) -> str:
    """The sentence every failed download or check ends on: what happened,
    the vendor's page, Check again, and the proxy clause the two earlier
    downloads already add (criterion 17)."""
    return f"{what}; get it from {VENDOR_PAGE}, then Check again{credentials.proxy_note()}"


def install_plan(
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    found: dict | None = None,
    into: Path | None = None,
) -> dict:
    """Data to *show* before the question: the artifact's URL, size and sha256,
    the exact argv, where it installs, and what the person is owed in words.

    `command` is the argv `install` hands `subprocess.Popen`, list for list;
    `shown` is the same command as a person reads it, or on Linux the commands
    they run themselves. Nothing runs that is not in one of the two.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    pin = release() if found is None else found
    into = (paths.DATA_DIR / DOWNLOADS) if into is None else Path(into)
    key = _platform_key(platform)
    artifact = pin["artifacts"][key]
    landed = str(into / artifact["name"])

    if key == "win32":
        known = install_locations(environ, key)
        install_dir = str(known[0].parent) if known else artifact["install_dir"]
        command = [landed, *artifact["args"]]
        # Popen turns a list into this very line on Windows, so what is shown
        # is what runs, quotes and all (criterion 3).
        shown = [subprocess.list2cmdline(command)]
    elif key == "darwin":
        install_dir = artifact["install_dir"]
        command = ["open", landed]
        shown = [shlex.join(command)]
    else:
        install_dir = artifact["install_dir"]
        command = []
        shown = [
            f"curl -fsSL -o ollama-install.sh {artifact['url']}",
            f"sha256sum ollama-install.sh   # must print {artifact['sha256']}",
            "less ollama-install.sh",
            "sh ollama-install.sh",
        ]
    return {
        "platform": key,
        "tag": pin["tag"],
        "read": pin["read"],
        "name": artifact["name"],
        "url": artifact["url"],
        "bytes": int(artifact["bytes"]),
        "sha256": artifact["sha256"],
        "artifact": landed,
        "vendor_page": pin["vendor_page"],
        "install_dir": install_dir,
        "signer": artifact.get("signer_subject", ""),
        # docs.ollama.com/windows, read 2026-09-23: "installs in your account
        # without requiring Administrator rights"; the .dmg and the Linux
        # commands are the person's own to run.
        "needs_admin": False,
        # docs.ollama.com/faq, same day: Ollama on macOS and Windows downloads
        # its updates itself. On Linux it is the person's package.
        "self_updates": key != "linux",
        "runs_here": key != "linux",
        "command": command,
        "shown": shown,
        "notes": list(artifact.get("notes", [])),
    }


# --- the model offer (W4) --------------------------------------------------------


def choices(cuda_bytes: int | None, apple_silicon: bool) -> list[dict]:
    """The models the new Ollama may get, as the choices of one question.

    `qwen3.5:4b` always, and first, because it is the default. `gemma4:12b`
    only at `GEMMA_THRESHOLD_BYTES` or more of reported CUDA memory, and never
    on Apple Silicon until somebody has measured it on a real Mac. Every note
    names its bytes and says which GB it means, because "16 GB" is 15.9995 GiB
    on the one card that was measured (W4).
    """

    def gb(size: int) -> str:
        return f"{size / 10**9:.1f} GB ({size:,} bytes; GB is 10^9 bytes here)"

    listed = [
        {
            "value": DEFAULT_MODEL,
            "label": DEFAULT_MODEL,
            "note": f"{gb(MODEL_BYTES[DEFAULT_MODEL])}, the default: the model that answered on a 16 GB card beside a transcription",
        }
    ]
    if cuda_bytes is not None and cuda_bytes >= GEMMA_THRESHOLD_BYTES and not apple_silicon:
        listed.append(
            {
                "value": BIGGER_MODEL,
                "label": BIGGER_MODEL,
                "note": (
                    f"{gb(MODEL_BYTES[BIGGER_MODEL])}; listed because this card reports {cuda_bytes:,} bytes, "
                    f"at least {GEMMA_THRESHOLD_BYTES:,} (22 GiB) - an estimate modelled on one measured 16 GB card, "
                    "not a measurement of this one"
                ),
            }
        )
    return listed


# --- where the models go (G8) ------------------------------------------------------


def default_models_dir(platform: str | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """Ollama's own model folder, from docs.ollama.com/faq as read on
    2026-09-23: `C:\\Users\\%username%\\.ollama\\models`, `~/.ollama/models`, and
    `/usr/share/ollama/.ollama/models` for the Linux service user."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    key = _platform_key(platform)
    if key == "win32":
        return Path(environ.get("USERPROFILE") or "") / ".ollama" / "models"
    if key == "darwin":
        return Path(environ.get("HOME") or "") / ".ollama" / "models"
    return Path("/usr/share/ollama/.ollama/models")


def _inside_git_tree(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def _myscribe_home(platform: str, environ: Mapping[str, str]) -> Path:
    """The per-user home the launcher uses, respelled here in the one case that
    needs it (a clone): MYSCRIBE_HOME, else the launcher's per-OS default
    (`home_dir` in packaging/launcher/myscribe_launcher.py). The pointer file is
    deliberately not read: the app may not import the launcher, and a folder
    beside the library is what somebody with a pointer already has."""
    if environ.get("MYSCRIBE_HOME"):
        return Path(environ["MYSCRIBE_HOME"])
    key = _platform_key(platform)
    if key == "win32":
        return Path(environ.get("LOCALAPPDATA") or "") / "MyScribe"
    home = Path(environ.get("HOME") or "")
    if key == "darwin":
        return home / "Library" / "Application Support" / "MyScribe"
    if environ.get("XDG_DATA_HOME"):
        return Path(environ["XDG_DATA_HOME"]) / "MyScribe"
    return home / ".local" / "share" / "MyScribe"


def models_dir_with_myscribe(
    environ: Mapping[str, str] | None = None,
    *,
    data_dir: Path | None = None,
    platform: str | None = None,
) -> Path:
    """Where the models live when the default volume is too small (row 8a):
    beside the library, which survives an uninstall (the Inno script leaves the
    home alone, M8) - unless the library sits inside a git working tree, where
    `git clean -fdx` would take it, and then under the per-user home."""
    environ = os.environ if environ is None else environ
    data_dir = paths.DATA_DIR if data_dir is None else Path(data_dir)
    platform = sys.platform if platform is None else platform
    if _inside_git_tree(data_dir):
        return _myscribe_home(platform, environ) / MODELS_FOLDER
    return data_dir / MODELS_FOLDER


def free_bytes(path: Path) -> int:
    """Free space on the volume `path` is or will be on, measured on the nearest
    folder that exists - a folder nothing has created yet is still on a disk."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free)


def room(path: Path, needed: int) -> dict:
    """Both numbers a person is shown, and the verdict."""
    free = free_bytes(path)
    return {"path": str(path), "free": free, "needed": int(needed), "enough": free >= needed}


def models_dir_sentence(platform: str, folder: Path) -> str:
    """macOS and Linux keep row 8a a sentence with the exact command: a systemd
    unit wants root and a launch agent is a second mechanism nobody has run."""
    if _platform_key(platform) == "darwin":
        return (
            f"To keep Ollama's models at {folder}, set OLLAMA_MODELS for your account before starting "
            f"Ollama: launchctl setenv OLLAMA_MODELS {folder}"
        )
    return (
        f"To keep Ollama's models at {folder}, give the service the variable: "
        f"sudo systemctl edit ollama, then under [Service] add Environment=OLLAMA_MODELS={folder}, "
        "then sudo systemctl restart ollama (docs.ollama.com/faq, read 2026-09-23)"
    )


def models_landed(folder: Path) -> bool:
    """Did a pull put anything in `folder`? Whether the daemon inherits
    OLLAMA_MODELS is unmeasured (criterion 13), so a sentence about where the
    model went is earned by blobs on disk, never assumed."""
    blobs = Path(folder) / "blobs"
    try:
        return blobs.is_dir() and any(blobs.iterdir())
    except OSError:
        return False


# --- the marker (M1, G6, G7) ------------------------------------------------------


def marker_path() -> Path:
    return paths.DATA_DIR / MARKER


def read_marker() -> dict | None:
    """The marker as written, or None for no file, an unreadable one, or one
    that is not an object. Doubt reads as no marker."""
    try:
        found = json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def write_marker(marker: dict) -> None:
    """In one piece, the way the stamp goes down: a temp file, then `os.replace`."""
    marker_path().parent.mkdir(parents=True, exist_ok=True)
    written = marker_path().with_name(MARKER + ".tmp")
    written.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.replace(written, marker_path())


def remove_marker() -> None:
    try:
        marker_path().unlink()
    except FileNotFoundError:
        pass


def _same_file(one: str, other: str) -> bool:
    return os.path.normcase(os.path.realpath(one)) == os.path.normcase(os.path.realpath(other))


def valid_marker(found: State, marker: dict | None) -> bool:
    """Does the marker still count? Only while the same version stands at the
    same path, and any doubt drops it (G7).

    Lapses, one test each: a missing field or an unreadable file; no binary at
    the recorded path; a binary found elsewhere; a version that differs, which
    is what Ollama's self-update does; Ollama seen ready, after which that
    Ollama is the user's; and absent, where nothing stands at all. A stopped
    Ollama's version cannot be read and is not doubted: the pull it unlocks is
    offered only once the daemon answers, and the version is checked then.
    """
    if not isinstance(marker, dict) or any(field not in marker for field in MARKER_FIELDS):
        return False
    if found.state in (READY, ABSENT):
        return False
    recorded = str(marker["binary"])
    if not recorded or not Path(recorded).exists():
        return False
    if found.binary and not _same_file(found.binary, recorded):
        return False
    if found.version and found.version != str(marker["version"]):
        return False
    return True


# --- the download (criterion 4) --------------------------------------------------


def download_client() -> httpx2.Client:
    """The client the artifact comes over. `trust_env` stays on: this is a
    remote host, and somebody behind a corporate proxy needs it - the loopback
    bypass is for the daemon only (TASK-089.05). Redirects are followed because
    GitHub's release URLs answer with one."""
    return httpx2.Client(timeout=httpx2.Timeout(30.0, read=120.0), follow_redirects=True)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    into: Path,
    name: str,
    *,
    expected_bytes: int,
    expected_sha256: str,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> Path:
    """Fetch the pinned artifact to `into/name`, streaming, with resume.

    It goes to `name.part` in chunks and is hashed as it arrives, so nothing
    the size of the installer is ever in memory. A part that is already there
    is hashed first and asked for from where it stopped (`Range`), so an
    interrupted download is finished with the same button (G6); a server that
    answers 200 to that starts the file over rather than appending. It lands
    under `name` only when the byte count and the sha256 both match the pin;
    a 404, another status or a mismatch raises `InstallError` with the vendor
    page, and a mismatch removes the part, because resuming it would only
    reproduce the mismatch. A file that already landed is checked, not fetched.
    """
    into = Path(into)
    final = into / name
    part = into / f"{name}.part"

    if final.exists() and final.stat().st_size == expected_bytes and _sha256_of(final) == expected_sha256:
        return final

    have = part.stat().st_size if part.exists() else 0
    if have > expected_bytes:
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        with open(part, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)

    def landed_or_refused(count: int, found: str) -> Path:
        if count != expected_bytes:
            part.unlink(missing_ok=True)
            raise InstallError(_ended(
                f"{name} arrived as {count:,} bytes where the pin says {expected_bytes:,}"))
        if found != expected_sha256:
            part.unlink(missing_ok=True)
            raise InstallError(_ended(f"{name} arrived with sha256 {found}, not the pinned {expected_sha256}"))
        os.replace(part, final)
        return final

    if have == expected_bytes:
        return landed_or_refused(have, digest.hexdigest())

    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        with download_client() as client:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 404:
                    raise InstallError(_ended(f"{url} answered 404: the pinned release is gone"))
                if response.status_code == 206 and have:
                    mode = "ab"
                elif response.status_code == 200:
                    mode, have, digest = "wb", 0, hashlib.sha256()
                else:
                    raise InstallError(_ended(f"{url} answered HTTP {response.status_code}"))
                into.mkdir(parents=True, exist_ok=True)
                # No chunk size on purpose: `iter_bytes(CHUNK)` buffers a
                # chunk before it yields, so the bytes that arrived before a
                # dropped connection never reached the part - and the part is
                # what the resume is built on. Without one, each read the
                # transport makes is written as it arrives; the file object
                # does the buffering.
                with open(part, mode) as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        digest.update(chunk)
                        have += len(chunk)
                        if on_progress is not None:
                            on_progress(name, have, expected_bytes)
    except httpx2.HTTPError as exc:
        raise InstallError(_ended(
            f"the download of {name} stopped after {have:,} of {expected_bytes:,} bytes "
            f"({exc.__class__.__name__}); it resumes from there next time")) from None
    return landed_or_refused(have, digest.hexdigest())


# --- the signer (criterion 5) ----------------------------------------------------


def verify_signer(path: Path, *, run: Callable[..., subprocess.CompletedProcess] | None = None) -> tuple[bool, str]:
    """Is the downloaded installer signed, validly, by Ollama Inc.?

    PowerShell's `Get-AuthenticodeSignature`, asked for two strings and
    answered as JSON; the shape was tried on this machine on 2026-09-23 against
    notepad.exe. Anything that is not `Valid` with `O=Ollama Inc.` in the
    subject - another signer, no signature, a hash mismatch, no PowerShell, an
    answer that is not JSON - refuses, and a refusal ends on the vendor page.
    """
    run = subprocess.run if run is None else run
    quoted = str(path).replace("'", "''")
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath '{quoted}'; "
        "[pscustomobject]@{status=$s.Status.ToString(); subject=[string]$s.SignerCertificate.Subject} "
        "| ConvertTo-Json -Compress"
    )
    try:
        done = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001 - every way of not answering is a refusal
        return False, f"powershell could not be asked about the signature ({exc.__class__.__name__})"
    try:
        answer = json.loads(done.stdout or "")
    except ValueError:
        return False, "powershell did not answer with the signature as JSON"
    if not isinstance(answer, dict):
        return False, "powershell did not answer with the signature as JSON"
    status = str(answer.get("status") or "")
    subject = str(answer.get("subject") or "")
    if status != "Valid":
        return False, f"signature status {status or 'unknown'}"
    if not SIGNER_PATTERN.search(subject):
        return False, f"signed by {subject or 'nobody'}, not by {SIGNER_SUBJECT}"
    return True, subject


# --- OLLAMA_MODELS for the user's account (G8) -------------------------------------


def set_user_variable(name: str, value: str) -> None:
    """HKCU\\Environment, the place `windows_env` already reads
    (`scribe/llm/base.py`), plus the broadcast that tells Explorer. Windows
    only, and called only for an install MyScribe itself makes in this sitting
    (ADR-017, Must Not)."""
    if sys.platform != "win32":
        raise RuntimeError("a user variable is written on Windows only; elsewhere it is a sentence")
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    _broadcast_environment(ctypes)


def unset_user_variable(name: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("a user variable is written on Windows only; elsewhere it is a sentence")
    import ctypes
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        return
    _broadcast_environment(ctypes)


def _broadcast_environment(ctypes) -> None:
    """WM_SETTINGCHANGE to every top-level window, the way `setx` does, so a
    later Explorer-started process sees the variable. Bounded: 5 s per window
    that does not answer."""
    hwnd_broadcast, wm_settingchange, smto_abortifhung = 0xFFFF, 0x001A, 0x0002
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast, wm_settingchange, 0, "Environment", smto_abortifhung, 5000, ctypes.byref(result)
    )


# --- the installer process (criterion 14) ------------------------------------------


def run_installer(
    command: list[str],
    *,
    on_event: Callable[[bool], None] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> int:
    """Run exactly `command` and wait for it - through a Ctrl-C as well.

    `on_event(True)` before and `on_event(False)` after, whatever the exit
    code: the engine prints those as one JSON line each, and the launcher
    refuses Quit between them (M10). A KeyboardInterrupt while waiting prints
    one line and waits again; the installer is never signalled, because a
    third-party installer stopped half-way is the one thing this must not do.
    """
    child = subprocess.Popen(command)
    if on_event is not None:
        on_event(True)
    try:
        while True:
            try:
                return child.wait()
            except KeyboardInterrupt:
                if on_line is not None:
                    on_line(INSTALLER_WAITS)
    finally:
        if on_event is not None:
            on_event(False)


def install(
    plan: dict,
    *,
    into: Path | None = None,
    models_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    model: str = "",
    on_progress: Callable[[str, int, int], None] | None = None,
    on_line: Callable[[str], None] | None = None,
    on_event: Callable[[bool], None] | None = None,
) -> Outcome:
    """Do what the plan showed, in the plan's order, and say what happened.

    Linux: nothing runs; the sentence is the four commands. macOS: download,
    sha256, `open`, and a sentence that waits with Check again - no marker,
    because no installer process finishes here for MyScribe to see (ADR-017,
    Exceptions). Windows: download, sha256, signer, then - only with a
    `models_dir` - OLLAMA_MODELS for the account and for this process, so the
    installer's children can inherit it; then `plan["command"]` verbatim;
    exit 0 and a binary at the known path write the marker, anything else
    writes none and takes the variable back with it (row 8a).
    """
    environ = os.environ if environ is None else environ
    folder = Path(plan["artifact"]).parent if into is None else Path(into)
    if folder / plan["name"] != Path(plan["artifact"]):
        raise ValueError("the plan names another folder than the one given: only what was shown may run")

    if not plan["runs_here"]:
        return Outcome(ok=False, installed=False, sentence=_commands_sentence(plan))

    try:
        landed = download(
            plan["url"], folder, plan["name"],
            expected_bytes=plan["bytes"], expected_sha256=plan["sha256"], on_progress=on_progress,
        )
    except InstallError as exc:
        return Outcome(ok=False, installed=False, sentence=str(exc))

    if plan["platform"] == "darwin":
        run_installer(plan["command"], on_event=on_event, on_line=on_line)
        return Outcome(
            ok=True,
            installed=False,
            sentence=(
                f"{plan['name']} {plan['tag']} was downloaded, its sha256 matched, and it was opened. "
                f"Drag Ollama into {plan['install_dir']}, start it, then Check again. Nobody has run "
                "this on a Mac yet."
            ),
        )

    ok, why = verify_signer(landed)
    if not ok:
        return Outcome(ok=False, installed=False, sentence=_ended(
            f"{plan['name']} was downloaded and its sha256 matched, but its signature was refused ({why})"))

    variable = ""
    if models_dir is not None:
        set_user_variable(VARIABLE, str(models_dir))
        environ[VARIABLE] = str(models_dir)  # type: ignore[index] - os.environ, or a test's dict
        variable = VARIABLE

    def take_back() -> None:
        if variable:
            unset_user_variable(variable)
            environ.pop(variable, None)  # type: ignore[attr-defined]

    code = run_installer(plan["command"], on_event=on_event, on_line=on_line)
    if code != 0:
        take_back()
        return Outcome(ok=False, installed=False, sentence=_ended(f"Ollama's installer ended with exit code {code}"))

    binary = _binary(environ, platform=plan["platform"])
    if not binary:
        take_back()
        return Outcome(ok=False, installed=False, sentence=_ended(
            "Ollama's installer ended with exit code 0, but no ollama binary is at the known location"))

    marker = {
        "tag": plan["tag"],
        # The pinned tag, not the daemon's answer: this is what MyScribe
        # installed by construction, and it is known before the daemon has
        # answered anything (criterion 8).
        "version": str(plan["tag"]).lstrip("v"),
        "binary": binary,
        "model": model or DEFAULT_MODEL,
        "installed": time.time(),
        "models_dir": str(models_dir) if models_dir is not None else "",
    }
    write_marker(marker)
    landed.unlink(missing_ok=True)  # 1.57 GB of installer is not kept once it ran
    sentence = f"Ollama {marker['version']} was installed at {Path(binary).parent}"
    if variable:
        sentence += f"; {VARIABLE} was set for your account to {models_dir}"
    return Outcome(ok=True, installed=True, sentence=sentence, marker=marker, variable=variable)


def _commands_sentence(plan: dict) -> str:
    return (
        "Ollama's own install script needs root and can install drivers, so MyScribe does not run it. "
        "Run these yourself - download, check the sum, read, run - then Check again: "
        + " ; ".join(plan["shown"])
    )


def not_answering_yet_sentence(model: str = DEFAULT_MODEL) -> str:
    """The 120 s poll ran out. Not failure: whether the installer starts the
    daemon is verified by nobody, and neither outcome is an error."""
    return (
        "Ollama is installed but not answering yet. Start Ollama, then Check again: "
        f"the pull of {model} is offered again in a sitting opened with --setup."
    )


# --- after the install: the poll and the pull (criteria 8, 11) ----------------------


def wait_for_version(
    provider: ollama.OllamaProvider,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> str:
    """Poll `GET /api/version` until it answers or `deadline` seconds have
    passed; the version, or "" - never a start (ADR-017)."""
    deadline = VERSION_WAIT_S if deadline is None else deadline
    clock = time.monotonic if clock is None else clock
    sleep = time.sleep if sleep is None else sleep
    started = clock()
    while True:
        found = provider.version()
        if found:
            return found
        if clock() - started >= deadline:
            return ""
        sleep(POLL_S)


def pull(
    provider: ollama.OllamaProvider,
    tag: str,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> None:
    """`POST /api/pull`, streamed, every NDJSON line read.

    A line with `error` fails the pull with Ollama's words - Ollama answers 200
    and then reports the failure inside the stream - and a stream that ends
    without `{"status": "success"}` fails too. Through `OllamaProvider`'s own
    client (`trust_env=False`), the way every request to the daemon goes.
    """
    client = provider.client(PULL_TIMEOUT_S)
    succeeded = False
    try:
        with client.stream("POST", "/api/pull", json={"model": tag, "stream": True}) as response:
            if response.status_code != 200:
                response.read()
                raise PullError(f"Ollama answered HTTP {response.status_code} to the pull of {tag}: {response.text[:200]}")
            for raw in response.iter_lines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    raise PullError(f"Ollama sent a line that is not JSON while pulling {tag}: {line[:200]}") from None
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise PullError(f"Ollama could not pull {tag}: {event['error']}")
                total, completed = event.get("total"), event.get("completed")
                if on_progress is not None and isinstance(total, int) and isinstance(completed, int) and total > 0:
                    on_progress(tag, completed, total)
                if event.get("status") == "success":
                    succeeded = True
    except httpx2.HTTPError as exc:
        raise PullError(
            f"Ollama at {provider.host} did not answer the pull of {tag} "
            f"({exc.__class__.__name__}){credentials.proxy_note()}"
        ) from None
    if not succeeded:
        raise PullError(f"the pull of {tag} ended without Ollama's final success line")


# --- the pin's owner (M7, criterion 15) -------------------------------------------


RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/tags/{tag}"
"""The tag's own metadata, never the newest release: a pin is checked against
what it pins."""


def pin_client() -> httpx2.Client:
    return httpx2.Client(timeout=30.0, follow_redirects=True)


def check_pin(found: dict | None = None, *, client: httpx2.Client | None = None, token: str | None = None) -> list[str]:
    """Does the pin still describe its release? The problems, or [].

    Without downloading an installer: one GET of the tag's metadata, whose
    `size` and `digest` fields are compared per asset; one GET of the release's
    small `sha256sum.txt`, a second source for the digest; and one HEAD per
    artifact URL, which must answer 200 with the pinned byte count - httpx
    keeps HEAD a HEAD across GitHub's redirect, where urllib would have turned
    it into a GET of 1.57 GB. `token` is sent as a bearer and never printed.
    """
    pin = release() if found is None else found
    tag = pin["tag"]
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MyScribe-ollama-pin-check",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    own = client is None
    client = pin_client() if client is None else client
    problems: list[str] = []
    try:
        answer = client.get(RELEASE_API.format(tag=tag), headers=headers)
        if answer.status_code != 200:
            return [f"the GitHub API answered HTTP {answer.status_code} for tag {tag}"]
        assets = {
            str(asset.get("name")): asset
            for asset in (answer.json() or {}).get("assets", [])
            if isinstance(asset, dict)
        }
        sums: dict[str, str] = {}
        listed = assets.get("sha256sum.txt")
        if listed is None:
            problems.append(f"{tag} has no sha256sum.txt among its assets")
        else:
            text = client.get(str(listed.get("browser_download_url")), headers={"User-Agent": headers["User-Agent"]}).text
            for line in text.splitlines():
                digest, _, name = line.strip().partition("  ")
                if digest and name:
                    sums[name.strip().removeprefix("./")] = digest.strip()
        for artifact in pin["artifacts"].values():
            name = artifact["name"]
            asset = assets.get(name)
            if asset is None:
                problems.append(f"{name}: not an asset of {tag}")
                continue
            if asset.get("size") != artifact["bytes"]:
                problems.append(f"{name}: the API says {asset.get('size')} bytes, the pin {artifact['bytes']:,} bytes")
            digest = str(asset.get("digest") or "").removeprefix("sha256:")
            if digest != artifact["sha256"]:
                problems.append(f"{name}: the API's digest {digest} is not the pin's {artifact['sha256']}")
            if sums and sums.get(name) != artifact["sha256"]:
                problems.append(f"{name}: sha256sum.txt's digest {sums.get(name)} is not the pin's {artifact['sha256']}")
            if asset.get("browser_download_url") != artifact["url"]:
                problems.append(f"{name}: the API's URL is not the pinned one")
            head = client.head(artifact["url"], headers={"User-Agent": headers["User-Agent"]}, follow_redirects=True)
            if head.status_code != 200:
                problems.append(f"{name}: HEAD {artifact['url']} answered HTTP {head.status_code}")
            else:
                length = head.headers.get("Content-Length")
                if length and int(length) != artifact["bytes"]:
                    problems.append(f"{name}: HEAD says {int(length):,} bytes, the pin {artifact['bytes']:,} bytes")
    except httpx2.HTTPError as exc:
        problems.append(f"the GitHub API could not be asked about {tag}: {exc.__class__.__name__}")
    finally:
        if own:
            client.close()
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scribe.ollama_setup", description="The Ollama pin, checked against its release.")
    parser.add_argument("--check-pin", action="store_true", help="compare scribe/ollama_release.json with the release's metadata; exit 1 on any difference")
    parser.add_argument("--release", type=Path, default=None, help="another pin file to check (a probe), instead of the shipped one")
    args = parser.parse_args(argv)
    if not args.check_pin:
        parser.print_help()
        return 2
    pin = release(args.release)
    with pin_client() as client:
        problems = check_pin(pin, client=client, token=os.environ.get("GITHUB_TOKEN") or None)
    if problems:
        print(f"the Ollama pin {pin['tag']} does not match its release:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    names = ", ".join(artifact["name"] for artifact in pin["artifacts"].values())
    print(f"the Ollama pin {pin['tag']} matches its release: size, digest and URL agree for {names}; sha256sum.txt agrees")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
