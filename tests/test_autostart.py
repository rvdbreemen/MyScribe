"""Start MyScribe at login: one per-user entry, read back from the OS.

Three mechanisms, one of which this machine can actually exercise. The
LaunchAgent and the XDG autostart file are plain files, so their whole
lifecycle runs here against a tmp_path folder - what those tests prove is the
file's contents and that on/off/off-again behave, not that a real login
honours them. Nobody has logged in on a Mac or a Linux desktop with either in
place; both are built from the platforms' own documentation and are labelled
unverified wherever they are mentioned.

The Windows mechanism is exercised against the real registry, under a scratch
key of this test's own (`Software\\MyScribe-autostart-test`) that the fixture
deletes before and after. A fake `winreg` would have left the one mechanism
anybody here can verify asserted by nothing. The key's name carries no process
id on purpose: a run that is killed outright never reaches its `finally`, and
one fixed name means the next run collects that leftover instead of leaving a
new one beside it.

Nothing in this file uses the shipped default mechanism to *write*: that would
be this developer's own Run key, his own `~/Library` or his own `~/.config`.
Every lifecycle test injects a root it owns; the tests about the shipped paths
compare strings and touch no disk at all.
"""

from __future__ import annotations

import ast
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scribe import autostart

SCRATCH_KEY = r"Software\MyScribe-autostart-test"
# Somebody else's login entry, sitting where ours sits.
NEIGHBOUR = "not-myscribe"


def _drop_scratch_key() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY)
    except FileNotFoundError:
        pass


@pytest.fixture(params=["windows", "macos", "linux"])
def mechanism(request, tmp_path):
    """One mechanism per platform, each pointed at somewhere this test owns."""
    if request.param == "windows":
        if sys.platform != "win32":
            pytest.skip("no registry on this platform")
        _drop_scratch_key()
        try:
            yield autostart.WindowsRun(key=SCRATCH_KEY)
        finally:
            _drop_scratch_key()
    elif request.param == "macos":
        yield autostart.LaunchAgent(folder=tmp_path / "LaunchAgents")
    else:
        yield autostart.XdgAutostart(folder=tmp_path / "autostart")


def _remove_behind_its_back(mechanism) -> None:
    """Delete the entry the way a user with regedit or rm would, never through
    `disable()` - the point is that `status()` asks the OS and not a row."""
    if isinstance(mechanism, autostart.WindowsRun):
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, mechanism.key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, mechanism.name)
    else:
        mechanism.path.unlink()


def _put_a_neighbour(mechanism) -> None:
    """Another program's login entry, right beside ours.

    A real Run key and a real `~/.config/autostart` hold every other program's
    login entries. A test whose root holds nothing but MyScribe cannot tell
    `disable()` removing one value from `disable()` removing the whole key, so
    it gets a neighbour to lose.
    """
    if isinstance(mechanism, autostart.WindowsRun):
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, mechanism.key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, NEIGHBOUR, 0, winreg.REG_SZ, "somebody-else.exe")
    else:
        (mechanism.folder / NEIGHBOUR).write_text("somebody else's", encoding="utf-8")


def _the_neighbour_is_still_there(mechanism) -> bool:
    if isinstance(mechanism, autostart.WindowsRun):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, mechanism.key) as key:
                winreg.QueryValueEx(key, NEIGHBOUR)
        except FileNotFoundError:
            return False
        return True
    return (mechanism.folder / NEIGHBOUR).exists()


COMMAND = ("C:\\Program Files\\MyScribe\\MyScribe.exe", "--at-login", "--no-browser")


# --- the state comes from the OS, never from a row ---------------------------------


def test_a_login_entry_removed_by_hand_reads_as_off(mechanism):
    """AC1. Nothing is remembered: a user who deleted the value or the file
    gets a switch that says off, not a switch that insists it is on."""
    autostart.enable(mechanism, command=COMMAND)
    assert autostart.status(mechanism).on is True

    _remove_behind_its_back(mechanism)

    assert autostart.status(mechanism).on is False


def test_the_module_never_reads_or_writes_a_setting_row():
    """AC1, the other half: no database anywhere in it, so there is no row it
    could be answering from and no row that could go stale."""
    source = Path(autostart.__file__).read_text(encoding="utf-8")
    assert "sqlite" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {"sqlite3", "scribe"}, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in {"sqlite3", "scribe"}, node.module


