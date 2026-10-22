"""The Windows installer starts MyScribe without RedirectionGuard (TASK-116).

Inno Setup 6.7.1 turns Windows' RedirectionGuard on for Setup by default. The
MyScribe that Setup starts - the "Start MyScribe" checkbox, and the headless
restart after an update - was measured with the mitigation enforced, and then
uv cannot follow its own Python junction: the start fails with os error 448.
This holds the one line that prevents it.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ISS = REPO / "packaging" / "windows" / "myscribe.iss"


def _setup_directives() -> dict[str, str]:
    """The [Setup] section's directives, by lower-cased name."""
    directives, inside = {}, False
    for line in ISS.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == "[Setup]"
            continue
        if inside and "=" in stripped and not stripped.startswith(";"):
            name, value = stripped.split("=", 1)
            directives[name.strip().lower()] = value.strip()
    return directives


def test_setup_does_not_enable_redirectionguard_for_what_it_starts():
    assert _setup_directives().get("redirectionguard", "").lower() == "no"
