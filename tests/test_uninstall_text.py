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
    # TASK-112.06: with a systemd user session the entry is a user unit.
    linux = section[section.index("**Linux**"):section.index("**A clone**")]
    assert autostart.UNIT_NAME in linux and autostart.DESKTOP_NAME in linux
    assert "systemctl --user disable" in linux
    assert "`.tools/`" in readme and "python install.py` fetches it again" in readme


# --- TASK-112.04: an update or uninstall stops a running MyScribe first ---------


def test_the_stop_script_ships_in_the_install_folder():
    """In `{app}`, because the uninstaller runs it from there; the installer
    extracts the same entry to `{tmp}` before anything is replaced."""
    files = _section(_iss(), "Files")
    assert any('Source: "stop-myscribe.ps1"' in line and 'DestDir: "{app}"' in line for line in files), files
    assert (ISS.parent / "stop-myscribe.ps1").exists()


def test_an_update_and_an_uninstall_both_run_the_stop_script_first():
    code = "\n".join(_section(_iss(), "Code"))
    assert "function PrepareToInstall" in code and "ExtractTemporaryFile('stop-myscribe.ps1')" in code
    assert "function InitializeUninstall" in code and r"{app}\stop-myscribe.ps1" in code


def test_a_stopped_myscribe_with_its_login_item_on_comes_back_headless():
    """The command the login item runs (ADR-026), and only when the item is
    there: an update must not switch on a login start nobody chose. Without
    --no-browser: the launcher opens MyScribe in the browser once it serves."""
    code = "\n".join(_section(_iss(), "Code"))
    assert "'--at-login --headless'" in code
    assert "--no-browser" not in code
    assert r"RegValueExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MyScribe')" in code


def test_setup_always_ends_with_myscribe_open_in_the_browser():
    """Robert, 2026-10-23: at the end of an install MyScribe always opens in
    the browser. Not a checkbox, which a silent install skips: the [Code]
    starts it - the login item's headless way when that is what was running,
    otherwise the launcher with its window - and either opens the browser
    once MyScribe serves. One place starts it, so two launchers never race."""
    iss = _iss()
    assert _section(iss, "Run") == [], "a [Run] entry would be a second start"
    code = "\n".join(_section(iss, "Code"))
    post = code[code.index("procedure CurStepChanged"):code.index("function InitializeUninstall")]
    assert "ssPostInstall" in post
    assert "Exec(ExpandConstant('{app}\{#MyAppExe}'), ''" in post, post


# --- TASK-112.07: the lock and the login note beside the default home --------

def _launcher():
    import importlib.util

    path = REPO / "packaging" / "launcher" / "myscribe_launcher.py"
    spec = importlib.util.spec_from_file_location("myscribe_launcher_uninstall", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_uninstall_removes_the_lock_and_the_login_note_beside_the_default_home():
    r"""Robert, 2026-10-24: the launcher keeps `<home>.lock` and
    `<home>-at-login.txt` beside the home, and the uninstaller removes both
    beside the default home, %LOCALAPPDATA%\MyScribe. The names come from the
    launcher, so renaming one there turns this red. Only those two files:
    never the home itself, and never MyScribe.location, which the
    installation guide says stays."""
    launcher = _launcher()
    # The folder comes from the launcher too: default_home under a stand-in
    # %LOCALAPPDATA%, so a renamed or nested default home moves the paths the
    # uninstaller must name, not only the suffixes.
    base = Path("LOCALAPPDATA-stand-in")
    home = launcher.default_home("win32", {"LOCALAPPDATA": str(base)})
    files = [launcher.instance_lock_path(home), launcher.login_note_path(home)]
    assert {f.name for f in files} == {"MyScribe.lock", "MyScribe-at-login.txt"}
    relative = ["\\".join(f.relative_to(base).parts) for f in files]

    entries = _section(_iss(), "UninstallDelete")
    assert sorted(entries) == sorted(
        rf'Type: files; Name: "{{localappdata}}\{rel}"' for rel in relative)


def test_the_macos_and_linux_uninstall_text_names_the_lock_and_the_login_note():
    """The launcher leaves both files beside the home on every platform, not
    only on Windows (review of TASK-112.07): the macOS and Linux paragraphs of
    the uninstall section name them, by the names the launcher uses."""
    launcher = _launcher()
    home = Path(launcher.APP_NAME)
    names = [launcher.instance_lock_path(home).name, launcher.login_note_path(home).name]
    text = README.read_text(encoding="utf-8")
    section = text.split("## Uninstalling", 1)[1]
    for platform in ("**macOS**", "**Linux**"):
        paragraph = section.split(platform, 1)[1].split("\n\n", 1)[0]
        for name in names:
            assert f"`{name}`" in paragraph, (platform, name)
