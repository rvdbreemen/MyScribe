"""`.env` loading without a dependency.

The file holds the one secret this app needs - the Hugging Face token for the
gated pyannote weights - and possibly `SCRIBE_DATA_DIR`. Two rules, and they
are the whole reason this is not `python-dotenv`:

* **The environment wins.** A key already set in the process is left alone,
  so `SCRIBE_DATA_DIR=... python -m scribe` overrides the file rather than
  being overridden by it. Only keys the environment does not have are filled.
* **The file is optional.** A fresh clone has no `.env`; that is not an error,
  it is a machine that has not been given a token yet.

The syntax is the common subset every tool agrees on: one `KEY=VALUE` per
line, blank lines and `#` lines ignored, optional matching single or double
quotes around the value. Nothing else - no interpolation, no multi-line
values, no inline comments (a value is allowed to contain `#`).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_DIR / ".env"

# Names the file instead. An installed copy runs from a read-only source tree,
# so its launcher keeps `.env` in the user's MyScribe folder (ADR-011).
PATH_VARIABLE = "SCRIBE_ENV_FILE"


def parse(text: str) -> dict[str, str]:
    """`KEY=VALUE` lines to a mapping; comments, blanks and non-pairs skipped."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_dotenv(path: str | Path | None = None) -> dict[str, str]:
    """Read ``path`` (default: `SCRIBE_ENV_FILE`, else the repository's `.env`)
    into ``os.environ``.

    Returns everything the file defines, applied or not, so a caller can say
    what it found. Keys the environment already has are not touched.
    """
    if path is None:
        path = os.environ.get(PATH_VARIABLE) or DEFAULT_PATH
    path = Path(path)
    try:
        # utf-8-sig: Notepad likes to leave a BOM, and a BOM in front of the
        # first key would turn `HF_TOKEN` into `﻿HF_TOKEN`.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values = parse(text)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values
