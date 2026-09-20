"""`.env` loading without a dependency.

The file holds the one secret this app needs - the Hugging Face token for the
gated pyannote weights - and possibly `SCRIBE_DATA_DIR`. Two rules, and they
are the whole reason this is not `python-dotenv`:

* **An environment that says something wins.** A key the process has a value
  for is left alone, so `SCRIBE_DATA_DIR=... python -m scribe` overrides the
  file rather than being overridden by it. A blank one is not a value: a
  shell's `export HF_TOKEN=`, a compose file or a service unit leaves the name
  set and empty, and that must not keep the token in the file from arriving.
  The same goes the other way - a blank line in the file, which is what
  `.env.example` ships, is not exported at all.
* **The file is optional.** A fresh clone has no `.env`; that is not an error,
  it is a machine that has not been given a token yet.

The syntax is the common subset every tool agrees on: one `KEY=VALUE` per
line, blank lines and `#` lines ignored, optional matching single or double
quotes around the value. Nothing else - no interpolation, no multi-line
values, no inline comments (a value is allowed to contain `#`).
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_DIR / ".env"

# Names the file instead. An installed copy runs from a read-only source tree,
# so its launcher keeps `.env` in the user's MyScribe folder (ADR-011).
PATH_VARIABLE = "SCRIBE_ENV_FILE"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_applied: set[str] = set()

# Windows has one variable per name whatever the case, and Python spells it in
# capitals there: `hf_token=...` in the file fills HF_TOKEN. Everywhere else
# the two are different variables.
_CASELESS = os.name == "nt"


def _spelled(name: str) -> str:
    """``name`` as the process environment spells it."""
    return name.upper() if _CASELESS else name


def _pair(raw: str) -> tuple[str, str] | None:
    """One line's key and value; None for a comment, a blank or a non-pair.

    The one definition of "a line for this name". parse() reads with it and
    write_env() replaces with it, so the writer cannot miss a line the reader
    honours - which is how a file came to hold two lines for one name.
    """
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return (key, value) if key else None


def parse(text: str) -> dict[str, str]:
    """`KEY=VALUE` lines to a mapping; comments, blanks and non-pairs skipped."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        pair = _pair(raw)
        if pair:
            values[pair[0]] = pair[1]
    return values


def _file(path: str | Path | None) -> Path:
    return Path(path if path is not None else os.environ.get(PATH_VARIABLE) or DEFAULT_PATH)


def load_dotenv(path: str | Path | None = None) -> dict[str, str]:
    """Read ``path`` (default: `SCRIBE_ENV_FILE`, else the repository's `.env`)
    into ``os.environ``.

    Returns everything the file defines, applied or not, so a caller can say
    what it found; `applied()` says which of them went in. A key the
    environment has a non-blank value for is not touched, and a blank value in
    the file is not exported.
    """
    path = _file(path)
    try:
        # utf-8-sig: Notepad likes to leave a BOM, and a BOM in front of the
        # first key would turn `HF_TOKEN` into `﻿HF_TOKEN`.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values = parse(text)
    for key, value in values.items():
        if not value.strip() or (os.environ.get(key) or "").strip():
            continue
        os.environ[key] = value
        _applied.add(_spelled(key))
    return values


def applied() -> frozenset[str]:
    """The names load_dotenv has put into the environment, over every call.

    Once the file is loaded `os.environ` cannot tell `.env` from the shell,
    and whoever reports where a setting came from has to. Names only: this is
    asked in order to be shown, and what `.env` holds is a secret. Spelled the
    way the environment spells them, which on Windows is in capitals whatever
    the file says - the name a caller asks after. It adds up and is never
    reset, because setup loads the file twice (main() and needed()) and the
    second load, finding every key set, applies nothing.
    """
    return frozenset(_applied)