# --- on, off, and off when it is already gone --------------------------------------


def test_on_off_and_off_when_already_gone(mechanism):
    """AC2. Three states per mechanism, including the one an uninstaller and a
    double click both reach: removing what is not there is not an error.

    And the other half of AC2 - "removes exactly that and nothing else".
    `enable()` first, because that is what creates the folder; then a
    neighbour beside our entry, which every `disable()` here has to leave
    alone. Without it, taking away the whole Run key or the whole autostart
    folder reads back exactly like taking away our one entry.
    """
    assert autostart.status(mechanism).on is False

    on = autostart.enable(mechanism, command=COMMAND)
    assert on.on is True
    assert autostart.status(mechanism).on is True

    _put_a_neighbour(mechanism)

    off = autostart.disable(mechanism)
    assert off.on is False
    assert autostart.status(mechanism).on is False
    assert _the_neighbour_is_still_there(mechanism), "disable() took another program's entry with it"

    again = autostart.disable(mechanism)
    assert again.on is False
    assert _the_neighbour_is_still_there(mechanism), "the second disable() took the neighbour"


def test_enabling_says_where_it_wrote_and_what_it_wrote(mechanism):
    """AC2. The Entry carries the place and the whole command, because the card
    prints both - 'on' alone is exactly what a user cannot check."""
    entry = autostart.enable(mechanism, command=COMMAND)

    assert entry.where == mechanism.where
    assert "MyScribe.exe" in entry.command
    assert "--at-login" in entry.command and "--no-browser" in entry.command
    # And what the OS holds is what the Entry says it holds.
    assert autostart.status(mechanism).command == entry.command


def test_what_each_mechanism_actually_leaves_on_disk(tmp_path):
    """AC2. The file a Mac and a Linux desktop would read, written out in full.

    Unverified by any real login - this asserts the contents against Apple's
    launchd keys and the freedesktop Autostart spec, nothing more.
    """
    agent = autostart.LaunchAgent(folder=tmp_path / "LaunchAgents")
    autostart.enable(agent, command=COMMAND)
    plist = plistlib.loads(agent.path.read_bytes())
    assert plist["Label"] == autostart.LABEL
    assert plist["ProgramArguments"] == list(COMMAND)
    assert plist["RunAtLoad"] is True
    assert agent.read() == shlex.join(COMMAND)

    xdg = autostart.XdgAutostart(folder=tmp_path / "autostart")
    autostart.enable(xdg, command=COMMAND)
    desktop = xdg.path.read_text(encoding="utf-8").splitlines()
    assert "[Desktop Entry]" in desktop
    assert "Type=Application" in desktop
    # The whole line, not "starts with Exec=": this string is what a desktop
    # actually runs, and it is the only evidence about Linux there can be
    # here. A path with a space in it is the case that breaks a naive join.
    assert r'Exec="C:\\Program Files\\MyScribe\\MyScribe.exe" --at-login --no-browser' in desktop


def test_the_desktop_entry_quotes_the_way_the_spec_says(tmp_path):
    """`Exec=` is the operative field on Linux - a desktop reads that one
    string and runs it - so it is pinned character for character.

    The Desktop Entry spec reserves four characters inside a quoted argument:
    a backslash, a double quote, a backtick and a dollar sign, each escaped
    with a backslash. `%` is a field code wherever it appears, so a literal
    one is doubled. Unverified by any real desktop: this is the spec, not a
    measurement.
    """
    xdg = autostart.XdgAutostart(folder=tmp_path / "autostart")

    assert xdg.render(("/opt/myscribe/start.sh", "--at-login")) == "/opt/myscribe/start.sh --at-login"
    assert xdg.render(("/opt/My Scribe/start.sh",)) == '"/opt/My Scribe/start.sh"'
    assert xdg.render(("/opt/a%b",)) == "/opt/a%%b"
    assert xdg.render(("/opt/a$b",)) == r'"/opt/a\$b"'
    assert xdg.render(("/opt/a`b",)) == r'"/opt/a\`b"'
    assert xdg.render(('/opt/a"b',)) == r'"/opt/a\"b"'
    assert xdg.render(("/opt/a\\b",)) == r'"/opt/a\\b"'


