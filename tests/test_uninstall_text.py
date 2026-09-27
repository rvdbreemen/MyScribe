"""What stays behind after an uninstall, and whose it is (TASK-089.23).

The Windows installer and the installation guide are text, and text drifts: the README
said the program installs into the folder that is really the library's, and
the installer's header cited a decision record by a number it no longer has.
These tests read the two files and hold them to the code they describe.
"""

from __future__ import annotations

import re
from pathlib import Path

from scribe import autostart

REPO = Path(__file__).resolve().parents[1]
ISS = REPO / "packaging" / "windows" / "myscribe.iss"
# The install and uninstall text moved from README.md to the installation
# guide on 2026-09-27; the README links to it.
README = REPO / "docs" / "installation.md"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def _section(text: str, name: str) -> list[str]:
    """The non-comment lines of one [section] of the .iss."""
    lines, inside = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == f"[{name}]"
            continue
        if inside and stripped and not stripped.startswith(";"):
            lines.append(stripped)
    return lines


def test_an_uninstall_removes_the_login_item_and_an_install_never_writes_one():
    """Settings > Start at login writes a value named MyScribe under the Run key
    (scribe/autostart.py). The uninstaller has to take it away, and the
    installer must not create it: a login item is a choice, default No
    (TASK-089.22). Inno Setup: ValueType none writes no value, dontcreatekey
    creates nothing when the key is missing, uninsdeletevalue deletes the
    value on uninstall."""
    entries = [line for line in _section(_iss(), "Registry") if 'ValueName: "MyScribe"' in line]

    assert len(entries) == 1, _section(_iss(), "Registry")
    (entry,) = entries
    assert "Root: HKCU" in entry
    assert f'Subkey: "{autostart.RUN_KEY}"' in entry
    assert f'ValueName: "{autostart.APP_NAME}"' in entry
    assert "ValueType: none" in entry, "any other type writes the value at install time"
    flags = re.search(r"Flags:\s*([^;]+)", entry).group(1).split()
    assert "uninsdeletevalue" in flags and "dontcreatekey" in flags
    assert "uninsdeletekey" not in flags, "the Run key holds other programs' items"


def test_the_installer_cites_the_record_by_its_current_number():
    first = _iss().splitlines()[0]
    assert "ADR-011" in first and "ADR-008" not in first


def test_the_welcome_text_says_the_folder_is_a_default_and_who_owns_what_stays():
    (welcome,) = [line for line in _iss().splitlines() if line.startswith("WelcomeLabel2=")]

    assert "only the default" in welcome
    assert "Ollama" in welcome and "belong to you" in welcome
    assert "uninstaller" in welcome.lower()


def test_the_readme_and_the_installer_agree_on_where_the_program_goes():
    """README.md said %LOCALAPPDATA%\\MyScribe - the library's folder - where the
    installer puts the program into %LOCALAPPDATA%\\Programs\\MyScribe."""
    default_dir = re.search(r"^DefaultDirName=(.+)$", _iss(), re.M).group(1).strip()
    assert default_dir == r"{localappdata}\Programs\{#MyAppName}"

    readme = README.read_text(encoding="utf-8")
    windows = readme[readme.index("* **Windows** - SmartScreen"):]
    windows = windows[: windows.index("* **Linux**")]
    assert r"%LOCALAPPDATA%\Programs\MyScribe" in windows


def test_the_readme_has_an_uninstalling_section_per_platform_and_documents_tools():
    readme = README.read_text(encoding="utf-8")
    section = readme[readme.index("## Uninstalling"):]
    # The last section of the guide: it runs to the end of the file.
    if "\n## " in section[1:]:
        section = section[: section.index("\n## ", 1)]

    for heading in ("**Windows**", "**macOS**", "**Linux**", "**A clone**"):
        assert heading in section, heading
    assert "MyScribe.location" in section, "the pointer file stays and is named"
    assert f"{autostart.LABEL}.plist" in section
    assert autostart.DESKTOP_NAME in section
    assert "`.tools/`" in readme and "python install.py` fetches it again" in readme
