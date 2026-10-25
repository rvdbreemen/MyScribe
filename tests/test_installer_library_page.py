"""The Windows installer's library page (TASK-097, ADR-036).

Read out of `packaging/windows/myscribe.iss` the way
tests/test_installer_redirectionguard.py reads it: this machine may compile the
script with ISCC but never run what comes out (the installer touches the real
Start menu and the HKCU Run value). So these hold the shape of the handoff -
which file, which key, which escaping, written when, never over what is there -
and tests/test_launcher_library_request.py holds that the launcher reads exactly
that shape. Running a built Setup end to end is not done here.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ISS = REPO / "packaging" / "windows" / "myscribe.iss"
LAUNCHER_PATH = REPO / "packaging" / "launcher" / "myscribe_launcher.py"
_spec = importlib.util.spec_from_file_location("myscribe_launcher_iss", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


def _text() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def _code() -> str:
    """The [Code] section without its // comments."""
    text = _text()
    code = text[text.index("[Code]"):]
    code = code[:code.index("\n[", 1)] if "\n[" in code[1:] else code
    return "\n".join(line.split("//", 1)[0] for line in code.splitlines())


def _function(name: str) -> str:
    code = _code()
    start = re.search(rf"(function|procedure)\s+{name}\b", code)
    assert start, f"{name} is not in [Code]"
    end = re.search(r"\n(function|procedure)\s", code[start.end():])
    return code[start.start(): start.end() + end.start() if end else len(code)]


def test_there_is_a_library_page_after_the_program_folder_page():
    code = _code()
    assert re.search(r"CreateInputDirPage\(\s*wpSelectDir\b", code), "no folder page after the program folder"


def test_the_page_opens_on_the_per_user_default_or_the_library_parameter():
    """Prefilled with %LOCALAPPDATA%\\MyScribe - what the launcher reads as
    "as today" - and /LIBRARY= gives a silent install the same answer."""
    initialize = _function("InitializeWizard")
    assert "{localappdata}\\MyScribe" in _code()
    assert re.search(r"\{param:LIBRARY\|", initialize), "/LIBRARY= is not read"


def test_the_page_and_the_request_are_skipped_where_anything_already_says_where():
    """Never over a pointer that exists, and never over a default home that
    already holds an install: an update keeps what it has (AC #4)."""
    asks = _function("AskForLibrary")
    assert "FileExists" in asks and "MyScribe.location" in _code()
    assert "DirExists" in asks and "\\env" in _code()
    skip = _function("ShouldSkipPage")
    assert "AskForLibrary" in skip


def test_the_request_is_written_with_the_launchers_key_into_its_pointer_file():
    write = _function("WriteLibraryRequest")
    assert f"'{launcher.REQUEST_KEY}'" in write or f'"{launcher.REQUEST_KEY}"' in write
    assert launcher.pointer_path("win32", {"LOCALAPPDATA": "X"}).name == "MyScribe.location"
    assert "{localappdata}\\MyScribe.location" in _code()
    assert "AskForLibrary" in write, "the writer does not re-check that nothing is there"


def test_the_request_is_written_before_setup_starts_the_launcher():
    changed = _function("CurStepChanged")
    assert "WriteLibraryRequest" in changed
    assert changed.index("WriteLibraryRequest") < changed.index("Exec("), \
        "the launcher could start before the request it is meant to read"


def test_the_json_is_ascii_with_every_backslash_doubled():
    """A bare D:\\x is not JSON, and read_pointer then reports the pointer as
    unreadable. Everything outside printable ASCII becomes \\uXXXX, so the
    file's encoding cannot matter."""
    escape = _function("JsonString")
    assert "'\\\\'" in escape and "'\\'" in escape, "a backslash is not doubled"
    assert "'\\\"'" in escape or "'\\\"" in escape, "a double quote is not escaped"
    assert "\\u" in escape and "Format(" in escape and "126" in escape


def test_the_page_refuses_a_share_and_a_folder_inside_the_program_folder():
    check = _function("NextButtonClick")
    assert "'\\\\'" in check or "\\\\\\\\" in check, "a UNC path is not refused"
    assert "WizardDirValue" in check, "a library inside {app} is not refused"


def test_uninstall_deletes_neither_the_library_nor_the_pointer():
    text = _text()
    section = text[text.index("[UninstallDelete]"):]
    section = section[:section.index("\n[", 1)]
    entries = [line for line in section.splitlines() if line.strip() and not line.strip().startswith(";")]
    assert all("MyScribe.location" not in line for line in entries)
    assert all(not re.search(r"MyScribe\\?\"\s*$|\\data|filesandordirs|dirifempty", line) for line in entries), entries


def test_the_welcome_text_no_longer_says_the_first_start_asks_where():
    welcome = next(line for line in _text().splitlines() if line.startswith("WelcomeLabel2="))
    assert "first start asks where" not in welcome
    assert "next page" in welcome.lower() or "a later page" in welcome.lower()