def test_an_entry_the_desktop_was_told_to_ignore_reads_as_off(tmp_path):
    """AC1, on the one mechanism whose users have a switch of their own.

    The Desktop Entry spec switches an entry off with `Hidden=true` rather
    than by deleting the file, and GNOME's own toggle writes
    `X-GNOME-Autostart-enabled=false`. Either has to read as off here, or the
    card says Yes for an entry the desktop is ignoring - which is exactly the
    stale answer AC1 exists against.
    """
    xdg = autostart.XdgAutostart(folder=tmp_path / "autostart")
    autostart.enable(xdg, command=COMMAND)
    live = xdg.path.read_text(encoding="utf-8")
    assert autostart.status(xdg, command=COMMAND).on is True

    xdg.path.write_text(live + "Hidden=true\n", encoding="utf-8")
    assert autostart.status(xdg, command=COMMAND).on is False

    xdg.path.write_text(
        live.replace("X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-enabled=false"),
        encoding="utf-8",
    )
    assert autostart.status(xdg, command=COMMAND).on is False


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows' own")
def test_the_windows_entry_is_a_value_under_the_user_hive_and_nothing_else():
    """AC2, AC3. The hive is the current user's, the key is the per-user Run
    key, and the entry is one named value in it."""
    import winreg

    run = autostart.WindowsRun()
    assert run.hive == winreg.HKEY_CURRENT_USER
    assert run.key == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert run.name == "MyScribe"
    assert run.where == r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\MyScribe"


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows' own")
def test_the_registry_value_is_the_command_a_shell_would_run():
    """AC2. What is written is one command line, quoted the way Windows quotes
    one, so a path with a space in it survives the next login."""
    _drop_scratch_key()
    try:
        run = autostart.WindowsRun(key=SCRATCH_KEY)
        autostart.enable(run, command=COMMAND)

        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as key:
            value, kind = winreg.QueryValueEx(key, "MyScribe")
        assert kind == winreg.REG_SZ
        assert value == subprocess.list2cmdline(COMMAND)
        assert value.startswith('"C:\\Program Files\\MyScribe\\MyScribe.exe"')
    finally:
        _drop_scratch_key()


# --- per user only -----------------------------------------------------------------


def test_every_mechanism_names_a_place_inside_this_user_s_own_profile():
    """AC3. The exact path each mechanism will touch, compared as strings:
    nothing is created, nothing is read, and no default folder of this
    developer's is written to just to find out where it is."""
    assert autostart.default_mechanism("win32").where == (
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\MyScribe"
    )
    assert autostart.default_mechanism("darwin").where == str(
        Path.home() / "Library" / "LaunchAgents" / f"{autostart.LABEL}.plist"
    )
    assert autostart.default_mechanism("linux", {"XDG_CONFIG_HOME": str(Path("/x/cfg"))}).where == str(
        Path("/x/cfg") / "autostart" / "myscribe.desktop"
    )
    assert autostart.default_mechanism("linux", {}).where == str(
        Path.home() / ".config" / "autostart" / "myscribe.desktop"
    )
    for platform in ("darwin", "linux"):
        assert autostart.default_mechanism(platform, {}).where.startswith(str(Path.home()))


def test_nothing_machine_wide_is_named_in_the_module():
    """AC3. No machine-wide hive, no system-wide agent folder, no system
    autostart directory, and nothing that asks for rights this user has not
    got. This is one account's own entry; an uninstall needs no administrator
    to take it away again."""
    source = Path(autostart.__file__).read_text(encoding="utf-8")

    for forbidden in ("HKEY_LOCAL_MACHINE", "HKLM", "/Library/LaunchAgents", "/etc/xdg"):
        assert forbidden not in source, forbidden
    for elevation in ("runas", "ShellExecute", "sudo", "AdminExecute"):
        assert elevation not in source, elevation


