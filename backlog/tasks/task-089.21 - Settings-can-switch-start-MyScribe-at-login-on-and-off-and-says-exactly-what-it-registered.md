---
id: TASK-089.21
title: >-
  Settings can switch 'start MyScribe at login' on and off, and says exactly
  what it registered
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 03:07'
labels:
  - settings
  - packaging
  - ux
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 158000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
This is the counterpart that has to exist FIRST (brief: U3). scribe/setup.py:21-23 sets the rule: 'an answer given once at the start must never be the only place it can be given.' Nothing offers it today. A search of the tracked files for autostart, login item or LaunchAgents finds only the microphone recorder's own `data-record-autostart` attribute, and nothing in the launcher, the .iss or the README.

It is functional, not cosmetic. Watch folders and feed subscriptions only do work while the app runs: the Watcher and the FeedWatcher are threads the lifespan starts (scribe/app.py:268-281), and ADR-008 polls feeds on a schedule. A user who set up a watch folder in TASK-089.20 and then reboots has a folder nobody watches until they remember to start MyScribe.

For a release, what is registered is the launcher and not the bare app. Only the launcher re-syncs the environment after an update (packaging/launcher/myscribe_launcher.py:433-438); a login item that started the environment's python directly would run a stale environment after the next release.

The mechanism per OS is NOT decided here and none has been tried by anybody: a per-user Run entry or the Startup folder on Windows, a LaunchAgent on macOS, an XDG autostart entry on Linux. Nothing needs administrator rights, and nothing machine-wide is touched.

