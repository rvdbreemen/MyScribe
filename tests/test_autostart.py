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
import configparser
import importlib.util
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


class FakeSystemctl:
    """`systemctl --user` as far as this module asks it anything.

    It keeps the one fact the real one keeps - which units are enabled - and
    answers `is-enabled` from that and from whether the unit file is still
    there, the way the user manager does. Every call is recorded, so a test
    can say which verbs were asked and in what order. The real one was driven
    in WSL for TASK-112.06; this stands in for it on a machine without systemd.
    """

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.calls: list[tuple[str, ...]] = []
        self.enabled: set[str] = set()

    def __call__(self, args):
        args = tuple(args)
        self.calls.append(args)
        verb, *rest = args
        if verb == "enable":
            if not (self.folder / rest[0]).exists():
                return subprocess.CompletedProcess(args, 1, "", f"Unit file {rest[0]} does not exist.")
            self.enabled.add(rest[0])
        elif verb == "disable":
            self.enabled.discard(rest[0])
        elif verb == "is-enabled":
            on = rest[0] in self.enabled and (self.folder / rest[0]).exists()
            return subprocess.CompletedProcess(args, 0 if on else 1, "enabled\n" if on else "disabled\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


def _systemd(tmp_path, environ: dict | None = None):
    """A user unit under a folder this test owns, a fake `systemctl`, and the
    desktop file it replaces beside it."""
    folder = tmp_path / "systemd" / "user"
    return autostart.SystemdUserUnit(
        folder=folder,
        run=FakeSystemctl(folder),
        legacy=autostart.XdgAutostart(folder=tmp_path / "autostart"),
        environ={} if environ is None else environ,
    )


class FakeLaunchctl:
    """`launchctl` as far as this module asks it anything.

    Every call is recorded with whether the caller waited for it, so a test
    can say which verbs were asked, in what order, and which one was left to
    run on its own. `bootstrap` answers with `refuse` when a test sets it.
    Nothing here has met a real launchd: there is no Mac on this machine.
    """

    def __init__(self):
        self.calls: list[tuple[tuple[str, ...], bool]] = []
        self.refuse: subprocess.CompletedProcess | None = None

    def __call__(self, args, *, wait=True):
        args = tuple(args)
        self.calls.append((args, wait))
        if not wait:
            return None
        if args[0] == "bootstrap" and self.refuse is not None:
            return self.refuse
        return subprocess.CompletedProcess(args, 0, "", "")

    def verbs(self) -> list[str]:
        return [args[0] for args, _wait in self.calls]


def _agent(tmp_path, uid: int = 501):
    """A LaunchAgent under a `Library` this test owns, with a fake `launchctl`
    and a fixed uid: no real launchd is asked and no `os.getuid` is needed,
    which this Windows machine does not have."""
    return autostart.LaunchAgent(folder=tmp_path / "Library" / "LaunchAgents", run=FakeLaunchctl(), uid=uid)


@pytest.fixture(params=["windows", "macos", "linux", "systemd"])
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
        yield _agent(tmp_path)
    elif request.param == "systemd":
        yield _systemd(tmp_path)
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


COMMAND = ("C:\\Program Files\\MyScribe\\MyScribe.exe", "--at-login", "--no-browser", "--headless")


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
    agent = _agent(tmp_path)  # a stand-in launchctl: a macOS CI runner must not load this
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
    assert r'Exec="C:\\Program Files\\MyScribe\\MyScribe.exe" --at-login --no-browser --headless' in desktop


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
    # TASK-112.06: a release with a systemd user session gets a user unit,
    # in the same per-user config directory.
    release = {autostart.LAUNCHER_VARIABLE: "/opt/MyScribe.AppImage"}
    assert autostart.default_mechanism("linux", {**release, "XDG_CONFIG_HOME": str(Path("/x/cfg"))},
                                       session=True).where == str(
        Path("/x/cfg") / "systemd" / "user" / "myscribe.service"
    )
    assert autostart.default_mechanism("linux", release, session=True).where == str(
        Path.home() / ".config" / "systemd" / "user" / "myscribe.service"
    )
    for platform in ("darwin", "linux"):
        assert autostart.default_mechanism(platform, {}).where.startswith(str(Path.home()))


def test_nothing_machine_wide_is_named_in_the_module():
    """AC3. No machine-wide hive, no system-wide agent folder, no system
    autostart directory, and nothing that asks for rights this user has not
    got. This is one account's own entry; an uninstall needs no administrator
    to take it away again."""
    source = Path(autostart.__file__).read_text(encoding="utf-8")

    for forbidden in ("HKEY_LOCAL_MACHINE", "HKLM", "/Library/LaunchAgents", "/etc/xdg",
                      "/etc/systemd", "/usr/lib/systemd", "--system", "--global"):
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
    assert release == (r"C:\Program Files\MyScribe\MyScribe.exe", "--at-login", "--no-browser", "--headless")

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


def test_a_mac_release_names_the_binary_inside_the_bundle():
    """TASK-112.05. The stable path a release names on a Mac is the `.app`
    bundle (`launcher_path`), and a bundle is a directory - `launchd.plist(5)`
    hands `ProgramArguments` to `execvp(3)`, so argv[0] has to be a file.

    Up to 0.9.x that file was `/usr/bin/open -a <bundle>`, which returns at
    once: launchd then watched nothing, logged nothing, and `KeepAlive` would
    have been a respawn loop. The entry now names the binary inside the
    bundle, `Contents/MacOS/<FROZEN_NAME>`, so launchd watches the launcher
    itself. The name is read from the build script, so the two cannot drift.

    Not run at a real login, like everything macOS here.
    """
    bundle = "/Applications/MyScribe.app"

    mac = autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: bundle}, platform="darwin")

    frozen = _build_constant("FROZEN_NAME")
    assert mac == (f"{bundle}/Contents/MacOS/{frozen}", "--at-login", "--no-browser", "--headless")
    assert "/usr/bin/open" not in mac
    assert not mac[0].endswith(".app"), "argv[0] goes to execvp; a bundle directory is not executable"

    # A launcher that handed down a file (an unpacked build) is named as it is.
    unpacked = "/Users/me/MyScribe/MyScribe"
    assert autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: unpacked}, platform="darwin")[0] == unpacked

    # The other two doors name their launcher directly: both are files.
    for platform, launcher in (("win32", r"C:\Program Files\MyScribe\MyScribe.exe"), ("linux", "/opt/MyScribe.AppImage")):
        assert autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: launcher}, platform=platform) == (
            launcher, "--at-login", "--no-browser", "--headless",
        )


def test_a_login_starts_the_launcher_headless_on_every_os():
    """TASK-112.01 AC #1, ADR-026's Must: no window and no console at login.
    The launcher's own `--headless` door; without it the Tk window opens
    and has to be kept open."""
    for platform, launcher in (("win32", r"C:\Program Files\MyScribe\MyScribe.exe"),
                               ("darwin", "/Applications/MyScribe.app"),
                               ("linux", "/opt/MyScribe.AppImage")):
        command = autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: launcher}, platform=platform)
        assert command is not None and "--headless" in command, platform


# --- an entry an older version wrote ----------------------------------------------


