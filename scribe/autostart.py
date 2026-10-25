"""Start MyScribe when this user logs in: one entry, read back from the OS.

Functional, not a convenience. Watch folders and feed subscriptions only do
work while the app runs - the Watcher and the FeedWatcher are threads the
lifespan starts (`scribe/app.py`), and a feed is polled on a due date
(ADR-008). A user who set up a watch folder and then rebooted has a folder
nobody watches until somebody remembers to start MyScribe.

Three mechanisms, one per OS, each writing inside this user's own profile and
nothing that needs an administrator:

* **Windows** - a value named `MyScribe` under this user's own Run key. Not a
  shortcut in the Startup folder: a `.lnk` needs COM to write, and a shortcut
  is the thing R3 declined. A value is one string, visible in regedit, and
  gone when it is deleted.
* **macOS** - a launchd agent in this user's own `Library` that starts the
  launcher's own binary, so launchd restarts a crash and keeps its output in
  `~/Library/Logs/MyScribe` (TASK-112.05). The Settings switch loads it now
  (`load_now`) and switching it off unloads it now, which stops a MyScribe it
  started. The first-start setup only writes it: it loads at the next login.
* **Linux** - a systemd user unit in this user's own config directory where a
  systemd user session runs, so a crash is restarted and the output lands in
  the journal (ADR-026). Where none runs, and for a clone, a desktop entry in
  the same directory, as the freedesktop Autostart spec describes.

The Windows one has been run against its real OS API, and the user unit
against a real systemd 255 user manager in WSL (TASK-112.06) - not a
graphical desktop login. The LaunchAgent and the desktop entry are built from
Apple's launchd keys and the freedesktop specs and have been honoured by no
real login anywhere - no launchd and no `launchctl` has run what this module
writes or asks; every place that mentions them says so.

`status()` asks the OS every time. Nothing is remembered: a user who deleted
the value or the file gets a switch that says off, which a stored row could
not do. `disable()` removes exactly the one value or file this module wrote,
and removing what is not there is not an error - an uninstall and a second
click both reach that state.

What the entry starts is `start_command()`, and where nothing on this machine
is safe to name it returns None and `enable()` refuses. A path that is wrong
at the next login is worse than no entry at all.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import dataclasses
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "MyScribe"
# What a release launcher tells the app about itself. Set by the launcher's
# `app_environment`; absent means this is a clone started some other way.
LAUNCHER_VARIABLE = "MYSCRIBE_LAUNCHER"
# And its pid. The app's parent is not always the launcher: on Windows it is
# uv's trampoline, env/Scripts/python.exe, which starts the real python as
# its own child (TASK-117).
LAUNCHER_PID_VARIABLE = "MYSCRIBE_LAUNCHER_PID"
# Under this user's own hive, never the machine's.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# The identifier the release build gives the Mac bundle
# (`packaging/build_release.py`, BUNDLE_ID). Spelled again rather than
# imported: that script is not shipped with the app.
LABEL = "io.github.rvdbreemen.myscribe"
DESKTOP_NAME = "myscribe.desktop"
UNIT_NAME = "myscribe.service"
# The launcher's `APP_DIED`: the one exit that means the app died under it
# (TASK-112.02). Spelled again rather than imported, like LABEL: the launcher
# is not shipped inside the app. A test reads the launcher's own value.
APP_DIED = 70
# The binary inside the Mac bundle, `Contents/MacOS/<this>`
# (`packaging/build_release.py`, FROZEN_NAME). Spelled again, like LABEL; a
# test reads the build script's own value.
FROZEN_NAME = "MyScribe"
# How long launchd waits after its SIGTERM before it sends SIGKILL. Longer
# than the launcher's own longest stop (`LAUNCHER_STOP_WAIT`, 40 s on main and
# 207 s on TASK-112.09's branch), or a stop that is still running is killed
# and exits non-zero, which KeepAlive reads as a crash. A test reads the
# launcher's value and asks for 10 s on top. Only a hung launcher ever waits
# this long: one that stops sooner is not held up by it.
EXIT_TIMEOUT = 240
LAUNCHD_LOG = "launchd.log"
# Where macOS runs a quarantined app opened from a dmg or Downloads: a random,
# read-only copy that is gone after the next login (ADR-026's Must Not).
TRANSLOCATION = "/AppTranslocation/"
TRANSLOCATED = (
    "MyScribe is running from a temporary copy macOS made when it was opened from the "
    "disk image or the Downloads folder, and that copy is gone after the next login. "
    "Drag MyScribe to the Applications folder in Finder, open it from there, and switch "
    "this on again."
)
# Five starts in five minutes, then systemd stops trying (TASK-112.06 AC #4).
START_LIMIT_BURST = 5
START_LIMIT_INTERVAL_SEC = 300
# Long enough for a user manager that is slow to answer, short enough that a
# Settings page waiting on it still loads. launchctl gets the same.
SYSTEMCTL_TIMEOUT = 15
LAUNCHCTL_TIMEOUT = SYSTEMCTL_TIMEOUT


class NothingToStart(RuntimeError):
    """Nothing on this machine is known to start MyScribe again at login."""


@dataclass(frozen=True)
class Entry:
    """One login entry: where it lives, what it runs, and whether it is there.

    `command` is the whole command, exactly as the OS holds it - or, while the
    entry is off, exactly what switching it on would write. Settings prints
    both, because "on" is the one thing a user cannot check.
    """

    where: str
    command: str
    on: bool
    # What the OS said when a switch half-worked (TASK-112.05): the entry is
    # off, but launchctl could not be asked to stop what launchd started.
    # Empty otherwise.
    note: str = ""
    # Why there is nothing to register, when that is known better than
    # "nothing to start": an app running from a translocated copy.
    refused: str = ""
    # Switching this entry off also stops a MyScribe it started - the
    # LaunchAgent's bootout. Settings warns before the click.
    stops_now: bool = False


# --- the mechanisms ----------------------------------------------------------------


class WindowsRun:
    """A named value under this user's own Run key.

    `key` is injectable so a test can drive the whole lifecycle against a
    scratch key of its own rather than the one a real login reads.
    """

    def __init__(self, key: str = RUN_KEY, name: str = APP_NAME):
        self.key = key
        self.name = name

    @property
    def hive(self):
        """This user's own hive.

        `winreg` is imported here and not at the top of the file: it is
        Windows-only stdlib, and this module is imported by the settings page,
        so a top-level import would take the whole app down on a Mac.
        """
        import winreg

        return winreg.HKEY_CURRENT_USER

    @property
    def where(self) -> str:
        return rf"HKCU\{self.key}\{self.name}"

    def render(self, command) -> str:
        """One command line, quoted the way Windows quotes one, so a path with
        a space in it survives the next login."""
        return subprocess.list2cmdline([str(part) for part in command])

    def read(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(self.hive, self.key) as key:
                value, _kind = winreg.QueryValueEx(key, self.name)
        except FileNotFoundError:
            return None
        return str(value)

    def write(self, command) -> None:
        import winreg

        with winreg.CreateKeyEx(self.hive, self.key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, self.render(command))

    def remove(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(self.hive, self.key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.name)
        except FileNotFoundError:
            pass


def launchctl(args, *, wait: bool = True) -> subprocess.CompletedProcess | None:
    """`/bin/launchctl ...`, never one found on PATH.

    Waited for with a timeout, or - `wait=False` - started in a session of its
    own and let go: a bootout waits for the job to stop, and when the job is
    this app, the app cannot answer its own stop while it waits. In its own
    session it also outlives the launcher's process-group stop of this app.
    """
    argv = ["/bin/launchctl", *[str(arg) for arg in args]]
    if not wait:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return None
    return subprocess.run(argv, capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT, check=False)


class LaunchAgent:
    """A launchd agent in this user's own Library. Unverified by any real login.

    The keys are the research note's (docs/reports/2026-10-19-background-
    server-research.md, section 2.2), all from `launchd.plist(5)`:

    * `ProgramArguments` names the launcher's binary inside the bundle, so
      launchd watches the launcher itself - not `open -a`, which returns at
      once and left launchd nothing to watch or log (0.9.x and earlier).
    * `KeepAlive` with `SuccessfulExit` false: restarted after any non-zero
      exit. The launcher's own is `APP_DIED`, when the app died under it
      (TASK-112.02); a clean stop, a busy port and another instance exit 0.
      An uncaught error in the frozen launcher exits 1 and is restarted too.
      launchd throttles a respawn to one per 10 s (`ThrottleInterval`'s
      default, launchd.plist(5)) but has no ceiling like systemd's
      `StartLimitBurst`: a launcher that fails every time, or a bundle that
      was deleted, is retried until logout. Not seen on a Mac.
    * `StandardOutPath` and `StandardErrorPath` in `~/Library/Logs/MyScribe`,
      beside the `LaunchAgents` folder, made before the plist is written.
    * `ExitTimeOut` longer than the launcher's longest stop (`EXIT_TIMEOUT`).
    * `ProcessType` Interactive, `LimitLoadToSessionType` Aqua, and
      `AssociatedBundleIdentifiers`, which ties the plist to the app in
      Login Items.

    The Settings switch loads it now (`load_now()`, `launchctl bootstrap
    gui/<uid>`); a refusal is a note, because the plist still loads at the
    next login. `enable()` alone loads nothing: the first-start sitting calls
    it in the launcher before the app is started, and a load there would let
    RunAtLoad start a second MyScribe while nothing serves yet. Switching
    off removes the plist and then boots the job out, which stops a MyScribe
    launchd started - the one answering the click - right away (Robert's
    decision of 2026-10-24). Writing alone asks launchctl nothing: the start
    that rewrites an older plist may be the very job a reload would stop.
    `run` and `uid` are injectable so a test stands in for launchctl.
    """

    stops_when_switched_off = True

    def __init__(self, folder: Path | None = None, label: str = LABEL, logs: Path | None = None,
                 run=None, uid: int | None = None):
        self.folder = Path(folder) if folder is not None else Path.home() / "Library" / "LaunchAgents"
        self.logs = Path(logs) if logs is not None else self.folder.parent / "Logs" / APP_NAME
        self.label = label
        self.run = run or launchctl
        self._uid = uid

    @property
    def path(self) -> Path:
        return self.folder / f"{self.label}.plist"

    @property
    def where(self) -> str:
        return str(self.path)

    @property
    def log(self) -> Path:
        return self.logs / LAUNCHD_LOG

    @property
    def domain(self) -> str:
        """`gui/<uid>`: this user's own login session, never `system`."""
        uid = self._uid
        if uid is None:
            getuid = getattr(os, "getuid", None)
            if getuid is None:
                raise OSError("this platform has no user id to give launchctl")
            uid = getuid()
        return f"gui/{uid}"

    def render(self, command) -> str:
        return shlex.join(str(part) for part in command)

    def plist(self, command) -> dict:
        log = str(self.log)
        return {
            "Label": self.label,
            "ProgramArguments": [str(part) for part in command],
            "AssociatedBundleIdentifiers": [self.label],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Interactive",
            "LimitLoadToSessionType": "Aqua",
            "ExitTimeOut": EXIT_TIMEOUT,
            "StandardOutPath": log,
            "StandardErrorPath": log,
        }

    def _held(self) -> dict | None:
        try:
            return plistlib.loads(self.path.read_bytes())
        except FileNotFoundError:
            return None

    def read(self) -> str | None:
        plist = self._held()
        arguments = plist.get("ProgramArguments") if plist else None
        return self.render(arguments) if arguments else None

    def current(self, command) -> bool:
        """Whether the plist on disk is exactly the one `write` would write -
        every key, not only the command, so a key added later reaches it."""
        return self._held() == self.plist(command)

    def names(self, launcher: str) -> bool:
        """Whether the plist starts this launcher: the bundle itself (`open -a`
        up to 0.9.x) or a file inside it. Paths, not shell text, so a path
        with a space in it still matches."""
        plist = self._held() or {}
        launcher = str(launcher).rstrip("/")
        return any(str(part) == launcher or str(part).startswith(launcher + "/")
                   for part in plist.get("ProgramArguments") or ())

    def write(self, command) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        # launchd opens the log itself and makes no folder for it.
        self.logs.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(plistlib.dumps(self.plist(command)))

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)

    def switch_on(self) -> str:
        """Load the agent now. Empty when launchd took it; otherwise what it
        said, with the reason the switch is on anyway. No bootout first: the
        job it would hit may be the app that is answering this click."""
        try:
            done = self.run(["bootstrap", self.domain, str(self.path)])
        except subprocess.TimeoutExpired as slow:
            said = f"launchctl did not answer in {slow.timeout} s"
        except OSError as failed:
            said = str(failed)
        else:
            if done.returncode == 0:
                return ""
            said = (done.stderr or done.stdout or "").strip() or f"exit code {done.returncode}"
        return f"launchd did not load it now ({said}); it starts at your next login."

    def switch_off(self) -> str:
        """Unload the agent, which stops a MyScribe launchd started. Not waited
        for, and asked only after the plist is gone: when the job is this
        app, nothing after this line is sure to run.

        Empty when launchctl was started; otherwise why not. The plist is
        gone either way, so the switch is off - only the stop is not now."""
        try:
            self.run(["bootout", f"{self.domain}/{self.label}"], wait=False)
        except OSError as failed:
            return (f"launchctl could not be asked to stop it now ({failed}); the MyScribe "
                    "launchd started runs until you quit it or log out.")
        return ""