Needs a real machine: A real log-out and log-in on each OS: Robert on Windows. macOS: the Mac is somebody else's (brief: G9, decided by Robert on 2026-09-20); its points are bundled for the Mac's owner in TASK-089 criterion 10, and until that sitting reported as not run. Linux needs a desktop login, which WSL is not, and nobody is named for it. No mechanism has been tried by anybody.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Settings shows one switch, 'Start MyScribe when I log in', with its current state read from the OS and not from a remembered row, so a login item the user removed by hand shows as off.
- [x] #2 Switching it on says in words what was registered and where, per OS. Switching it off removes exactly that and nothing else. A test on each OS's mechanism covers on, off, and off-when-already-gone.
- [x] #3 It registers per user only. Nothing needs administrator rights, and nothing under a machine-wide key or folder is written. A test asserts the paths it touches.
- [x] #4 A release registers the launcher, not the environment's python, so a start at login still re-syncs after an update. A clone registers the clone's own start command, the existing scripts/start.*. That is a choice, stated so that Robert can overturn it, because R3 declines 'a start script or shortcut for a clone'. The login item is what R3's 'start at login' needs; it creates no desktop or Start-menu entry and no new script. On Windows the Startup-folder mechanism would literally be a shortcut file, while a per-user Run entry is not, and that weighs in the choice of mechanism, which is still open. If Robert reads a login item for a clone as the declined item, start-at-login is restricted to releases and the clone's switch says so. The notes say what each door registers.
- [x] #5 A start at login never opens the first-run sitting: nobody is at the screen, and a modal dialog that holds up the watch folders is the opposite of what the entry is for. The entry carries a flag of its own (the design spec proposes `--at-login`), a sitting that is due waits for the next start somebody makes by hand, and a test shows the app starts without one. What else appears at login is decided and said: no browser tab opens (`--no-browser` exists, myscribe_launcher.py:692), and whether the launcher window shows or starts minimised is recorded in the notes.
- [ ] #6 Needs a real login: on Windows, Robert switches it on, logs out and in, and /health answers without anybody starting MyScribe; then off, log out and in, and it does not. Linux needs a desktop login, which WSL is not; nobody on the project is known to have a Linux desktop, so that box stays unticked and the parent's final summary lists it unless Robert names a machine. macOS needs a real Mac. The Mac is somebody else's, decided by Robert on 2026-09-20 (brief: G9), so this point is not asked on its own: it goes into the bundled macOS list of TASK-089 criterion 10 with its command and its expected output, and reads 'not run' until that sitting. If it is not run, the box stays unticked and the parent's final summary lists it.
- [x] #7 What an uninstall has to remove is written into the notes, for TASK-089.23 to act on.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New scribe/autostart.py: status()/enable()/disable() over one Entry dataclass (where, what command, on/off). Per platform a small mechanism object, chosen by sys.platform, each with an injectable root so a test drives it anywhere: Windows = a value named MyScribe under HKCU\Software\Microsoft\Windows\CurrentVersion\Run (stdlib winreg, no new dependency, no .lnk - a Startup shortcut would be the item R3 declined and needs COM); macOS = ~/Library/LaunchAgents/<label>.plist (plistlib, RunAtLoad); Linux = $XDG_CONFIG_HOME/autostart/myscribe.desktop. status() reads the OS, never a setting row, so a hand-removed entry reads off (AC1). disable() is idempotent and removes only that one name (AC2).
2. What the entry starts (AC4). Release: the launcher with --at-login --no-browser. The app cannot see the launcher today - MYSCRIBE_LAUNCHER exists only in the spec - so launcher app_environment() (myscribe_launcher.py:201) hands its own stable path down (sys.executable on Windows; .app bundle / $APPIMAGE elsewhere, both unverified). Clone: the existing scripts/start.ps1 -Detached, scripts/start.sh --detached. Nothing new is created. The notes state the clone choice and its fallback (release only) so Robert can overturn R3's edge.
3. Launcher gets --at-login (AC5). The gate at :737 becomes a pure predicate, wants_setup(layout, force=, at_login=), so a sitting that is due waits for the next start by hand; tested without Tk. --no-browser already exists (:774, not :692). The notes record that the binary is windowed, so the Tk window shows at login; minimising is not attempted this task.
4. Settings (AC1-AC3): a seventh SECTIONS entry 'Start at login', autostart_context() in page_context(), a _settings_autostart.html card, POST /settings/autostart on|off through _back_to. The card prints the exact entry - the key or file path, and the full command - not only a state. Where nothing is known to start it says so and offers no switch.
5. Tests. tests/test_autostart.py: on/off/off-when-already-gone per mechanism (macOS and Linux against tmp_path; Windows against a real but scratch HKCU subkey, restored in a fixture); AC3 asserts the touched paths are per-user and that no machine-wide root appears in the module. tests/test_web_settings.py: the card shows the exact entry, and the switch flips a fake mechanism. tests/test_launcher.py: --at-login skips a due sitting, and the app environment carries MYSCRIBE_LAUNCHER. Every run fenced with SCRIBE_DATA_DIR/SCRIBE_ENV_FILE, one file per process; the bite shown by mutating a copy under the scratchpad.
6. AC7: the uninstall list (which registry value, which two files) goes into the notes for TASK-089.23.
7. AC6 cannot be closed by an agent: a real logout/login on Windows is Robert's; Linux needs a desktop login nobody has; macOS goes into the bundled list of TASK-089 criterion 10 as 'not run'. The notes carry the exact commands and expected output for each.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (2026-09-22)

