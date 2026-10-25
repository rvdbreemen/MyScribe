"""The Windows installer ships the ``myscribe`` command (TASK-108, option C).

``packaging/windows/myscribe.cmd`` goes into ``{app}\\bin`` - not ``{app}``,
where Windows would run ``MyScribe.exe`` for ``myscribe`` because .EXE comes
before .CMD in PATHEXT. Putting ``{app}\\bin`` on the user's PATH is a task
the person ticks; it is off by default, like the login item (TASK-089.22).
Inno Setup's [Registry] cannot append to a value and take only its own part
back out, and ``uninsdeletevalue`` on ``HKCU\\Environment\\Path`` would delete
the person's whole PATH, so both directions are [Code] that touches only the
one entry. These are text tests: the installer is compiled with ISCC by hand
and never run by the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ISS = REPO / "packaging" / "windows" / "myscribe.iss"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def _section(name: str) -> list[str]:
    lines, inside = [], False
    for line in _iss().splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == f"[{name}]"
            continue
        if inside and stripped and not stripped.startswith(";"):
            lines.append(stripped)
    return lines


def test_the_shim_ships_in_its_own_bin_folder():
    (entry,) = [line for line in _section("Files") if "myscribe.cmd" in line]
    assert 'Source: "myscribe.cmd"' in entry
    assert 'DestDir: "{app}\\bin"' in entry
    assert (ISS.parent / "myscribe.cmd").is_file()


def test_the_path_task_is_there_and_off_by_default():
    (task,) = [line for line in _section("Tasks") if 'Name: "addtopath"' in line]
    assert re.search(r"Flags:\s*unchecked", task), task
    assert "myscribe" in task


def test_setup_tells_windows_the_environment_changed():
    directives = {line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip()
                  for line in _section("Setup") if "=" in line}
    assert directives.get("changesenvironment", "").lower() == "yes"


def test_no_registry_entry_touches_the_users_path():
    """``uninsdeletevalue`` on Path would delete all of it on uninstall."""
    assert not [line for line in _section("Registry") if "Environment" in line]


def test_the_path_entry_is_added_only_when_ticked_and_taken_out_on_uninstall():
    code = "\n".join(_section("Code"))
    assert "WizardIsTaskSelected('addtopath')" in code
    assert re.search(r"AddToPath\(ExpandConstant\('\{app\}\\bin'\)\)", code)
    # Unticked on an update: the entry an earlier install added goes.
    assert code.count("RemoveFromPath(ExpandConstant('{app}\\bin'))") == 2
    assert "procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);" in code
    assert "RegWriteExpandStringValue(HKCU, 'Environment', 'Path'" in code