def write_env(name: str, value: str, path: str | Path | None = None) -> Path:
    """Set ``name`` in the `.env` file, and leave exactly one line for it.

    The first line for the name is replaced where it stands and any later one
    is dropped: two lines for one name is a file where the last silently wins
    and nobody can see which they are using. On Windows a line for `hf_token`
    is a line for HF_TOKEN - one variable there, which the reader fills from
    whichever line comes first. Every other line is left as it was, comments
    and line endings included. A BOM is not written back - this module reads
    past one, a compose file or a service unit does not.

    The new text is whole in a neighbouring temp file before os.replace gives
    it the name, so a crash or a full disk never leaves half a `.env`. A
    replace that fails - on Windows, while another process holds the file
    open - is raised, not worked around by writing in place. A new file is
    0600 on POSIX, because what goes in here next may be a token; a file that
    exists keeps the mode somebody chose for it, and its owner and group as
    far as this process may give them.

    Refused with ValueError, and without repeating the value: a name that is
    not a variable name, and a value with a line break, which would be a
    second line whose contents somebody else chose.
    """
    if not _NAME.fullmatch(name):
        raise ValueError(f"not a variable name: {name!r}")
    if value.splitlines() not in ([], [value]):
        raise ValueError(f"the value for {name} contains a line break")
    path = _file(path)
    # Through a symlink to the file itself: replacing the link would swap it
    # for a regular file and leave the real one stale.
    target = Path(os.path.realpath(path))
    try:
        # newline="": the file's own line endings, not this platform's.
        with open(target, encoding="utf-8-sig", newline="") as handle:
            lines = handle.read().splitlines(keepends=True)
        was = target.stat()
    except FileNotFoundError:
        lines, was = [], None
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"

    line = f"{name}={value}"
    if parse(line).get(name) != value:
        # Padded or already quoted: bare, it would read back as something else.
        line = f'{name}="{value}"'
    out: list[str] = []
    written = False
    for raw in lines:
        pair = _pair(raw)
        if pair is None or _spelled(pair[0]) != _spelled(name):
            out.append(raw)
        elif not written:
            out.append(line + eol)
            written = True
    if not written:
        if out and not out[-1].endswith(("\n", "\r")):
            out[-1] += eol
        out.append(line + eol)

    # mkstemp makes it 0600 from the first byte, not world-readable until a
    # chmod. The `.env.` prefix keeps a leftover under .gitignore's `.env.*`.
    handle_no, temp = tempfile.mkstemp(dir=target.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(handle_no, "w", encoding="utf-8", newline="") as handle:
            handle.write("".join(out))
            handle.flush()
            os.fsync(handle.fileno())
        if was is not None and os.name == "posix":
            os.chmod(temp, stat.S_IMODE(was.st_mode))
            try:
                # `sudo python -m scribe.setup ...`: the temp file is root's,
                # and after the move so would `.env` be - 0600, and closed to
                # the user the app runs as. The group goes the same way, and
                # with it the point of a 0640.
                os.chown(temp, was.st_uid, was.st_gid)
            except OSError:
                pass  # only root may give a file away; it stays the writer's
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return path


def bootstrap() -> dict[str, str]:
    """What a command does first: load `.env`, then put `<repo>/.tools/bin` at
    the front of PATH when there is one.

    `.tools/bin` is where the installer puts the tools it fetches for this
    checkout (TASK-089.17: the pinned uv, and an ffmpeg where none answers).
    First, so they are found before whatever else the machine has under the
    same name. A checkout without the directory - every one made by hand -
    keeps the PATH it was given. Safe to call again: the directory ends up
    first once, not once per call.
    """
    values = load_dotenv()
    tools = REPO_DIR / ".tools" / "bin"
    if tools.is_dir():
        mine = os.path.normcase(str(tools))
        given = os.environ.get("PATH", "")
        rest = [entry for entry in given.split(os.pathsep) if os.path.normcase(entry) != mine] if given else []
        os.environ["PATH"] = os.pathsep.join([str(tools), *rest])
    return values