**New:** `scribe/autostart.py` - `Entry(where, command, on)`, `status()`, `enable()`,
`disable()` over three mechanisms chosen by `sys.platform`, each with an injectable root:
`WindowsRun` (a value named MyScribe under this user's own Run key, stdlib `winreg`),
`LaunchAgent` (`~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist`, `RunAtLoad`),
`XdgAutostart` (`$XDG_CONFIG_HOME/autostart/myscribe.desktop`). `winreg` is imported *inside*
the Windows mechanism, so the module still imports on a Mac - the settings page imports it.
No new dependency: `winreg` and `plistlib` are stdlib.

**Changed:** `scribe/web/settings.py` (a seventh section "Start at login", `autostart_context()`
in `page_context()`, `POST /settings/autostart`), new `scribe/templates/_settings_autostart.html`,
`scribe/templates/settings.html`, `scribe/static/app.css` (three selector lists),
`packaging/launcher/myscribe_launcher.py` (`launcher_path()`, `this_launcher()`,
`MYSCRIBE_LAUNCHER` in `app_environment()`, `wants_setup()`, `--at-login`).

**Tests:** `tests/test_autostart.py` (new, 19), `tests/test_launcher.py` (+5),
`tests/test_web_settings.py` (+5).

## Evidence

Every pytest run fenced with `SCRIBE_DATA_DIR`/`SCRIBE_ENV_FILE`, one test file per process.
Output kept under the build scratchpad for TASK-089.21.

Red first:

* `red-autostart.txt` - `ImportError: cannot import name 'autostart' from 'scribe'`
* `red-launcher.txt` - `5 failed, 27 passed, 1 skipped`, including the behavioural
  `myscribe: error: unrecognized arguments: --at-login`
* `red-web-settings.txt` - `47 passed, 5 errors`

Green - every file touched, plus every existing file that renders `/settings` or imports what
changed (found with grep, not guessed):
`green-autostart.txt` 19 passed - `green-launcher.txt` 33 passed, 1 skipped -
`green-web-settings.txt` 52 passed - `green-web-scaffold.txt` 27 - `green-ingest-watching.txt` 84 -
`green-web-ai.txt` 124 - `green-web-exports.txt` 52 - `green-web-library.txt` 82 -
`green-glossary.txt` 69 - `green-web-transcript.txt` 104 - `green-library-row-meta.txt` 24 -
`green-web-transcribe-dialog.txt` 54.

The tests bite (`red-mutants.txt`; seven mutants on a **copy** under `mut/`, never the repository.
`grep -rn MUTANT scribe tests packaging` finds nothing afterwards):
A `status()` answering from a remembered row -> 6 failed - B `disable()` not removing -> 3 failed -
C the machine-wide hive -> 6 failed - D a release registering the environment's python -> 1 -
E a login start opening the sitting -> 1 - F the launcher path never handed down -> 1 -
G `main()` not passing `at_login` on -> 1 (the addendum below: it survived first).

Real and read-only on this machine (`real-card-this-machine.txt`): the card rendered against the
actual registry without starting the app. It reads *Starts when you log in: No*, would register
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\MyScribe`, and would run
`powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File D:\...\scripts\start.ps1`
`-Detached -Args --no-browser` (re-rendered after the review; see the last section).
Checked afterwards: no MyScribe value in the real Run key, no scratch key left under HKCU, and
nothing at all under HKLM.

The Windows lifecycle test writes to the real HKCU hive, under a key it owns
(`Software\MyScribe-autostart-test-<pid>`) that a fixture deletes in its `finally`. Faking
`winreg` would have left the one mechanism anybody here can verify asserted by nothing. Mutant C
also showed that the machine-wide write simply fails as this user, which is AC3's point.

## AC4 - a decision for Robert to overturn

A release registers the launcher (`<launcher> --at-login --no-browser`), because only the
launcher re-syncs after an update. The app could not tell a release from a clone at all, so the
launcher now hands its own stable path down as `MYSCRIBE_LAUNCHER`. Where it did not,
`start_command()` returns None, `enable()` refuses, and the card says there is nothing to
register rather than writing a path that breaks at the next login.

A clone registers the `scripts/start.*` that is already there, detached. **This sits close to
R3's declined "a start script or shortcut for a clone".** Nothing new is created and it is not a
shortcut file - it is one registry value naming a script that exists. If Robert reads it the
other way, the fallback is: releases only, drop the clone branch of `start_command()`, and the
card's existing "nothing here is known to start MyScribe" paragraph covers a clone unchanged.

## AC5 - what appears at a login start

* `--at-login` makes `wants_setup()` false for a sitting that is *due*; it waits for the next
  start by hand. `--setup` still wins, because somebody typed it. `--headless` never asked.
* `--no-browser` is on the entry, so no tab opens.
* The frozen binary is **windowed** on every target (`packaging/build_release.py`, `--windowed`),
  so a login start shows the launcher's Tk window. Starting it minimised was not attempted here.
* **The clone door, which is the one Robert tests.** `powershell.exe` is a console
  program, so the Run value now carries `-WindowStyle Hidden`: without it every login
  opens a console window that lives as long as the script's `Test-NetConnection` port
  check. The app itself was already started hidden by `scripts/start.ps1 -Detached`
  (`Start-Process -WindowStyle Hidden`). Not measured: whether a console still flashes
  briefly before the host applies the style. A login cannot be reproduced from a shell
  here, and a reading taken under the wrong conditions would look like evidence without
  being any. Hiding it loses nothing a user could have read: a failing `start.ps1`
  printed its error into a window that closed in about a second.
* TASK-089.11 will define the first-run gate properly. `--at-login` hooks into today's
  `setup_needed()` predicate because that gate does not exist yet.

## AC6 - the sitting nobody here can run

**Windows, for Robert.** A real log out and log in; a reboot also does.

1. `/settings?section=autostart` -> "Start MyScribe when I log in". The card must then read
   *Starts when you log in: Yes* and print the key and the whole command.
2. Outside the app: `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MyScribe`
   Expected: one `MyScribe REG_SZ` line identical to what the card printed.
3. Log out, log in, **start nothing**. Then `curl.exe -s http://127.0.0.1:4242/health`
   Expected: `{"ok":true,"version":"<the version>"}`.
4. Settings -> "Do not start at login". The card reads *No*. `reg query ...` again:
   Expected: `ERROR: The system was unable to find the specified registry key or value.`
5. Log out, log in, start nothing. `curl.exe -s http://127.0.0.1:4242/health`
   Expected: no answer at all (curl exit 7, "Failed to connect").

Begin from a state where nothing already serves 4242, or step 3 proves nothing:
`scripts/start.ps1` refuses to start a second instance when something answers there.

**macOS**: not run. It goes into the bundled list of TASK-089 criterion 10 - the same five steps,
with `ls -l ~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist` and `plutil -p` on it in
place of `reg query`.

**Linux**: not run, and nobody is named for it. It needs a desktop login; WSL is not one.

## AC7 - what an uninstall must remove (for TASK-089.23)

Exactly one thing per OS, all inside the user's own profile, **no administrator needed**:

* Windows: the single value `MyScribe` under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Not the key - other programs live there.
* macOS: the file `~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist`.
* Linux: the file `$XDG_CONFIG_HOME/autostart/myscribe.desktop`
  (`~/.config/autostart/myscribe.desktop` when the variable is unset).

`scribe.autostart.disable()` already does exactly this and is a no-op when it is already gone, so
an uninstaller can call it rather than spell the paths again. Nothing machine-wide was ever
written, so this part of an uninstall needs no elevation.

## Honest limits

* Only the **Windows** mechanism has been exercised against its real OS API. The LaunchAgent and
  the `.desktop` file are built from Apple's launchd keys and the freedesktop Autostart spec;
  their tests prove the file's contents and the on/off/off-again lifecycle against a tmp_path
  root, and **no real login anywhere has honoured either**.
* **The plist as first written could not have started anything, and that was not
  "unverified" but wrong.** `launchd.plist(5)` hands `ProgramArguments` to `execvp(3)`,
  and the stable path a release names on a Mac is the `.app` bundle - a directory.
  The entry now runs `open -a <bundle> --args --at-login --no-browser`, which is
  `open(1)`'s own way to start a bundle with arguments. Still not run at a real login,
  but composed from the platform's documentation rather than contradicting it.
* Whether writing the plist alone is enough, or `launchctl bootstrap` is needed for the *current*
  session, is untested. Nothing shells out to `launchctl`; the entry takes effect at the next login.
* The stable path a release entry should name - the `.app` bundle, `$APPIMAGE`, `sys.executable`
  - is the platforms' own documentation and nobody's measurement. Only the Windows one has been
  seen here, and only from a source checkout.
* `autostart_context()` reads this machine's real HKCU Run key on every `/settings` load in every
  test that renders the page. The read is harmless, but it is this machine answering; the tests
  that are *about* the card inject a mechanism of their own.

## Addendum - a seventh mutant, and two things a later reader should not re-derive

A review pass found one gap and it is now closed. `--at-login` was asserted at the parser and at
the predicate, but nothing asserted the wiring between them: deleting `at_login=args.at_login`
from `main()`'s call to `run_window` left all 32 launcher tests green while a login would have
opened the first-run sitting again - exactly AC5's failure.

* Mutant **G** (`main()` forgets to pass at_login on) survived: `red-mutants-G-before.txt`,
  `tests/test_launcher.py: 32 passed, 1 skipped`.
* Added `tests/test_launcher.py::test_the_flag_reaches_the_window_and_not_only_the_parser`, which
  captures the call rather than running `run_window` (it needs Tk).
* Re-run: `red-mutants.txt` now shows all seven caught, G among them.
  `green-launcher.txt`: `33 passed, 1 skipped`.

Two facts worth keeping:

* `main()` hands `at_login` to `run_window` only; both `run_headless` branches drop it. That is
  not a hole: `run_headless` goes straight to `Launch.run()` and never consults `wants_setup` at
  all, so a headless start - including the fallback when `import tkinter` fails in a frozen
  build - never opens the sitting either way.
* A login entry always starts on port 4242. `scripts/start.ps1` reads `--port` out of its
  `-Args` to decide whether something already answers, and the entry passes only
  `--no-browser`. That is the intended port; a login entry on another port is simply not a shape
  this offers. Noted as known, not as work.

Verified independently by the orchestrator on 2026-09-22.

The suite, one file per process, fenced: 2737 passed, 0 failed, 10 skipped over 80 files, against 2702 before this task.

Checked by me rather than taken from the report:
- Robert's real HKCU Run key is untouched. Fourteen entries, MyScribe absent. The tests used a scratch subkey as the plan said. This mattered enough to check: one of the review's findings was that nothing asserted remove() takes away the value rather than the whole key, and the whole key is where Edge, OneDrive, Discord, Signal, Nextcloud and eM Client keep their own entries.
- scribe/autostart.py names HKEY_LOCAL_MACHINE, /Library/LaunchAgents and /etc/xdg zero times. Everything stays inside this account and needs no administrator.
- The macOS fix is real and reasoned in the code: launchd hands ProgramArguments straight to execvp, so the .app bundle directory it carried before could not have spawned anything. It is now /usr/bin/open -a <bundle> --args, which is open(1)'s documented way.

That macOS defect is the one worth remembering. It was knowably broken rather than merely unverified, and it came from building three mechanisms where only one can be tested here. The fixer read launchd.plist(5) instead of trusting the design - which is what makes the difference between "written from the documentation" and "checked against it".

Criterion 6 is Robert's sitting and no agent can do it: switch on, log out, log in, see /health answer with nobody having started MyScribe, then off and the same again. The notes carry the commands and the expected output.

For TASK-090: every test that renders /settings reads this machine's real HKCU Run key, because conftest stubs every other such place but knows nothing about autostart. Reading is harmless in itself; depending on machine state is not.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Settings can switch 'start MyScribe at login' on and off, and says exactly what it registered.

scribe/autostart.py writes one per-user login entry and reads its state back from the operating system rather than from a setting row, so a user who removes the entry by hand sees the switch go off by itself. The card prints the place and the whole command, not just 'on'.

It is functional and not a convenience. Watch folders and feed subscriptions only do work while the app runs - the Watcher and FeedWatcher threads start in the lifespan - and a feed polled on a due date (ADR-008) is polled by nothing when nothing is running.

Nothing machine-wide: the value under this user's own Run key, a LaunchAgent in ~/Library, an XDG entry in this user's config. No administrator, and no elevation call anywhere. Removing the switch removes the value and not the key - which the review found was asserted by nothing, on a key where six other programs keep their own entries.

The entry starts the launcher for a release, so an update still re-syncs, and the existing start script for a clone. It carries --at-login, and the launcher gained that flag so a start at login never opens the first-run sitting; a due sitting still appears on the next start by hand.

Of the three mechanisms only Windows can be verified on this machine, and the review showed why that matters: the macOS LaunchAgent carried the .app bundle directory in ProgramArguments, which launchd hands straight to execvp, so it could not have spawned anything. It is /usr/bin/open -a <bundle> --args now, read out of launchd.plist(5) rather than assumed. The Linux entry also ignored its own off switch, honouring Hidden=true now as the Desktop Entry spec says.

Criterion 6 stays unticked: no agent can log out and log in. The notes carry the sitting for Robert, and macOS goes into the bundled list of TASK-089 criterion 10.

Suite fenced at 2737 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->

## What the review changed (2026-09-22)

Three verifiers read this. Two majors were real code defects, two were real holes in the
tests, and the rest were smaller. What moved, with its evidence under the build scratchpad
for TASK-089.21:

**The Mac entry could not have started anything** (major, `scribe/autostart.py`). See Honest
limits above. Red first: `fix-red-autostart.txt`, *argv[0] goes to execvp; a bundle directory
is not executable*. `tests/test_autostart.py::test_a_mac_release_names_something_launchd_can_actually_execute`
pins argv[0] and keeps `launcher_path()` alone - the bundle is still the right *stable* path.

**"Removes exactly that and nothing else" was asserted by nothing** (major, AC2 and AC7).
Every lifecycle test ran against a root that held only MyScribe's own entry, so removing the
whole Run key or the whole autostart folder read back exactly like removing one entry. The
test now puts a neighbour beside ours and asserts it survives - on all three mechanisms.
Proved with two new mutants (`fix-red-mutants.txt`): **H1** `winreg.DeleteKey` in place of
`DeleteValue` -> 1 failed; **H2** `shutil.rmtree(folder)` in place of `unlink` -> 2 failed.
On a real machine H1 is every other program's login entry.

**Neither POSIX renderer was pinned** (major). `Exec=` is the operative field a desktop runs,
and the only evidence about Linux there can be here, yet a naive `" ".join` passed the whole
suite. The rendered line is now asserted character for character, with a case for a path
holding a space, and `agent.read()` is compared to `shlex.join`. Mutants **I** (no quoting in
`XdgAutostart.render`) -> 2 failed and **J** (no quoting in `LaunchAgent.render`) -> 1 failed.

**Two Linux correctness fixes found by the same reading** (red first, `fix-red-autostart.txt`):
`Hidden=true` and `X-GNOME-Autostart-enabled=false` now read as **off** - the spec's own way of
switching an entry off, and what GNOME's switch writes, so AC1's "removed by hand shows as off"
holds for the one mechanism whose users have a switch of their own. And `Exec=` now escapes a
backtick and a dollar sign as well as the backslash and the double quote: the spec reserves
all four inside a quoted argument.

**The card's "says why" was vacuous** (`tests/test_web_settings.py`). `"nothing" in page` is
true in every branch - the card's standing paragraph says nothing machine-wide is written.
The assertion is now on text only that branch renders. Mutant **K** (the explanation deleted)
-> 1 failed; it would have passed before.

**Both error paths of `POST /settings/autostart` had no test.** A 409 for a post from a stale
page and a 500 for an OS that refuses the write. The route was already right; mutant **L**
(both `except` clauses collapsed to `pass`, so a refusal answers 200) -> 2 failed.

**Two test-hygiene fixes.** The switch assertions were searching the whole settings page for
`name="enabled" value="1"`, which is byte-identical to what a switched-off watch folder
renders; they are now sliced to this card. And the scratch registry key lost its `-<pid>`
suffix: a run killed outright never reaches its `finally`, and one fixed name means the next
run collects the leftover instead of leaving a new one beside it.

**Rejected, with the reason.** The cross-check between `SECTIONS` and the three hand-written
`:has()` lists in `app.css` is missing, so a section could render invisible with the suite
green - but all six earlier sections carry the identical exposure and the verifier named it
out of scope. Reported, not fixed here. (The stale "Six categories" in that test's docstring
*was* this task's doing and now reads "Seven".)

Final, fenced, one test file per process:
`final-test_autostart.txt` **22 passed**, `final-test_web_settings.txt` **54 passed**.
No other file needs re-running: `default_mechanism()` is `WindowsRun` here, so the XDG and
macOS changes are unreachable from a `/settings` render, and the only other tests mentioning
"autostart" are the microphone recorder's own `data-record-autostart` attribute. The launcher
was not touched. `real-card-this-machine.txt` was re-rendered read-only after the command
changed; the real Run key held 14 values before and after and no `MyScribe` among them.
<!-- SECTION:NOTES:END -->
