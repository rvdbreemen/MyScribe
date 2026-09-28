"""Whether a version is a pre-release, for release.yml (TASK-100).

A version ending in a PEP 440 pre-release or dev segment - `0.8.0b1`,
`1.0.0rc1`, `0.9.0a3`, `0.8.0.dev2` - is published as a GitHub pre-release
that does not become Latest, so the download link and the repository's
website field keep pointing at the stable release. Standard library only: the
workflow runs this on the runner's own Python before anything is installed.

    python packaging/release_kind.py 0.8.0b1   ->  true
    python packaging/release_kind.py 0.7.2     ->  false
"""

from __future__ import annotations

import re
import sys

_PRE = re.compile(r"(a|b|rc)\d+$|\.dev\d+$")


def is_prerelease(version: str) -> bool:
    return bool(_PRE.search(version.strip()))


if __name__ == "__main__":
    print("true" if is_prerelease(sys.argv[1]) else "false")