OLD = ("C:\\Program Files\\MyScribe\\MyScribe.exe", "--at-login", "--no-browser")
RELEASE = {autostart.LAUNCHER_VARIABLE: "C:\\Program Files\\MyScribe\\MyScribe.exe"}


class _CountingXdg(autostart.XdgAutostart):
    """A real desktop-entry mechanism that counts its writes."""

    def __init__(self, folder):
        super().__init__(folder=folder)
        self.writes = 0

    def write(self, command) -> None:
        self.writes += 1
        super().write(command)


def test_an_entry_an_older_version_wrote_is_rewritten_to_the_current_command(tmp_path):
    """AC #2: 0.8.6 and earlier registered no `--headless`, so their login
    opened the window. The next start brings the entry up to date."""
    mechanism = _CountingXdg(tmp_path / "autostart")
    mechanism.write(OLD)

    entry = autostart.refresh(mechanism, command=COMMAND, environ=RELEASE)

    assert entry is not None and entry.on is True
    assert mechanism.read() == mechanism.render(COMMAND)
    assert mechanism.writes == 2


def test_each_mechanism_rewrites_an_older_entry_and_keeps_its_neighbour(mechanism):
    """The same rewrite against the real Run key (a scratch one), the plist
    and the desktop file: each quotes the launcher path its own way, and the
    match on "names this launcher" has to survive all three."""
    mechanism.write(OLD)
    _put_a_neighbour(mechanism)

    entry = autostart.refresh(mechanism, command=COMMAND, environ=RELEASE)

    assert entry is not None
    assert mechanism.read() == mechanism.render(COMMAND)
    assert _the_neighbour_is_still_there(mechanism)



@pytest.mark.skipif(sys.platform != "win32", reason="no registry on this platform")
def test_on_windows_the_launcher_path_matches_in_any_case():
    """Windows paths are case-insensitive, and the installer and
    `sys.executable` need not agree on the case of `programs`."""
    _drop_scratch_key()
    try:
        mechanism = autostart.WindowsRun(key=SCRATCH_KEY)
        mechanism.write((r"C:\program files\myscribe\MyScribe.exe", "--at-login", "--no-browser"))

        assert autostart.refresh(mechanism, command=COMMAND, environ=RELEASE) is not None
        assert mechanism.read() == mechanism.render(COMMAND)
    finally:
        _drop_scratch_key()

def test_an_entry_that_is_switched_off_stays_off(tmp_path):
    """AC #2's other half: off is a missing entry, or the desktop's own
    `Hidden=true`. Neither is written back."""
    missing = _CountingXdg(tmp_path / "missing")
    assert autostart.refresh(missing, command=COMMAND, environ=RELEASE) is None
    assert missing.writes == 0 and not missing.path.exists()

    hidden = _CountingXdg(tmp_path / "hidden")
    hidden.folder.mkdir()
    hidden.path.write_text(f"[Desktop Entry]\nExec={hidden.render(OLD)}\nHidden=true\n", encoding="utf-8")
    assert autostart.refresh(hidden, command=COMMAND, environ=RELEASE) is None
    assert "Hidden=true" in hidden.path.read_text(encoding="utf-8")


def test_an_entry_that_is_already_current_is_not_written(tmp_path):
    mechanism = _CountingXdg(tmp_path / "autostart")
    mechanism.write(COMMAND)

    autostart.refresh(mechanism, command=COMMAND, environ=RELEASE)

    assert mechanism.writes == 1


def test_only_a_launcher_rewrites_and_only_its_own_entry(tmp_path):
    """A clone on the same machine as a release must not point the release's
    entry at its start script, and a second install must not take over an
    entry that names another launcher. Neither is "an older version"."""
    clone = _CountingXdg(tmp_path / "clone")
    clone.write(OLD)
    assert autostart.refresh(clone, command=("/bin/bash", "start.sh"), environ={}) is None
    assert clone.writes == 1

    other = _CountingXdg(tmp_path / "other")
    elsewhere = ("D:\\Apps\\MyScribe\\MyScribe.exe", "--at-login", "--no-browser")
    other.write(elsewhere)
    assert autostart.refresh(other, command=COMMAND, environ=RELEASE) is None
    assert other.read() == other.render(elsewhere)


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


# --- the start that does the rewriting ---------------------------------------------


def test_a_registry_that_will_not_answer_costs_the_rewrite_not_the_start(monkeypatch):
    """The app calls this from its lifespan; a refusal is a log line."""
    from scribe import app as app_module

    def refuse(*_args, **_kwargs):
        raise OSError("access denied")

    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(autostart, "refresh", refuse)
    monkeypatch.setattr(app_module.applog, "log", lambda event, **fields: logged.append((event, fields)))

    app_module._refresh_login_entry()  # must not raise

    assert logged == [("autostart.refresh_failed", {"level": "warn", "error": "access denied"})]


def test_a_rewrite_is_logged_with_what_was_written(monkeypatch):
    from scribe import app as app_module

    entry = autostart.Entry(where=r"HKCU\Run\MyScribe", command="MyScribe.exe --headless", on=True)
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(autostart, "refresh", lambda *_a, **_k: entry)
    monkeypatch.setattr(app_module.applog, "log", lambda event, **fields: logged.append((event, fields)))

    app_module._refresh_login_entry()

    assert logged == [("autostart.rewritten", {"where": entry.where, "command": entry.command})]


# --- Linux: a systemd user unit, the desktop file as fallback (TASK-112.06) --------
#
# ADR-026, answered by Robert on 2026-10-19: a systemd user unit where a
# systemd user session runs, the XDG autostart file where none does. The unit
# is what restarts a crash and logs to the journal; the desktop file nothing
# supervises. These run against a fake `systemctl`. The real user manager
# (systemd 255 in WSL, a scratch user, the 0.9.1 AppImage) was driven once
# for TASK-112.06; the commit that added this section says what it showed.

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py"
APPIMAGE = "/home/me/Apps/MyScribe.AppImage"
LINUX_RELEASE = {autostart.LAUNCHER_VARIABLE: APPIMAGE}
AT_LOGIN = (APPIMAGE, "--at-login", "--no-browser", "--headless")


def _launcher():
    spec = importlib.util.spec_from_file_location("myscribe_launcher_for_autostart", LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unit(mechanism) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # systemd's keys are case-sensitive
    parser.read_string(mechanism.path.read_text(encoding="utf-8"))
    return parser


def test_with_a_systemd_user_session_switching_on_writes_and_enables_the_unit(tmp_path):
    """AC #1. The unit file is written under the user's own `systemd/user`,
    the manager is told to read it, and it is enabled."""
    unit = _systemd(tmp_path)

    entry = autostart.enable(unit, command=AT_LOGIN)

    assert unit.path == tmp_path / "systemd" / "user" / "myscribe.service"
    assert unit.path.is_file()
    assert unit.run.calls == [("daemon-reload",), ("enable", "myscribe.service")]
    assert entry.on is True and entry.where == str(unit.path)
    assert autostart.status(unit).on is True
    assert _unit(unit)["Install"]["WantedBy"] == "default.target"


def test_switching_the_unit_off_disables_and_removes_it(tmp_path):
    """AC #1's other half: disabled first, then the file goes, then the
    manager forgets it. A second off is not an error and asks nothing."""
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=AT_LOGIN)
    unit.run.calls.clear()

    off = autostart.disable(unit)

    assert off.on is False
    assert not unit.path.exists()
    assert unit.run.calls[:2] == [("disable", "myscribe.service"), ("daemon-reload",)]
    assert "myscribe.service" not in unit.run.enabled

    unit.run.calls.clear()
    again = autostart.disable(unit)
    assert again.on is False
    assert "disable" not in unit.run.verbs()