def test_the_module_imports_on_a_machine_without_a_registry():
    """`winreg` is Windows-only stdlib and this module is imported by the
    settings page, so a top-level import would take the whole app down on a
    Mac. Asserted on the source, because this machine cannot run the failure."""
    tree = ast.parse(Path(autostart.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        assert "winreg" not in names, "winreg must be imported inside the Windows mechanism"


# --- what the entry starts ---------------------------------------------------------


def test_a_release_registers_the_launcher_and_a_clone_the_start_script(tmp_path):
    """AC4. A release names the launcher, because only the launcher re-syncs
    the environment after an update; a clone names the start script that is
    already in the repository, and nothing new is created."""
    release = autostart.start_command(
        environ={autostart.LAUNCHER_VARIABLE: r"C:\Program Files\MyScribe\MyScribe.exe"},
        platform="win32",
    )
    assert release == (r"C:\Program Files\MyScribe\MyScribe.exe", "--at-login", "--no-browser")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "start.ps1").write_text("", encoding="utf-8")
    (scripts / "start.sh").write_text("", encoding="utf-8")

    windows_clone = autostart.start_command(environ={}, repo=tmp_path, platform="win32")
    assert windows_clone is not None
    assert windows_clone[0] == "powershell"
    assert str(scripts / "start.ps1") in windows_clone
    assert "-Detached" in windows_clone and "--no-browser" in windows_clone
    # AC5, the clone door: powershell.exe is a console program, so without
    # this the login opens a console window nobody asked for. The script's
    # own child is already started hidden (`scripts/start.ps1`, -Detached).
    assert windows_clone[windows_clone.index("-WindowStyle") + 1] == "Hidden"

    posix_clone = autostart.start_command(environ={}, repo=tmp_path, platform="linux")
    assert posix_clone is not None
    assert posix_clone[0] == "/bin/bash"
    assert str(scripts / "start.sh") in posix_clone
    assert "--detached" in posix_clone and "--no-browser" in posix_clone


def test_a_mac_release_names_something_launchd_can_actually_execute():
    """AC4, the Mac's own shape. The stable path a release names there is the
    `.app` bundle (design spec §3.10) - and a bundle is a directory.

    `launchd.plist(5)` hands `ProgramArguments` to `execvp(3)`: no
    LaunchServices, so argv[0] has to be a file that can be executed. A
    bundle in that position fails to spawn at every login. `open -a <bundle>
    --args ...` is `open(1)`'s documented way to start a bundle with
    arguments, so that is what the entry runs.

    Not run at a real login, like everything macOS here - but composed from
    the platform's own documentation rather than from a guess.
    """
    bundle = "/Applications/MyScribe.app"

    mac = autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: bundle}, platform="darwin")

    assert mac is not None
    assert not mac[0].endswith(".app"), "argv[0] goes to execvp; a bundle directory is not executable"
    assert mac[:4] == ("/usr/bin/open", "-a", bundle, "--args")
    assert mac[4:] == ("--at-login", "--no-browser")

    # The other two doors name their launcher directly: both are files.
    for platform, launcher in (("win32", r"C:\Program Files\MyScribe\MyScribe.exe"), ("linux", "/opt/MyScribe.AppImage")):
        assert autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: launcher}, platform=platform) == (
            launcher, "--at-login", "--no-browser",
        )


def test_where_nothing_is_known_to_start_it_refuses_rather_than_guesses(tmp_path, monkeypatch):
    """AC4's honest end. A release whose launcher never said where it is, and a
    checkout without the start scripts, both leave nothing safe to register: a
    path that is wrong at the next login is worse than no entry at all."""
    assert autostart.start_command(environ={}, repo=tmp_path, platform="win32") is None

    monkeypatch.setattr(autostart, "start_command", lambda **kwargs: None)
    mechanism = autostart.XdgAutostart(folder=tmp_path / "autostart")
    with pytest.raises(autostart.NothingToStart):
        autostart.enable(mechanism)
    assert not (tmp_path / "autostart").exists()
    assert autostart.status(mechanism).command == ""


def test_status_offers_the_command_it_would_register_while_it_is_off(tmp_path):
    """The card shows what switching it on would write, before it is written."""
    mechanism = autostart.XdgAutostart(folder=tmp_path / "autostart")

    off = autostart.status(mechanism, command=COMMAND)

    assert off.on is False
    assert "--at-login" in off.command
    assert not (tmp_path / "autostart").exists()