class XdgAutostart:
    """A desktop entry in this user's own autostart directory. Unverified.

    The fields are the freedesktop Desktop Entry and Autostart specs': the
    type, the command, and the hint GNOME reads. No real Linux desktop has
    logged in with one of these.
    """

    def __init__(self, folder: Path | None = None, name: str = DESKTOP_NAME, environ: dict | None = None):
        if folder is not None:
            self.folder = Path(folder)
        else:
            environ = os.environ if environ is None else environ
            config = environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
            self.folder = Path(config) / "autostart"
        self.name = name

    @property
    def path(self) -> Path:
        return self.folder / self.name

    @property
    def where(self) -> str:
        return str(self.path)

    def render(self, command) -> str:
        """One `Exec=` value. The Desktop Entry spec quotes with double quotes
        and reserves four characters inside them - a backslash, a double
        quote, a backtick and a dollar sign - each escaped with a backslash.
        `%` is a field code wherever it stands, so a literal one is doubled.

        The backslash is replaced first, or the backslashes the other three
        put in would be escaped a second time.
        """
        parts = []
        for raw in command:
            part = str(raw).replace("%", "%%")
            if any(char in part for char in ' \t"\'\\><~|&;$*?#()`'):
                part = part.replace("\\", "\\\\")
                for reserved in ('"', "`", "$"):
                    part = part.replace(reserved, "\\" + reserved)
                part = '"' + part + '"'
            parts.append(part)
        return " ".join(parts)

    def read(self) -> str | None:
        """The command this file starts, or None when the desktop will not
        start it.

        Not only a missing file: the spec's own way of switching an entry off
        is `Hidden=true`, and GNOME's switch writes
        `X-GNOME-Autostart-enabled=false` rather than deleting anything.
        Either one means the desktop ignores this file, so the switch here has
        to say off as well - a user who turned it off over there must not find
        this card insisting it is on.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        command = None
        for line in text.splitlines():
            if line.startswith("Exec="):
                command = line[len("Exec="):]
            elif line.strip().lower() in ("hidden=true", "x-gnome-autostart-enabled=false"):
                return None
        return command

    def write(self, command) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME}\n"
            f"Exec={self.render(command)}\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)


def systemctl(args) -> subprocess.CompletedProcess:
    """`systemctl --user ...`: this user's own manager, never the machine's."""
    return subprocess.run(
        ["systemctl", "--user", *[str(arg) for arg in args]],
        capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT, check=False,
    )


def _unit_word(raw) -> str:
    """One word of a unit file's command line (systemd.service, "Command
    lines"). `%` is a specifier and `$` a variable wherever they stand, so
    each is doubled; a backslash and a double quote are C-style escapes; a
    word with whitespace or a quote in it is only one word inside quotes."""
    text = str(raw)
    quote = not text or any(char in text for char in " \t\"'\\;")
    text = text.replace("%", "%%").replace("$", "$$")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"' if quote else text


def _unit_assignment(name: str, value: str) -> str:
    """One `Environment=` assignment, always quoted. No `$` expansion happens
    there, but specifiers and C escapes do."""
    text = f"{name}={value}".replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


class SystemdUserUnit:
    """A systemd user unit in this user's own config directory.

    What the desktop entry cannot do: the user manager restarts a crash and
    keeps the output in the journal (`journalctl --user -u myscribe`). It is
    run against a real user manager only in WSL, never at a desktop login.

    * `Restart=no` with `RestartForceExitStatus=` the launcher's `APP_DIED`:
      the only restart is for the one exit that means the app died. A clean
      stop, another instance already serving and a configuration problem all
      end with 0, and a restart cannot plug a drive in.
    * Five starts in five minutes and the unit stays failed.
    * `KillMode=mixed`: a stop sends SIGTERM to the launcher alone, whose own
      stop takes the app's process group down; what is left after the
      timeout is killed.
    * `WantedBy=default.target`, reached at any first login, a desktop or
      not. MyScribe is a server and needs no display.
    * An `XDG_DATA_HOME` set at registration is written in, because the
      manager does not read `.profile`: without it the background server
      would open another library than a start by hand (ADR-026's Must).

    Switching it on does not start it (no `--now`): the app that writes it is
    already the one running. `legacy` is the desktop entry this replaces -
    written by an earlier version, or by this one without a user session -
    and on and off both take it away, so one login never starts two.
    `run` is injectable so a test can stand in for `systemctl`.
    """

    def __init__(self, folder: Path | None = None, name: str = UNIT_NAME, environ: dict | None = None,
                 run=None, legacy: "XdgAutostart | None" = None):
        environ = os.environ if environ is None else environ
        if folder is not None:
            self.folder = Path(folder)
        else:
            config = environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
            self.folder = Path(config) / "systemd" / "user"
        self.name = name
        self.environ = environ
        self.run = run or systemctl
        self.legacy = legacy if legacy is not None else XdgAutostart(environ=environ)

    @property
    def path(self) -> Path:
        return self.folder / self.name

    @property
    def where(self) -> str:
        return str(self.path)

    def render(self, command) -> str:
        return " ".join(_unit_word(part) for part in command)

    def _ask(self, *args) -> subprocess.CompletedProcess:
        try:
            return self.run(list(args))
        except subprocess.TimeoutExpired as slow:
            raise OSError(f"systemctl --user {' '.join(args)} did not answer in {slow.timeout} s") from slow

    def _must(self, *args) -> None:
        done = self._ask(*args)
        if done.returncode != 0:
            said = (done.stderr or done.stdout or "").strip() or f"exit code {done.returncode}"
            raise OSError(f"systemctl --user {' '.join(args)}: {said}")

    def text(self, command) -> str:
        service = [
            "Type=simple",
            f"ExecStart={self.render(command)}",
        ]
        data_home = self.environ.get("XDG_DATA_HOME")
        if data_home:
            service.append(f"Environment={_unit_assignment('XDG_DATA_HOME', data_home)}")
        service += [
            "Restart=no",
            f"RestartForceExitStatus={APP_DIED}",
            "RestartSec=5",
            "KillMode=mixed",
        ]
        return "\n".join([
            f"# Written by {APP_NAME}'s Start at login switch. Switch it off there, or:",
            f"#   systemctl --user disable {self.name}",
            "[Unit]",
            f"Description={APP_NAME}",
            f"StartLimitIntervalSec={START_LIMIT_INTERVAL_SEC}",
            f"StartLimitBurst={START_LIMIT_BURST}",
            "",
            "[Service]",
            *service,
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ])

    def read(self) -> str | None:
        """The `ExecStart=` this unit runs, or None when the manager will not
        start it at login: no file, or a unit somebody disabled with
        `systemctl --user disable`, which leaves the file where it was."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        command = None
        for line in text.splitlines():
            if line.startswith("ExecStart="):
                command = line[len("ExecStart="):]
        if command is None:
            return None
        answer = self._ask("is-enabled", self.name)
        return command if answer.returncode == 0 and answer.stdout.strip() == "enabled" else None

    def write(self, command) -> None:
        """Write, reload, enable, and only then take the desktop file away.

        A manager that refuses leaves no unit file this write created: a
        disabled unit beside a desktop file that still starts MyScribe is a
        file nobody asked for. A unit that was already there keeps its file,
        so a failed rewrite does not switch an entry that was on off.
        """
        self.folder.mkdir(parents=True, exist_ok=True)
        created = not self.path.exists()
        self.path.write_text(self.text(command), encoding="utf-8")
        try:
            self._must("daemon-reload")
            self._must("enable", self.name)
        except OSError:
            if created:
                self.path.unlink(missing_ok=True)
            raise
        self.legacy.remove()

    def remove(self) -> None:
        self.legacy.remove()
        if not self.path.exists():
            return
        self._must("disable", self.name)
        self.path.unlink(missing_ok=True)
        self._must("daemon-reload")


def systemd_user_session(environ: dict | None = None, which=shutil.which) -> bool:
    """Whether this user has a systemd user manager to register with.

    The manager's own socket, `$XDG_RUNTIME_DIR/systemd/private`, and a
    `systemctl` to talk to it: two cheap looks, and no process started,
    because Settings asks on every load.
    """
    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    if not runtime or not (Path(runtime) / "systemd" / "private").exists():
        return False
    return which("systemctl", path=environ.get("PATH")) is not None


def default_mechanism(platform: str = sys.platform, environ: dict | None = None, session: bool | None = None):
    """The mechanism this OS uses, pointed at this user's own profile.

    On Linux, a release with a systemd user session gets a user unit and
    everything else the desktop entry (ADR-026). A clone is the everything
    else: its command is `start.sh --detached`, which starts the app in the
    background and exits 0, and a unit reads that exit as the service ending
    and stops what is left in it - the app. `session` is injectable; None
    asks this machine.
    """
    if platform == "win32":
        return WindowsRun()
    if platform == "darwin":
        return LaunchAgent()
    environ = os.environ if environ is None else environ
    if environ.get(LAUNCHER_VARIABLE):
        if session is None:
            session = systemd_user_session(environ)
        if session:
            return SystemdUserUnit(environ=environ)
    return XdgAutostart(environ=environ)


# --- what the entry starts ---------------------------------------------------------


def start_command(
    environ: dict | None = None, repo: Path | None = None, platform: str = sys.platform
) -> tuple[str, ...] | None:
    """The command a login entry should run, or None when this machine holds
    nothing that would still be right at the next login.

    **A release** names the launcher. Only the launcher re-syncs the
    environment after an update, so an entry naming the environment's python
    would run a stale environment at the first login after a new release. The
    launcher hands its own stable path down as `MYSCRIBE_LAUNCHER`; where it
    did not, there is nothing to fall back on and this returns None rather
    than guess. On a Mac that stable path is the `.app` bundle - a directory,
    which launchd cannot execute - so there the entry names the launcher's
    binary inside it, `Contents/MacOS/MyScribe`, and launchd watches the
    launcher itself (TASK-112.05). A bundle running from a translocated copy
    is not named at all: that path is gone after the next login (`refusal`).

    **A clone** names the `scripts/start.*` that is already in the checkout,
    detached. Nothing new is created. That is a choice, not a given: R3
    declined "a start script or shortcut for a clone", and a login entry for a
    clone sits close to it. The reason to offer it anyway is that the machine
    this was written on is a clone with watch folders, which is the case the
    switch exists for. If it is read the other way, the fallback is releases
    only and the card says why.

    `--no-browser` on both, because a tab that opens at every login is the
    annoyance the entry exists to avoid. `--at-login` is the launcher's own,
    and leaves a first-run sitting that is due for the next start somebody
    makes by hand. `--headless` is the launcher's too: no window to keep open
    and, the Windows build being windowed, no console either (ADR-026). The
    clone doors do not get it - their scripts hand their arguments to
    `python -m scribe`, and already start it detached and hidden.
    """
    environ = os.environ if environ is None else environ
    launcher = environ.get(LAUNCHER_VARIABLE)
    if launcher:
        arguments = ("--at-login", "--no-browser", "--headless")
        if platform == "darwin":
            if TRANSLOCATION in launcher:
                return None
            # The stable path on a Mac is the `.app` bundle, which is a
            # directory - and launchd hands ProgramArguments straight to
            # execvp, so the binary inside it is named instead. Not
            # `open -a`: it returns at once and leaves launchd nothing to
            # watch, restart or log.
            bundle = launcher.rstrip("/")
            if bundle.endswith(".app"):
                return (f"{bundle}/Contents/MacOS/{FROZEN_NAME}", *arguments)
        return (launcher, *arguments)

    root = Path(__file__).resolve().parent.parent if repo is None else Path(repo)
    if platform == "win32":
        script = root / "scripts" / "start.ps1"
        if script.is_file():
            # -WindowStyle Hidden because powershell.exe is a console program:
            # without it every login opens a console window that lives as long
            # as the script's port check. The app itself is already started
            # hidden by `scripts/start.ps1 -Detached`.
            return (
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", str(script), "-Detached", "-Args", "--no-browser",
            )
    else:
        script = root / "scripts" / "start.sh"
        if script.is_file():
            # The script is not marked executable in the repository, so the
            # shell it is written for is named rather than relied upon.
            return ("/bin/bash", str(script), "--detached", "--", "--no-browser")
    return None


def refusal(environ: dict | None = None, platform: str = sys.platform) -> str | None:
    """Why this release must not be registered, in a sentence for Settings, or
    None when nothing is known against it.

    One case today: a Mac app running from App Translocation. macOS runs a
    quarantined app opened from a dmg or Downloads from a random read-only
    copy (Eclectic Light; Apple does not document the exact behaviour), and
    an entry naming it is broken at the next login.
    """
    environ = os.environ if environ is None else environ
    launcher = environ.get(LAUNCHER_VARIABLE) or ""
    if platform == "darwin" and TRANSLOCATION in launcher:
        return TRANSLOCATED
    return None


# --- on, off, and what the OS says -------------------------------------------------


def status(mechanism=None, command=None, environ: dict | None = None, platform: str | None = None) -> Entry:
    """What the OS holds right now.

    An entry that is there is reported exactly as the OS spells it, whatever
    wrote it - for the user unit, that includes a desktop file it replaces
    that the desktop still starts. An entry that is not there reports the command switching it on
    would write, so the card can show that before anything is written; where
    there is nothing to start, that is the empty string, and `refused` says
    why when that is known. `environ` and `platform` are for a test; the app
    passes neither.
    """
    mechanism = mechanism or default_mechanism()
    stops = bool(getattr(mechanism, "stops_when_switched_off", False))
    registered = mechanism.read()
    if registered is not None:
        return Entry(where=mechanism.where, command=registered, on=True, stops_now=stops)
    if isinstance(mechanism, SystemdUserUnit):
        # A desktop file the unit did not take over - one naming another
        # launcher path, or one a refused migration left - still starts
        # something at login. The card says so, and where; switching off
        # removes it, switching on replaces it.
        legacy = mechanism.legacy.read()
        if legacy is not None:
            return Entry(where=mechanism.legacy.where, command=legacy, on=True)
    command = command or _start_command(environ, platform)
    return Entry(
        where=mechanism.where,
        command=mechanism.render(command) if command else "",
        on=False,
        refused="" if command else (_refusal(environ, platform) or ""),
        stops_now=stops,
    )


def _start_command(environ, platform):
    """`start_command()` as the app calls it - with no arguments at all, which
    the tests that stand in for it rely on - unless a test passed its own."""
    if environ is None and platform is None:
        return start_command()
    return start_command(environ=environ, platform=platform or sys.platform)


def _refusal(environ, platform):
    if environ is None and platform is None:
        return refusal()
    return refusal(environ=environ, platform=platform or sys.platform)


def enable(mechanism=None, command=None, environ: dict | None = None, platform: str | None = None) -> Entry:
    """Write the login entry, and say what was written and where.

    Written only, never started: both doors make this one call (TASK-089.22),
    and the first-start sitting makes it in the launcher before the app runs.
    A LaunchAgent loaded there would have RunAtLoad start a launcher that
    finds nothing serving and starts a second MyScribe (TASK-112.05 review).
    The Settings switch, whose app is the one serving, asks `load_now()`.
    """
    mechanism = mechanism or default_mechanism()
    command = command or _start_command(environ, platform)
    if not command:
        raise NothingToStart(_refusal(environ, platform)
                             or "nothing on this machine is known to start MyScribe at login")
    mechanism.write(command)
    return Entry(where=mechanism.where, command=mechanism.render(command), on=True,
                 stops_now=bool(getattr(mechanism, "stops_when_switched_off", False)))


def load_now(mechanism=None) -> str:
    """Load a written entry in this session, where the OS has such a step -
    the LaunchAgent's bootstrap. Empty when it loaded or there is nothing to
    load; otherwise what the OS said, with the reason the switch is on anyway.

    Only for a MyScribe that already serves, so the launcher RunAtLoad starts
    finds it and exits 0: the Settings switch, not the first-start sitting.
    """
    mechanism = mechanism or default_mechanism()
    switch_on = getattr(mechanism, "switch_on", None)
    return switch_on() if switch_on is not None else ""


def refresh(mechanism=None, command=None, environ: dict | None = None) -> Entry | None:
    """Bring an entry an older version wrote up to today's command.

    0.8.6 and earlier registered no `--headless`, so their login opened the
    launcher's window (ADR-026's Must: rewritten at the next start). Returns
    the entry when it was rewritten, None when nothing was written:

    * an entry that is off stays off - a missing one, or one the desktop hides;
    * only a process the launcher started rewrites, so a clone on the same
      machine cannot point a release's entry at its start script;
    * only an entry that already names this same launcher, so a second
      install does not take over somebody else's.
    """
    environ = os.environ if environ is None else environ
    launcher = environ.get(LAUNCHER_VARIABLE)
    if not launcher:
        return None
    mechanism = mechanism or default_mechanism(environ=environ)
    registered = mechanism.read()
    if registered is None:
        return None
    if isinstance(mechanism, LaunchAgent):
        if not mechanism.names(launcher):
            return None
    else:
        named, held = mechanism.render((launcher,)), registered
        if isinstance(mechanism, WindowsRun):  # a path there is the same path in any case
            named, held = named.casefold(), held.casefold()
        if named not in held:
            return None
    command = command or start_command(environ)
    if not command:
        return None
    if isinstance(mechanism, LaunchAgent):
        # Every key, not only the command: a plist from 0.9.x runs `open -a`
        # with three keys. Written only, never reloaded - see `LaunchAgent`.
        if mechanism.current(command):
            return None
    elif mechanism.render(command) == registered:
        return None
    mechanism.write(command)
    return Entry(where=mechanism.where, command=mechanism.render(command), on=True)


def migrate(mechanism=None, command=None, environ: dict | None = None) -> Entry | None:
    """Move this launcher's desktop entry into the user unit, so one login
    starts one MyScribe (TASK-112.06).

    Up to 0.9.1 every Linux entry was a desktop file. With a user session the
    next start writes and enables the unit and removes the file; a unit that
    is already on only loses the leftover file. Returns the unit's entry when
    anything moved, None when nothing did. The same guards as `refresh()`:
    only a process the launcher started, only a desktop file that names this
    same launcher, and a file the desktop was told to ignore stays off with
    no unit beside it.
    """
    environ = os.environ if environ is None else environ
    launcher = environ.get(LAUNCHER_VARIABLE)
    if not launcher:
        return None
    mechanism = mechanism or default_mechanism(environ=environ)
    if not isinstance(mechanism, SystemdUserUnit):
        return None
    held = mechanism.legacy.read()
    if held is None or mechanism.legacy.render((launcher,)) not in held:
        return None
    registered = mechanism.read()
    if registered is not None:
        mechanism.legacy.remove()
        return Entry(where=mechanism.where, command=registered, on=True)
    command = command or start_command(environ)
    if not command:
        return None
    mechanism.write(command)
    return Entry(where=mechanism.where, command=mechanism.render(command), on=True)


def disable(mechanism=None, command=None) -> Entry:
    """Remove that one value or file, and nothing else. Already gone is fine.

    A mechanism that also stops what it started (the LaunchAgent's bootout)
    is asked to after the file is gone, and only when there was a file - one
    that will not parse counts, since launchd may have loaded it before it
    broke. What that stop said, when it could not run, is the entry's `note`.
    """
    mechanism = mechanism or default_mechanism()
    switch_off = getattr(mechanism, "switch_off", None)
    there = False
    if switch_off is not None:
        try:
            there = mechanism.read() is not None
        except ValueError:  # plistlib's InvalidFileException: a file is there
            there = True
    mechanism.remove()
    note = (switch_off() or "") if there else ""
    entry = status(mechanism, command=command)
    return dataclasses.replace(entry, note=note) if note else entry