def test_a_unit_disabled_with_systemctl_reads_as_off(tmp_path):
    """`systemctl --user disable myscribe` leaves the file and removes the
    link. The card has to say off, the way a hidden desktop file does."""
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=AT_LOGIN)

    unit.run.enabled.clear()

    assert unit.path.exists()
    assert autostart.status(unit, command=AT_LOGIN).on is False


def test_without_a_systemd_user_session_the_xdg_file_is_written_as_today(tmp_path):
    """AC #2. No user manager - no `$XDG_RUNTIME_DIR/systemd/private`, or no
    `systemctl` - and Linux writes the same desktop file it always did."""
    config = {**LINUX_RELEASE, "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    mechanism = autostart.default_mechanism("linux", config, session=False)
    assert type(mechanism) is autostart.XdgAutostart

    autostart.enable(mechanism, command=AT_LOGIN)
    assert (tmp_path / "cfg" / "autostart" / "myscribe.desktop").read_text(encoding="utf-8") == (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MyScribe\n"
        "Exec=/home/me/Apps/MyScribe.AppImage --at-login --no-browser --headless\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    assert type(autostart.default_mechanism("linux", config, session=True)) is autostart.SystemdUserUnit


def test_a_user_session_is_the_managers_own_socket_and_a_systemctl_to_ask(tmp_path):
    """What `default_mechanism` asks on every Settings load: two cheap
    looks, and no process started."""
    runtime = tmp_path / "run"
    (runtime / "systemd").mkdir(parents=True)
    found = lambda name, path=None: "/usr/bin/systemctl"  # noqa: E731
    absent = lambda name, path=None: None  # noqa: E731

    assert autostart.systemd_user_session({}, which=found) is False
    assert autostart.systemd_user_session({"XDG_RUNTIME_DIR": str(runtime)}, which=found) is False
    (runtime / "systemd" / "private").write_text("", encoding="utf-8")
    assert autostart.systemd_user_session({"XDG_RUNTIME_DIR": str(runtime)}, which=found) is True
    assert autostart.systemd_user_session({"XDG_RUNTIME_DIR": str(runtime)}, which=absent) is False


def test_a_clone_keeps_the_desktop_file_even_with_a_user_session():
    """A clone's command is `start.sh --detached`: the script starts the app
    in the background and exits 0. A unit reads that exit as the service
    ending and stops what is left in its cgroup - the app. So a clone keeps
    the desktop file, which nothing stops."""
    assert type(autostart.default_mechanism("linux", {}, session=True)) is autostart.XdgAutostart
    assert type(autostart.default_mechanism("linux", LINUX_RELEASE, session=True)) is autostart.SystemdUserUnit


def test_an_xdg_data_home_set_at_registration_is_written_into_the_unit(tmp_path):
    """AC #3. The user manager does not read `.profile`, so an
    `XDG_DATA_HOME` set there would send the background server to another
    library. The value at registration goes into the unit - quoted, because a
    path with a space in it is one `Environment=` word only inside quotes."""
    unit = _systemd(tmp_path, environ={"XDG_DATA_HOME": "/home/me/My Data"})
    autostart.enable(unit, command=AT_LOGIN)

    lines = unit.path.read_text(encoding="utf-8").splitlines()
    assert 'Environment="XDG_DATA_HOME=/home/me/My Data"' in lines

    plain = _systemd(tmp_path / "plain")
    autostart.enable(plain, command=AT_LOGIN)
    assert not [line for line in plain.path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Environment=")]

    odd = _systemd(tmp_path / "odd", environ={"XDG_DATA_HOME": '/d/100%/a"b\\c'})
    autostart.enable(odd, command=AT_LOGIN)
    assert r'Environment="XDG_DATA_HOME=/d/100%%/a\"b\\c"' in odd.path.read_text(encoding="utf-8").splitlines()


def test_the_unit_restarts_only_on_a_crash_and_gives_up_after_five_in_five_minutes(tmp_path):
    """AC #4. The launcher's contract (TASK-112.02): `APP_DIED` when the app
    died under it, 0 for a clean stop, another instance or a configuration
    problem. So no restart at all except on that one code, and five starts
    in five minutes is where systemd stops trying. The number is read from
    the launcher itself, so the two cannot drift apart."""
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=AT_LOGIN)
    parsed = _unit(unit)

    assert parsed["Service"]["Restart"] == "no"
    assert parsed["Service"]["RestartForceExitStatus"] == str(_launcher().APP_DIED)
    assert "RestartPreventExitStatus" not in parsed["Service"]
    assert "SuccessExitStatus" not in parsed["Service"]
    # The limit is a [Unit] setting since systemd 230; under [Service] it is ignored.
    assert parsed["Unit"]["StartLimitBurst"] == "5"
    assert parsed["Unit"]["StartLimitIntervalSec"] == "300"
    assert "StartLimitBurst" not in parsed["Service"]


def test_the_unit_starts_the_launcher_headless(tmp_path):
    """ADR-026's Must on the unit too: `ExecStart=` is the launcher with
    `--headless`, so no window opens at login."""
    unit = _systemd(tmp_path)
    command = autostart.start_command(environ=LINUX_RELEASE, platform="linux")
    autostart.enable(unit, command=command)

    assert _unit(unit)["Service"]["ExecStart"] == f"{APPIMAGE} --at-login --no-browser --headless"
    assert unit.read() == unit.render(command)


def test_exec_start_quotes_the_way_systemd_says(tmp_path):
    """`ExecStart=` has its own rules (systemd.service, "Command lines"):
    `%` is a specifier and `$` a variable everywhere on the line, so each is
    doubled; a backslash and a double quote are C-style escapes; a word with
    a space in it is one word only inside quotes."""
    unit = _systemd(tmp_path)

    assert unit.render(("/opt/MyScribe.AppImage", "--headless")) == "/opt/MyScribe.AppImage --headless"
    assert unit.render(("/opt/My Apps/MyScribe.AppImage",)) == '"/opt/My Apps/MyScribe.AppImage"'
    assert unit.render(("/opt/100%/x",)) == "/opt/100%%/x"
    assert unit.render(("/opt/a$b",)) == "/opt/a$$b"
    assert unit.render(("/opt/a\\b",)) == r'"/opt/a\\b"'
    assert unit.render(('/opt/a"b',)) == r'"/opt/a\"b"'


def test_a_systemctl_that_fails_or_hangs_is_an_os_error(tmp_path):
    """Settings and the setup engine catch OSError and show it. An enable the
    manager refused leaves the desktop file it would replace alone."""
    unit = _systemd(tmp_path)
    unit.legacy.write(AT_LOGIN)

    unit.run = lambda args: subprocess.CompletedProcess(args, 1, "", "Failed to connect to bus")
    with pytest.raises(OSError, match="Failed to connect to bus"):
        autostart.enable(unit, command=AT_LOGIN)
    assert unit.legacy.path.exists()

    def hang(args):
        raise subprocess.TimeoutExpired(args, 15)

    unit.run = hang
    # A unit file on disk, so `read()` gets as far as asking `is-enabled`.
    unit.path.write_text(unit.text(AT_LOGIN), encoding="utf-8")
    with pytest.raises(OSError):
        unit.read()


def test_the_real_runner_asks_the_users_own_manager_with_a_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    autostart.systemctl(["is-enabled", "myscribe.service"])

    assert seen["argv"] == ["systemctl", "--user", "is-enabled", "myscribe.service"]
    assert seen["kwargs"]["timeout"] > 0
    assert seen["kwargs"]["capture_output"] is True


# --- one entry, never two -----------------------------------------------------------


def test_switching_the_unit_on_or_off_takes_the_desktop_file_away_too(tmp_path):
    """One login, one MyScribe. A desktop file left beside the unit would
    start a second one; one left after "off" would start one the card says
    is not there."""
    unit = _systemd(tmp_path)
    unit.legacy.write(OLD)

    autostart.enable(unit, command=AT_LOGIN)
    assert not unit.legacy.path.exists()

    unit.legacy.write(OLD)
    autostart.disable(unit)
    assert not unit.legacy.path.exists() and not unit.path.exists()


def test_the_first_start_moves_this_launchers_desktop_file_into_the_unit(tmp_path):
    """An entry 0.9.1 or earlier wrote is a desktop file. With a user session
    the next start writes the unit, enables it and removes the file."""
    unit = _systemd(tmp_path)
    unit.legacy.write((APPIMAGE, "--at-login", "--no-browser"))

    entry = autostart.migrate(unit, command=AT_LOGIN, environ=LINUX_RELEASE)

    assert entry is not None and entry.on is True and entry.where == str(unit.path)
    assert not unit.legacy.path.exists()
    assert unit.read() == unit.render(AT_LOGIN)
    assert ("enable", "myscribe.service") in unit.run.calls


def test_migration_leaves_what_is_not_this_launchers_to_switch(tmp_path):
    """The guards of `refresh()`, kept: a desktop file the user switched off
    stays off and gets no unit; one naming another launcher is not ours; a
    clone moves nothing; and without a user session nothing moves."""
    hidden = _systemd(tmp_path / "hidden")
    hidden.legacy.folder.mkdir(parents=True)
    hidden.legacy.path.write_text(f"[Desktop Entry]\nExec={APPIMAGE} --at-login\nHidden=true\n", encoding="utf-8")
    assert autostart.migrate(hidden, command=AT_LOGIN, environ=LINUX_RELEASE) is None
    assert hidden.legacy.path.exists() and not hidden.path.exists()

    other = _systemd(tmp_path / "other")
    other.legacy.write(("/opt/Other.AppImage", "--at-login"))
    assert autostart.migrate(other, command=AT_LOGIN, environ=LINUX_RELEASE) is None
    assert other.legacy.path.exists() and not other.path.exists()

    clone = _systemd(tmp_path / "clone")
    clone.legacy.write((APPIMAGE, "--at-login"))
    assert autostart.migrate(clone, command=AT_LOGIN, environ={}) is None
    assert clone.legacy.path.exists()

    xdg = autostart.XdgAutostart(folder=tmp_path / "plain")
    xdg.write((APPIMAGE, "--at-login"))
    assert autostart.migrate(xdg, command=AT_LOGIN, environ=LINUX_RELEASE) is None
    assert xdg.path.exists()


def test_a_unit_already_on_takes_a_leftover_desktop_file_of_this_launcher_away(tmp_path):
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=AT_LOGIN)
    unit.legacy.write((APPIMAGE, "--at-login"))

    autostart.migrate(unit, command=AT_LOGIN, environ=LINUX_RELEASE)

    assert not unit.legacy.path.exists()
    assert unit.read() == unit.render(AT_LOGIN)


def test_the_start_migrates_before_it_refreshes_and_logs_it(monkeypatch):
    from scribe import app as app_module

    entry = autostart.Entry(where="/home/me/.config/systemd/user/myscribe.service", command="x", on=True)
    order: list[str] = []
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(autostart, "migrate", lambda *_a, **_k: order.append("migrate") or entry)
    monkeypatch.setattr(autostart, "refresh", lambda *_a, **_k: order.append("refresh") or None)
    monkeypatch.setattr(app_module.applog, "log", lambda event, **fields: logged.append((event, fields)))

    app_module._refresh_login_entry()

    assert order == ["migrate", "refresh"]
    assert logged == [("autostart.migrated", {"where": entry.where, "command": entry.command})]


# --- the card tells the truth about the desktop file the unit replaces --------------


def test_a_desktop_file_the_unit_did_not_take_over_reads_as_on(tmp_path):
    """Review of TASK-112.06. A desktop file naming another launcher path - an
    AppImage renamed off the release name, or one in another folder (TASK-119
    takes over only a release name in the same folder) - is not moved into
    the unit, and the desktop still starts it at login. The card has to
    say on, and where, or a user who believes it never flips the switch that
    would take the file away. Off then removes it; a hidden one stays off."""
    unit = _systemd(tmp_path)
    renamed = ("/home/me/Apps/MyScribe-0.9.0.AppImage", "--at-login", "--no-browser")
    unit.legacy.write(renamed)
    assert autostart.migrate(unit, command=AT_LOGIN, environ=LINUX_RELEASE) is None

    shown = autostart.status(unit, command=AT_LOGIN)

    assert shown.on is True
    assert shown.where == str(unit.legacy.path)
    assert shown.command == unit.legacy.render(renamed)

    off = autostart.disable(unit, command=AT_LOGIN)
    assert off.on is False and off.where == str(unit.path)
    assert not unit.legacy.path.exists()

    unit.legacy.folder.mkdir(parents=True, exist_ok=True)
    unit.legacy.path.write_text(f"[Desktop Entry]\nExec={APPIMAGE} --at-login\nHidden=true\n", encoding="utf-8")
    assert autostart.status(unit, command=AT_LOGIN).on is False


def test_a_migration_the_manager_refused_leaves_no_unit_and_the_card_on(tmp_path):
    """Review of TASK-112.06. `systemctl --user enable` fails during the
    migration: the desktop file still starts MyScribe at login, so the card
    says on and names it, and no disabled unit file is left behind."""
    unit = _systemd(tmp_path)
    unit.legacy.write((APPIMAGE, "--at-login", "--no-browser"))
    fake = unit.run

    def refuse_enable(args):
        if args[0] == "enable":
            fake.calls.append(tuple(args))
            return subprocess.CompletedProcess(args, 1, "", "Failed to enable unit")
        return fake(args)

    unit.run = refuse_enable
    with pytest.raises(OSError, match="Failed to enable unit"):
        autostart.migrate(unit, command=AT_LOGIN, environ=LINUX_RELEASE)

    assert not unit.path.exists()
    assert unit.legacy.path.exists()
    shown = autostart.status(unit, command=AT_LOGIN)
    assert shown.on is True and shown.where == str(unit.legacy.path)


def test_a_rewrite_the_manager_refused_keeps_the_unit_that_was_on(tmp_path):
    """The rollback is only for a file this write created: a unit that was
    already on and is being rewritten keeps its file, so a failed refresh
    does not switch the entry off."""
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=(APPIMAGE, "--at-login", "--no-browser"))
    fake = unit.run
    unit.run = lambda args: (
        subprocess.CompletedProcess(args, 1, "", "Failed to enable unit") if args[0] == "enable" else fake(args)
    )

    with pytest.raises(OSError):
        autostart.enable(unit, command=AT_LOGIN)

    assert unit.path.exists()


# --- macOS: a LaunchAgent that starts the binary itself (TASK-112.05) -------------
#
# Robert's decisions of 2026-10-24: the agent names Contents/MacOS/<binary>,
# headless, with its own log; KeepAlive restarts a non-zero exit, which the
# launcher means for APP_DIED alone (an uncaught error exits 1); a path under
# AppTranslocation is refused with a sentence; switching on bootstraps it now,
# switching off removes the plist and boots it out, which stops MyScribe now.
# Every launchctl call goes through an injected runner. Nothing in this
# section has met a real launchd, launchctl or App Translocation - there is
# no Mac here. What it proves is the plist (through plistlib), the argv each
# switch hands launchctl, and the order of the steps.

BUILD_RELEASE = Path(__file__).resolve().parent.parent / "packaging" / "build_release.py"
BUNDLE = "/Applications/MyScribe.app"
MAC_RELEASE = {autostart.LAUNCHER_VARIABLE: BUNDLE}
MAC_COMMAND = (f"{BUNDLE}/Contents/MacOS/MyScribe", "--at-login", "--no-browser", "--headless")
TRANSLOCATED = "/private/var/folders/xy/abc123/T/AppTranslocation/0F1E2D3C-4B5A-6978/d/MyScribe.app"


def _build_constant(name: str):
    """One top-level constant of `packaging/build_release.py`, read with `ast`
    rather than imported: the build script is not part of the app."""
    tree = ast.parse(BUILD_RELEASE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a constant in {BUILD_RELEASE}")


def _plist(agent) -> dict:
    return plistlib.loads(agent.path.read_bytes())


def test_the_launch_agent_plist_has_the_research_notes_keys(tmp_path):
    """AC #1: the plist of docs/reports/2026-10-19-background-server-research.md
    section 2.2, key by key, parsed back with plistlib. `ExitTimeOut` has its
    own test below: Robert's decision put it above the note's 40."""
    agent = _agent(tmp_path)

    autostart.enable(agent, command=MAC_COMMAND)
    plist = _plist(agent)

    log = str(tmp_path / "Library" / "Logs" / "MyScribe" / "launchd.log")
    assert plist == {
        "Label": autostart.LABEL,
        "ProgramArguments": list(MAC_COMMAND),
        "AssociatedBundleIdentifiers": [_build_constant("BUNDLE_ID")],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "ExitTimeOut": autostart.EXIT_TIMEOUT,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    assert autostart.LABEL == _build_constant("BUNDLE_ID")
    assert agent.read() == shlex.join(MAC_COMMAND)


def test_keep_alive_restarts_only_the_exit_that_means_the_app_died():
    """`SuccessfulExit: false` restarts every non-zero exit. The one the
    launcher chooses is `APP_DIED` (TASK-112.02): a clean stop, another
    instance and a busy port all exit 0. An uncaught Python error in the
    launcher exits 1 and is restarted as well - launchd has no start limit,
    only its 10 s throttle. The module spells the code again; this reads the
    launcher's own."""
    assert autostart.APP_DIED == _launcher().APP_DIED != 0


def test_the_launcher_knows_the_unit_and_the_job_by_the_names_written_here():
    """TASK-112.10, ADR-037: the launcher restarts a crashed app only where no
    OS supervisor watches it, and tells the systemd unit and the LaunchAgent
    by these names. It spells them again, like `APP_DIED`; this holds them
    to the module's own."""
    launcher = _launcher()
    assert launcher.SUPERVISED_UNIT == autostart.UNIT_NAME
    assert launcher.SUPERVISED_LABEL == autostart.LABEL


def test_the_plist_waits_longer_than_the_launchers_longest_stop():
    """Robert's decision: launchd's SIGKILL must not come before the
    launcher's own stop has run out - killed sooner, the launcher exits
    non-zero, which KeepAlive reads as a crash. `LAUNCHER_STOP_WAIT` is read
    from the launcher, so a branch that raises it (TASK-112.09's open branch
    takes it from 40 to 207 s) turns this red instead of drifting past it.
    The 10 s of margin for launchd's own bookkeeping is an estimate, not a
    measurement."""
    assert autostart.EXIT_TIMEOUT >= _launcher().LAUNCHER_STOP_WAIT + 10


def test_the_log_folder_is_made_before_launchd_needs_it(tmp_path):
    """launchd opens `StandardOutPath` itself and does not make the folder
    above it. It sits beside `LaunchAgents` - `~/Library/Logs/MyScribe` on a
    Mac - so a test that owns the one owns the other."""
    agent = _agent(tmp_path)

    agent.write(MAC_COMMAND)

    assert (tmp_path / "Library" / "Logs" / "MyScribe").is_dir()
    assert autostart.default_mechanism("darwin").log == Path.home() / "Library" / "Logs" / "MyScribe" / "launchd.log"


def test_an_open_a_plist_from_0_9_is_rewritten_to_the_binary(tmp_path):
    """The next start brings a 0.9.x plist - `open -a <bundle> --args ...`,
    three keys - up to the binary and the full set of keys, and asks launchctl
    nothing: the job it would reload may be the very app that is starting."""
    agent = _agent(tmp_path)
    agent.folder.mkdir(parents=True)
    agent.path.write_bytes(plistlib.dumps({
        "Label": autostart.LABEL,
        "ProgramArguments": ["/usr/bin/open", "-a", BUNDLE, "--args", "--at-login", "--no-browser", "--headless"],
        "RunAtLoad": True,
    }))

    command = autostart.start_command(MAC_RELEASE, platform="darwin")
    entry = autostart.refresh(agent, environ=MAC_RELEASE, command=command)

    assert entry is not None
    assert _plist(agent)["ProgramArguments"] == list(MAC_COMMAND)
    assert _plist(agent)["KeepAlive"] == {"SuccessfulExit": False}
    assert agent.run.calls == []


def test_a_plist_with_todays_arguments_but_older_keys_is_rewritten(tmp_path):
    """refresh() compared only the command, so a key added later never reached
    a plist that already ran the right binary. The LaunchAgent compares the
    whole plist it would write; one that already matches is left alone."""
    agent = _agent(tmp_path)
    agent.folder.mkdir(parents=True)
    agent.path.write_bytes(plistlib.dumps({
        "Label": autostart.LABEL, "ProgramArguments": list(MAC_COMMAND), "RunAtLoad": True,
    }))

    assert autostart.refresh(agent, environ=MAC_RELEASE, command=MAC_COMMAND) is not None
    assert _plist(agent)["ExitTimeOut"] == autostart.EXIT_TIMEOUT

    written = agent.path.read_bytes()
    assert autostart.refresh(agent, environ=MAC_RELEASE, command=MAC_COMMAND) is None
    assert agent.path.read_bytes() == written


def test_a_bundle_path_with_a_space_still_matches_its_own_plist(tmp_path):
    """The guard "names this same launcher" compares paths, not shell text:
    `shlex.join` quotes a path with a space, and the quoted bundle is not a
    substring of the quoted binary inside it."""
    bundle = "/Users/me/My Apps/MyScribe.app"
    environ = {autostart.LAUNCHER_VARIABLE: bundle}
    agent = _agent(tmp_path)
    agent.folder.mkdir(parents=True)
    agent.path.write_bytes(plistlib.dumps({
        "Label": autostart.LABEL,
        "ProgramArguments": [f"{bundle}/Contents/MacOS/MyScribe", "--at-login"],
        "RunAtLoad": True,
    }))

    assert autostart.refresh(agent, environ=environ, command=autostart.start_command(environ, platform="darwin"))
    assert _plist(agent)["ProgramArguments"] == [f"{bundle}/Contents/MacOS/MyScribe", "--at-login",
                                                 "--no-browser", "--headless"]

    other = {autostart.LAUNCHER_VARIABLE: "/Users/me/My Apps/MyScribe 2.app"}
    assert autostart.refresh(agent, environ=other, command=autostart.start_command(other, platform="darwin")) is None


def test_a_sibling_bundle_that_shares_the_prefix_is_not_this_launcher(tmp_path):
    """The guard is a path boundary, not a string prefix: a plist for
    `/Applications/MyScribe.app.old/...` starts with `/Applications/MyScribe.app`
    as text, but it is another install, and a start of this one must leave it
    exactly as it is (TASK-112.05 review)."""
    agent = _agent(tmp_path)
    agent.folder.mkdir(parents=True)
    agent.path.write_bytes(plistlib.dumps({
        "Label": autostart.LABEL,
        "ProgramArguments": [f"{BUNDLE}.old/Contents/MacOS/MyScribe", "--at-login"],
        "RunAtLoad": True,
    }))
    held = agent.path.read_bytes()

    assert agent.names(BUNDLE) is False
    assert agent.names(f"{BUNDLE}.old") is True
    assert autostart.refresh(agent, environ=MAC_RELEASE, command=MAC_COMMAND) is None
    assert agent.path.read_bytes() == held


def test_a_translocated_app_is_not_registered_and_says_why(tmp_path):
    """AC #2. An app opened from the dmg or Downloads with the quarantine flag
    runs from a random read-only path under AppTranslocation that is gone at
    the next login (ADR-026's Must Not). Nothing is written, and the reason
    is a sentence a user can act on, not "nothing to start"."""
    environ = {autostart.LAUNCHER_VARIABLE: TRANSLOCATED}

    assert autostart.start_command(environ=environ, platform="darwin") is None
    reason = autostart.refusal(environ=environ, platform="darwin")
    assert reason is not None
    assert "Applications" in reason and "temporary" in reason

    agent = _agent(tmp_path)
    with pytest.raises(autostart.NothingToStart, match="Applications"):
        autostart.enable(agent, command=None, environ=environ, platform="darwin")
    assert not agent.path.exists() and agent.run.calls == []

    off = autostart.status(agent, environ=environ, platform="darwin")
    assert off.on is False and off.command == "" and off.refused == reason

    # Only a Mac path, and only that folder.
    assert autostart.refusal(environ=MAC_RELEASE, platform="darwin") is None
    assert autostart.refusal(environ=environ, platform="linux") is None


def test_enable_writes_the_plist_and_asks_launchctl_nothing(tmp_path):
    """TASK-112.05 review. `enable()` is the one call both doors make
    (TASK-089.22 AC2), and the first-start sitting makes it before anything
    serves: a bootstrap there would let RunAtLoad start a second MyScribe
    beside the one the launcher is about to start. So writing loads nothing;
    the plist loads at the next login unless `load_now()` is asked."""
    agent = _agent(tmp_path, uid=501)

    entry = autostart.enable(agent, command=MAC_COMMAND)

    assert agent.path.exists() and entry.on is True
    assert agent.run.calls == []


def test_load_now_bootstraps_the_agent(tmp_path):
    """AC #3, the Settings switch: the plist first, then `launchctl bootstrap
    gui/<uid> <plist>`, so it is loaded in this session and not only at the
    next login. No bootout first: the job a bootout hits may be the app
    answering this request. A second launcher started by RunAtLoad finds this
    one serving and exits 0, which KeepAlive leaves alone (TASK-112.02)."""
    agent = _agent(tmp_path, uid=501)
    autostart.enable(agent, command=MAC_COMMAND)

    note = autostart.load_now(agent)

    assert agent.run.calls == [(("bootstrap", "gui/501", str(agent.path)), True)]
    assert note == ""


def test_load_now_is_nothing_where_the_os_has_no_such_step(tmp_path):
    """A Run value, a desktop file and a user unit have nothing to load now:
    the unit is enabled, not started (the app writing it is already running)."""
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=AT_LOGIN)
    asked = list(unit.run.calls)

    assert autostart.load_now(unit) == ""
    assert unit.run.calls == asked
    assert autostart.load_now(autostart.XdgAutostart(folder=tmp_path / "a")) == ""


def test_a_bootstrap_launchd_refuses_keeps_the_file_and_says_so(tmp_path):
    """launchd answers an agent that is already loaded with an error, and its
    error numbers are not documented. Either way the plist is loaded at the
    next login, so the file stays, the switch is on, and the note carries
    what launchctl said for Settings to show."""
    agent = _agent(tmp_path)
    agent.run.refuse = subprocess.CompletedProcess((), 5, "", "Bootstrap failed: 5: Input/output error")

    entry = autostart.enable(agent, command=MAC_COMMAND)
    note = autostart.load_now(agent)

    assert agent.path.exists() and entry.on is True
    assert "Input/output error" in note and "next login" in note

    def hang(args, *, wait=True):
        raise subprocess.TimeoutExpired(args, 15)

    agent.run = hang
    assert "next login" in autostart.load_now(agent)

    def missing(args, *, wait=True):
        raise FileNotFoundError("/bin/launchctl")

    agent.run = missing
    assert "next login" in autostart.load_now(agent)


def test_switching_off_removes_the_plist_then_boots_out_without_waiting(tmp_path):
    """AC #3, Robert's decision: off means off now. The plist goes first -
    bootout stops the launcher, which stops this app, and a step after that
    would never run. The bootout itself is not waited for: it waits for the
    job to stop, the job is this app, and this app is blocked in the request
    that asked. A second off asks launchctl nothing."""
    agent = _agent(tmp_path, uid=501)
    autostart.enable(agent, command=MAC_COMMAND)
    real = agent.run
    real.calls.clear()
    gone_before_bootout = []

    def watch(args, *, wait=True):
        gone_before_bootout.append(not agent.path.exists())
        return real(args, wait=wait)

    agent.run = watch

    off = autostart.disable(agent, command=MAC_COMMAND)

    assert off.on is False and not agent.path.exists()
    assert real.calls == [(("bootout", f"gui/501/{autostart.LABEL}"), False)]
    assert gone_before_bootout == [True]

    real.calls.clear()
    autostart.disable(agent, command=MAC_COMMAND)
    assert real.calls == []


def test_a_bootout_that_cannot_be_started_still_leaves_the_switch_off(tmp_path):
    """TASK-112.05 review. The plist is already gone when launchctl is asked,
    so a launchctl that cannot even be started (OSError from Popen) does not
    make the switch fail: it is off, and the note says the running MyScribe
    was not stopped now."""
    agent = _agent(tmp_path)
    autostart.enable(agent, command=MAC_COMMAND)

    def broken(args, *, wait=True):
        raise PermissionError(13, "Permission denied", "/bin/launchctl")

    agent.run = broken

    off = autostart.disable(agent, command=MAC_COMMAND)

    assert off.on is False and not agent.path.exists()
    assert "Permission denied" in off.note and "log out" in off.note


def test_a_plist_that_will_not_parse_is_still_switched_off(tmp_path):
    """A corrupt plist (plistlib raises ValueError) was removable before
    disable() read it first. It still is: it is removed, and launchd is
    asked to boot out whatever it loaded under the label."""
    agent = _agent(tmp_path, uid=501)
    agent.folder.mkdir(parents=True)
    agent.path.write_bytes(b"not a plist at all")

    off = autostart.disable(agent, command=MAC_COMMAND)

    assert off.on is False and not agent.path.exists()
    assert agent.run.calls == [(("bootout", f"gui/501/{autostart.LABEL}"), False)]


def test_only_the_launch_agent_says_switching_off_stops_myscribe(tmp_path):
    """The card's warning comes from the entry. A Windows Run value, a desktop
    file and a disabled user unit stop nothing that is running."""
    assert autostart.status(_agent(tmp_path), command=MAC_COMMAND).stops_now is True
    assert autostart.status(_systemd(tmp_path), command=AT_LOGIN).stops_now is False
    assert autostart.status(autostart.XdgAutostart(folder=tmp_path / "a"), command=AT_LOGIN).stops_now is False


def test_the_real_launchctl_runner_waits_with_a_timeout_or_lets_go(monkeypatch):
    """`/bin/launchctl`, never a `launchctl` found on PATH. Waited for with a
    timeout, or started in a session of its own and let go, so the app's
    process-group stop does not take the bootout down with it."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["run"] = (argv, kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_popen(argv, **kwargs):
        seen["popen"] = (argv, kwargs)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    monkeypatch.setattr(autostart.subprocess, "Popen", fake_popen)

    autostart.launchctl(["bootstrap", "gui/501", "/x.plist"])
    autostart.launchctl(["bootout", "gui/501/x"], wait=False)

    argv, kwargs = seen["run"]
    assert argv == ["/bin/launchctl", "bootstrap", "gui/501", "/x.plist"]
    assert kwargs["timeout"] > 0 and kwargs["capture_output"] is True
    argv, kwargs = seen["popen"]
    assert argv == ["/bin/launchctl", "bootout", "gui/501/x"]
    assert kwargs["start_new_session"] is True


# --- an AppImage update is a new file in the same folder (TASK-119) ----------------
#
# Robert, 2026-10-24: on Linux an entry whose command starts
# MyScribe-<anything>-linux-x64.AppImage in the folder of the running
# $APPIMAGE is ours, and the start rewrites it - the desktop file and the
# user unit alike. Windows and macOS keep the strict rule. The card says when
# the entry starts another file than the one running.

APPS = "/home/me/Apps"
OLD_APPIMAGE = f"{APPS}/MyScribe-0.9.0-linux-x64.AppImage"
NEW_APPIMAGE = f"{APPS}/MyScribe-0.9.1-linux-x64.AppImage"
OLD_AT_LOGIN = (OLD_APPIMAGE, "--at-login", "--no-browser", "--headless")
NEW_AT_LOGIN = (NEW_APPIMAGE, "--at-login", "--no-browser", "--headless")
NEW_RELEASE = {autostart.LAUNCHER_VARIABLE: NEW_APPIMAGE}


def test_an_appimage_update_in_the_same_folder_takes_the_desktop_entry_over(tmp_path):
    mechanism = _CountingXdg(tmp_path / "autostart")
    mechanism.write(OLD_AT_LOGIN)

    entry = autostart.refresh(mechanism, command=NEW_AT_LOGIN, environ=NEW_RELEASE)

    assert entry is not None and entry.on is True
    assert mechanism.read() == mechanism.render(NEW_AT_LOGIN)
    assert mechanism.writes == 2


def test_an_appimage_update_in_the_same_folder_rewrites_the_user_unit(tmp_path):
    unit = _systemd(tmp_path)
    autostart.enable(unit, command=OLD_AT_LOGIN)
    unit.run.calls.clear()

    entry = autostart.refresh(unit, command=NEW_AT_LOGIN, environ=NEW_RELEASE)

    assert entry is not None and entry.where == str(unit.path)
    assert unit.read() == unit.render(NEW_AT_LOGIN)
    assert [verb for verb in unit.run.verbs() if verb != "is-enabled"] == ["daemon-reload", "enable"]


def test_the_first_start_of_an_update_moves_the_older_desktop_file_into_the_unit(tmp_path):
    """0.9.0 wrote a desktop file; 0.9.1, started from the same folder with a
    user session, moves it into a unit that starts 0.9.1."""
    unit = _systemd(tmp_path)
    unit.legacy.write(OLD_AT_LOGIN)

    entry = autostart.migrate(unit, command=NEW_AT_LOGIN, environ=NEW_RELEASE)

    assert entry is not None and entry.where == str(unit.path)
    assert not unit.legacy.path.exists()
    assert unit.read() == unit.render(NEW_AT_LOGIN)


@pytest.mark.parametrize(
    "program",
    [
        "/home/me/Elsewhere/MyScribe-0.9.0-linux-x64.AppImage",  # another folder
        f"{APPS}/Other-1.0-linux-x64.AppImage",  # not MyScribe
        f"{APPS}/MyScribe-0.9.0-linux-x64.AppImage.bak",  # not the release name
        f"{APPS}/MyScribe-linux-x64.AppImage",  # no version between the dashes
        f"{APPS}/sub/MyScribe-0.9.0-linux-x64.AppImage",  # a folder below
    ],
)
def test_an_entry_that_is_not_a_sibling_appimage_is_left_alone(tmp_path, program):
    xdg = _CountingXdg(tmp_path / "autostart")
    xdg.write((program, "--at-login"))
    assert autostart.refresh(xdg, command=NEW_AT_LOGIN, environ=NEW_RELEASE) is None
    assert xdg.writes == 1

    unit = _systemd(tmp_path / "unit")
    autostart.enable(unit, command=(program, "--at-login"))
    assert autostart.refresh(unit, command=NEW_AT_LOGIN, environ=NEW_RELEASE) is None
    assert unit.read() == unit.render((program, "--at-login"))

    legacy = _systemd(tmp_path / "legacy")
    legacy.legacy.write((program, "--at-login"))
    assert autostart.migrate(legacy, command=NEW_AT_LOGIN, environ=NEW_RELEASE) is None
    assert legacy.legacy.path.exists() and not legacy.path.exists()


def test_only_the_program_counts_not_a_sibling_named_further_along(tmp_path):
    """The sibling has to be what the entry starts, not an argument to it."""
    xdg = _CountingXdg(tmp_path / "autostart")
    xdg.write(("/usr/bin/env", OLD_APPIMAGE, "--at-login"))

    assert autostart.refresh(xdg, command=NEW_AT_LOGIN, environ=NEW_RELEASE) is None
    assert xdg.writes == 1


def test_the_sibling_rule_is_linux_only(mechanism):
    """The same two names on Windows and macOS: the ownership rule there stays
    "names this same launcher", so neither entry is touched."""
    mechanism.write(OLD_AT_LOGIN)

    entry = autostart.refresh(mechanism, command=NEW_AT_LOGIN, environ=NEW_RELEASE)

    linux = isinstance(mechanism, (autostart.XdgAutostart, autostart.SystemdUserUnit))
    assert (entry is not None) is linux
    assert mechanism.read() == mechanism.render(NEW_AT_LOGIN if linux else OLD_AT_LOGIN)


@pytest.mark.parametrize("kind", ["xdg", "systemd"])
def test_a_folder_name_that_needs_quoting_is_still_recognised(tmp_path, kind):
    """Each Linux mechanism quotes its own way; the folder is read back through
    that quoting before it is compared."""
    folder = '/home/me/My "Apps" $5 100% \\ `x`'
    old = (f"{folder}/MyScribe-0.9.0-linux-x64.AppImage", "--at-login")
    new = (f"{folder}/MyScribe-0.9.1-linux-x64.AppImage", "--at-login", "--headless")
    mechanism = _CountingXdg(tmp_path / "autostart") if kind == "xdg" else _systemd(tmp_path)
    autostart.enable(mechanism, command=old)

    entry = autostart.refresh(mechanism, command=new, environ={autostart.LAUNCHER_VARIABLE: new[0]})

    assert entry is not None
    assert mechanism.read() == mechanism.render(new)


def test_status_says_when_the_entry_starts_another_file_than_the_one_running(tmp_path):
    """Robert's option C: the card names the case the rule does not reach."""
    xdg = autostart.XdgAutostart(folder=tmp_path / "autostart")
    xdg.write(("/home/me/Elsewhere/MyScribe-0.9.0-linux-x64.AppImage", "--at-login"))
    assert autostart.status(xdg, command=NEW_AT_LOGIN, environ=NEW_RELEASE).elsewhere is True

    # A sibling the start did not rewrite yet - it failed, say - is another
    # file too: the line asks the strict question.
    xdg.write(OLD_AT_LOGIN)
    assert autostart.status(xdg, command=NEW_AT_LOGIN, environ=NEW_RELEASE).elsewhere is True

    xdg.write(NEW_AT_LOGIN)
    assert autostart.status(xdg, command=NEW_AT_LOGIN, environ=NEW_RELEASE).elsewhere is False
    # A clone has no launcher to compare with, and an entry that is off
    # starts nothing.
    assert autostart.status(xdg, command=NEW_AT_LOGIN, environ={}).elsewhere is False
    xdg.remove()
    assert autostart.status(xdg, command=NEW_AT_LOGIN, environ=NEW_RELEASE).elsewhere is False

    unit = _systemd(tmp_path / "unit")
    unit.legacy.write(("/home/me/Elsewhere/MyScribe-0.9.0-linux-x64.AppImage", "--at-login"))
    shown = autostart.status(unit, command=NEW_AT_LOGIN, environ=NEW_RELEASE)
    assert shown.on is True and shown.where == str(unit.legacy.path) and shown.elsewhere is True


# --- a Mac copy running from App Translocation (ADR-026 Must Not) ------------------
#
# TASK-119 and TASK-112.05 each refused a translocated copy; the merge keeps
# one mechanism: `start_command()` names nothing for it, `refusal()` says why,
# and `enable()` raises `Translocated` - for that, and for a command handed in
# that names a translocated path anyway. TRANSLOCATED is the path above.

APPLICATIONS = "/Applications/MyScribe.app"


def test_a_copy_running_from_app_translocation_is_never_registered(tmp_path):
    """ADR-026 Must Not: register a macOS path under `AppTranslocation`. The
    switch refuses with the way out, and writes nothing - whether the command
    comes from `start_command()` or is handed in."""
    agent = _agent(tmp_path)
    environ = {autostart.LAUNCHER_VARIABLE: TRANSLOCATED}
    assert autostart.start_command(environ=environ, platform="darwin") is None

    with pytest.raises(autostart.Translocated) as refused:
        autostart.enable(agent, environ=environ, platform="darwin")
    assert isinstance(refused.value, autostart.NothingToStart), "the settings route answers it with a 409"
    assert "Applications" in str(refused.value)

    handed_in = (f"{TRANSLOCATED}/Contents/MacOS/MyScribe", "--at-login", "--no-browser", "--headless")
    with pytest.raises(autostart.Translocated, match="Applications"):
        autostart.enable(agent, command=handed_in)

    assert agent.read() is None and not agent.path.exists() and agent.run.calls == []
    assert autostart.translocated(TRANSLOCATED) is True
    assert autostart.translocated(APPLICATIONS) is False
    assert autostart.translocated(None) is False


def test_a_translocated_copy_can_still_switch_an_entry_off_and_a_moved_one_on(tmp_path):
    """Off removes the /Applications entry, which harms nothing; the same app
    moved into Applications registers as before."""
    agent = _agent(tmp_path)
    applications = autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: APPLICATIONS}, platform="darwin")
    assert autostart.start_command(environ={autostart.LAUNCHER_VARIABLE: TRANSLOCATED}, platform="darwin") is None

    autostart.enable(agent, command=applications)
    assert agent.read() is not None and APPLICATIONS in agent.read()

    assert autostart.disable(agent, command=applications).on is False
    assert agent.read() is None


def test_a_launch_agent_naming_the_binary_inside_the_running_bundle_is_not_elsewhere(tmp_path):
    """Merge of TASK-119 into TASK-112.05: the plist names
    `<bundle>/Contents/MacOS/MyScribe`, while the launcher is the bundle. The
    card's `elsewhere` asks the agent by path, so the bundle that is running
    is not "another file" - with a space in its name too - and another bundle is."""
    for bundle in (APPLICATIONS, "/Users/me/My Apps/MyScribe.app"):
        agent = _agent(tmp_path / bundle.replace("/", "_").replace(" ", "-"))
        environ = {autostart.LAUNCHER_VARIABLE: bundle}
        autostart.enable(agent, command=autostart.start_command(environ=environ, platform="darwin"))

        assert autostart.status(agent, environ=environ, platform="darwin").elsewhere is False
        other = {autostart.LAUNCHER_VARIABLE: "/Users/me/Other/MyScribe.app"}
        assert autostart.status(agent, environ=other, platform="darwin").elsewhere is True
