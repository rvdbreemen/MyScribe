---
id: TASK-089.15
title: >-
  The launcher's first run syncs, shows what was found, asks what is open and
  shows failures
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 22:30'
labels:
  - packaging
  - ui
  - ux
dependencies:
  - TASK-089.14
  - TASK-089.11
  - TASK-089.13
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 152000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Tk dialog is a fixed form that cannot see the machine. It always draws the token field (packaging/launcher/myscribe_launcher.py:547-549), so a user whose token already resolves is shown an empty field, reasonably concludes it is missing, and goes to mint a second one. The provider and tier radios are hard-coded to 'ollama' and 'turbo' (:558, :565) and are always sent on Save (:578-585). So someone on OpenRouter who runs `--setup` only to add a token presses 'Save and start' and is now on Ollama with tier Turbo, having touched neither control.

The token rides on argv (:475-476). The result of run_setup is discarded (:659-663), so exit codes 3 and 2 from scribe.setup are never interpreted: the one line that explains a gated model scrolls past in a grey log while the headline says 'MyScribe is running'. The headline stays 'Saving your answers...' for a whole download (:658), and each carriage-return progress update becomes a log line of its own; a reader calculated about 1,500 of them for 1.6 GB, which was not measured on a real download.

Quit does not stop a setup child: quit_app only calls launch.stop() (:617-621), which knows the app process, and run_streaming keeps its Popen local (:277-286). The conditions URL is a plain tk.Label (:550-555), neither clickable nor selectable. A headless start, or a launcher without Tk, asks nothing at all (:500-512, :730-736), and `--setup` there does nothing visible.

CI has never walked any of this: `--smoke` returns before run_window (:717-728).

The order becomes: location (TASK-089.14), tools, sync, plan, one sitting, apply, prove, start. The launcher stays stdlib-only and inside ADR-011: it learns only to read one JSON document and to write one.

Needs a real machine: A person at the screen once per OS: Robert on Windows, and in WSL for the Linux console door. macOS: the Mac is somebody else's (brief: G9, decided by Robert on 2026-09-20); its points are bundled for the Mac's owner in TASK-089 criterion 10, and until that sitting reported as not done. Nobody has run the real Tk window.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The first run is one function with injected callables, in this order: location, tools, sync, plan, sitting, apply, prove, start. A test asserts that order.
- [x] #2 Red first (today a re-run with Save resets the provider to ollama and the tier to turbo): the dialog is drawn from `--plan`. It shows a 'Found on this machine' block with sources only, then only the open questions. Each question has its own Skip and its if_skipped sentence, and radios start on the current values. shown_if is the only condition the launcher interprets.
- [x] #3 Answers reach the child on stdin. A test asserts that no secret appears in the child's argv. setup_command's answer flags go (packaging/launcher/myscribe_launcher.py:474-484), `--hf-token` (:475-476) and `--diarize/--no-diarize` (:481-482) among them, and tests/test_launcher.py:400-407, which pins them, is revised knowingly with its diff shown.
- [x] #4 'Save and start' stamps the sitting and 'Ask me next time' does not, as TASK-089.11 defines them. Neither writes a speakers guard: there is none (brief: W6).
- [x] #5 JSON-line progress drives a progress bar and the headline. There are no per-MiB log lines.
- [x] #6 Exit codes 3, 2 and 1, and any `reopen`, become a visible error state with Retry and Continue without.
- [x] #7 Every reported line is teed to <home>/logs/launcher.log with secrets redacted.
- [x] #8 A Setup button and `--setup` reopen the sitting on current state. The button is new: today the window has 'Open MyScribe', 'Open data folder' and 'Quit' (packaging/launcher/myscribe_launcher.py:613-623). A question recorded as skipped is asked again in a sitting opened from the button; that route's test lives here and not in TASK-089.11, which is built first. A sitting opened from the Setup button while the app runs loads no model in the setup child: the launcher passes its port, and the proof follows TASK-089.13. A test asserts the gpu-smoke is never called on that path.
- [x] #9 On macOS and Linux with --headless, or without Tk, the console asker runs with the terminal attached when stdin is a TTY. Without a TTY one line says nothing was asked, and how to ask. The Windows binary is windowed and has no console; Tk is always there.
- [x] #10 Quit during a MyScribe download stops the setup child, and a test shows that no orphaned python process survives. How: the launcher keeps the child's handle and stops that one process - terminate(), not the tree kill it uses for the app (packaging/launcher/myscribe_launcher.py:376-380) - so that no tree kill passes over an Ollama the installer has just started. The design spec and ADR-017's Must say the same (M10 belongs to ADR-017 since the split of 2026-09-20, brief: G1). This lands while ADR-017 is still Proposed, and TASK-089.18 criterion 13 confirms or changes it; the notes here say what was promised and what that run then found. What a single-process stop can orphan is said in those words: at most a version probe that ends by itself within 15 s (scribe/doctor.py:116-125), and the test waits that long. What Quit may promise around a freshly installed Ollama is for TASK-089.18 to establish first (brief: M10).
- [x] #11 The Hugging Face conditions link is clickable. The hard-coded provider tuple (:561) and the '1.6 GB' literal (:574) are gone from the launcher.
- [x] #12 `--smoke` applies `{}` over stdin before /health, and asserts that no setting row was written and that the app still serves. It writes <home>/logs/launcher-smoke.log, which build_release.py prints on failure.
- [ ] #13 Needs a person at the screen: one recorded real first run per OS with a fresh --home. Robert does Windows, and Linux in WSL through the console door; whether the Tk window shows under WSL has been tried by nobody. macOS needs a real Mac. The Mac is somebody else's, decided by Robert on 2026-09-20 (brief: G9), so this point is not asked on its own: it goes into the bundled macOS list of TASK-089 criterion 10 with its command and its expected output, and reads 'not run' until that sitting. If it is not run, the box stays unticked and the parent's final summary lists it.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
0. Corrections, all re-read 2026-09-22. Every line number in the description is stale (the file is 1913 lines): setup_command 1361 (the `--hf-token` lines 1370-1371), run_setup 1383, first_run 1398, _apply_setup 1434, ask_setup 1531, run_window 1650, the three buttons 1666-1677, AppProcess/app.log 1140, locate_home 1017. There is no launcher.log yet and no `--prove` call in the launcher at all. Two facts moved: (a) criterion 2's parenthetical red is stale - b3804f5 (TASK-089.25) already set both radios to StringVar(value="") and setup_command skips empty values, so a re-run with Save resets nothing today; the criterion's substance stands and only its red moves (step 3). (b) ADR-017 is Accepted since 2026-09-21, not Proposed; its Enforcement is on for every commit.
1. The gate, confirmed by reading: `scribe.setup` main() refuses `--hf-token` with exit 2 as its first statement after parse_args, before env.load_dotenv(); setup_command builds that flag first into the same argv. A sitting in which somebody types a token loses the whole sitting - no row, no stamp, the gate refires. Red first: a test that runs a whole Tk sitting with a token against the real `scribe.setup` and asserts a stamp exists. Keep the failing output.
2. Criterion 1: first_run keeps its injected callables and gains plan, prove and the child's handle; location stays in main() where TASK-089.14 put it (Layout needs the home). One test drives launcher.main() with every step monkeypatched to append its name and asserts [location, tools, sync, plan, sitting, apply, prove, start]. Any test that drives main() passes --home AND sets LOCALAPPDATA/XDG_DATA_HOME/HOME: the autouse fixture patches pointer_path only, so home_dir() otherwise reaches the real %LOCALAPPDATA%.
3. Criterion 2: the launcher runs `--plan` (with --unasked-only for the gate, without it for --setup and the Setup button, per ADR-015's Must) and draws the dialog from that document - the found block from found[] and ollama, then one widget per questions[] entry by kind, each with its own Skip, its if_skipped sentence, and radios on `current`. shown_if is the only condition interpreted. Red first on the new seam: ask_setup takes no plan today, so it cannot hide a found token, cannot open on `current` and has no per-question Skip. Driven on the existing fake-tkinter harness (the seam is ask_setup's in-function `import tkinter as tk`), which grows bind/config for the link.
4. Criterion 3, the one that matters most: setup_command loses every answer flag; the answers go as one JSON document on the child's stdin through run_streaming(input=...), carrying `contract` from setup_contract(layout) - and omitting that key when it returns None, so an unreadable shipped setup.py is not a spurious exit 2. Proof: a test on the argv, a SENTINEL test that the typed secret appears in no argv and in no line of launcher.log, and ADR-015's own check - `grep -n -- "--hf-token" packaging/launcher/myscribe_launcher.py` empty. tests/test_launcher.py:561 and :580 are rewritten knowingly against the document, with their diff in the notes.
5. Criterion 4: ask() returns None only for "Ask me next time" or a closed window, and always a dict for Save - so a sitting in which everything was skipped still runs the child and the engine still stamps. Tests assert `default_diarize` is in no document and `--diarize/--no-diarize` in no argv (W6).
6. Criteria 5 and 6: the child's stdout is split - a line that parses as a JSON object with event=progress drives a ttk progress bar and the headline and is never logged; everything else is a log line. The apply exit code becomes a visible error state with Retry and Continue without: named sentences for 2 (contract or pinned-file mismatch - never the token refusal, which the launcher can no longer provoke), 3 (gated model, with the conditions link) and 4 (disk), anything else generic. Criterion 6 omits 4; reported, not silently widened. `reopen` is read off the child's "still open: " prose prefix - a coupling commented at both ends and listed as a risk, since making it a JSON line would be a contract change.
7. Criterion 7: one tee around report(state, text), appending to <home>/logs/launcher.log, created lazily on first write (the location question runs before a Layout exists and is out of its reach). Redaction is by value: every secret the dialog collected is replaced before the line is written. This closes what TASK-089.13 criterion 1 owed - "mirrors the report into the install log" had no referent until this file exists; the notes say so.
8. Criterion 8: a Setup button beside the three at :1666-1677, and --setup, reopen the sitting on a full `--plan`, so a question recorded as skipped is asked again; that route's test lives here. While the app runs the launcher passes --port to `--prove`, so the child queues the doctor job instead of loading a model; a test with a gpu-smoke double that raises proves it is never called. Report and failure are kept apart: per TASK-089.13 step 14 transcription is a required line, so "not tested (app running)" exits 1 by design - prove's exit code is information and is rendered as a report; only apply's exit code drives criterion 6's error state. Otherwise the Setup button would always end in "Retry".
9. Criterion 9: the headless door with a TTY hands the terminal to the engine (stdin and stdout inherited, no --apply-stdin) and lets scribe.setup's own asker run - at_a_terminal() already carries the measured Windows-NUL fix and the engine already prints the no-TTY line naming --apply-stdin. Criterion 7 therefore covers what passes through report(); a console sitting writes to the terminal, which is its log. Said as a scope, not as a gap.
10. Criterion 10, ADR-017's Must: the launcher keeps the setup child's handle and Quit stops that one process with terminate() - never the tree kill AppProcess uses - so no tree kill can pass over an Ollama a third-party installer has just started. What a single-process stop can orphan is at most a version probe that ends by itself within 15 s (scribe/doctor.py's _run timeout); the test polls for the grandchild up to that ceiling rather than sleeping it, and is the one slow test in the file. No installer exists in the engine yet, so ADR-017's "refuse Quit while a third-party installer runs" has no referent here; TASK-089.18 criterion 13 owns it and the notes say what was promised.
11. Criterion 11: the conditions URL becomes a clickable Label (webbrowser.open on Button-1); the hard-coded provider tuple and the "1.6 GB" literal go - the sizes come from the plan's questions[] and from download_note(). The URL itself stays a launcher constant keyed to the hf_token question id: moving it into the contract would need a CONTRACT bump, which is TASK-089.09's and out of scope. Noted as a tension with ADR-015, not hidden.
12. Criterion 12: `--smoke` applies {} over stdin before /health and writes <home>/logs/launcher-smoke.log, which packaging/build_release.py prints on failure. The narrow claim only: {} writes no setting row (asserted on the child's own "saved: nothing"), but it does create the directories, migrate and write a stamp - the notes say which, so nobody later reads the smoke as proof that {} touches nothing.
13. Method: red first for criteria 2, 3 and 6; one pytest process per test file behind the TASK-090 fence (SCRIBE_DATA_DIR and SCRIBE_ENV_FILE exported); the tests bite shown on a copy under scratch/mut, one change per run, then grep -rn MUTANT over scribe, tests and packaging empty. ADR-011 holds: the scribe-import grep over the launcher stays empty, and the launcher learns only to read one JSON document and to write one.
14. What no agent can close: criterion 13 needs a person at a real screen once per OS - Robert on Windows and in WSL for the console door, and a Mac that is somebody else's (G9); the code and the exact commands and expected output go in the notes and the box stays unticked. Criterion 5's bar and criterion 6's error state are Tk: the fake-tkinter harness covers what is built and with which text, and nobody has seen the real pixels - the notes say which half each test covers.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (agent, 2026-09-22)

### What landed

`packaging/launcher/myscribe_launcher.py` (1913 -> 2482 lines), `tests/test_launcher.py`,
the new `tests/test_launcher_sitting.py` (52 tests) and `packaging/build_release.py`.

**Criterion 3, the release gate, first and on its own.** `setup_command(layout)` is now
`[env_python, -m, scribe.setup, --apply-stdin]` and carries no answer at all;
`setup_document(layout, answers)` builds `{contract, answers}` keyed by the plan's question
ids, and `run_streaming` gained `input=` - it writes the document to the child's stdin and
closes it, because the engine reads until EOF and a stdin left open is a child that never
starts. The `contract` key is left out when `setup_contract()` returns None, so an
unreadable shipped `setup.py` cannot become a spurious exit 2.

The gate itself, measured against the real engine before the change
(`measured-the-gate-today.txt`):

    setup.main(['--hf-token','hf_SENTINELTOKEN','--provider','ollama','--tier','max','--fetch-models'])
    -> exit code: 2 ; data dir exists: True contents: [] ; stamp: False

So a Tk sitting in which somebody typed a token lost the whole sitting: no setting row, no
stamp, and the gate again at every start, with only "Saving your answers failed with exit
code 2" to go on. The launcher can no longer build that flag.

**Criteria 1, 2, 4-12.** `first_run` is one sequence with injected callables and replaceable
steps - location (in `main`, where TASK-089.14 put it), tools, sync, plan, sitting, apply,
prove, start. `ask_setup(root, layout, plan)` draws the dialog from `--plan`: a "Found on
this machine" block from `found[]` and `ollama` (sources only), then one widget per open
question by kind, each with its own Skip and its `if_skipped`/`answer_later` sentence.
`shown_if` is the only condition interpreted (`shown()`), and a question it hid is not in the
document at all. New pure functions carry the decisions so they can be tested without Tk:
`found_lines`, `shown`, `opening_value`, `progress_line`, `progress_headline`,
`setup_failure`, `plan_command`, `prove_command`. New Tk renderers: `ask_failure` (Retry /
Continue without) and `link_label`. `InstallLog` tees every reported line to
`<home>/logs/launcher.log` with secrets redacted by value; `Launch.report` is the one tee.
`Launch.stop_setup()` terminates the setup child, one process, never a tree kill.
`run_headless` now goes through `first_run` with no `ask`, so the console door asks at all -
it ran `launch.run()` before, which asks nothing.

### Evidence

Every run behind the TASK-090 fence (`SCRIBE_DATA_DIR`, `SCRIBE_ENV_FILE` exported), one
test file per process, output kept in the task's evidence folder.

    red-criterion-3.txt              5 failed, 97 deselected in 4.26s
    green-criterion-3-first.txt      5 passed, 97 deselected in 3.99s
    green-test_launcher.txt          99 passed, 1 skipped in 31.10s
    green-test_launcher_sitting.txt  52 passed in 11.19s
    green-test_proxy.txt             29 passed in 16.66s

The one skip is the pre-existing POSIX-only symlink test. `tests/test_proxy.py` is
there because it loads the launcher module too; grep found no other file that does.

### The tests bite: six mutations on a copy

One change per run, on a copy under the scratch directory, each asserting the text it
replaces is still there (`mutate.py`, `mutation-runs.txt`, `mutant-tree_kill.txt`). Every
one of them turns a green suite red:

    argv       the answers ride on the argv again      -> 3 failed (setup_command, the
                                                          end-to-end argv test, the smoke)
    skipped    a sitting with no open question is None -> 1 failed
    progress   a progress line reads as prose          -> 5 failed
    redaction  the secret is written unredacted        -> 1 failed (the SENTINEL test)
    shown_if   the condition is ignored                -> 1 failed
    tree_kill  Quit tree-kills the setup child         -> 1 failed, on ADR-017's own sentence

    grep -rn "MUTANT" scribe tests packaging   -> nothing: the repository is clean.

`skipped` is honest about what it caught: it fails
`test_the_plan_with_nothing_open_still_shows_what_was_found` and not
`test_save_with_every_question_skipped_is_still_a_sitting`, because a sitting in which every
question was skipped is a dict of nulls, which is truthy. The two tests pin different halves
and both are needed.

ADR checks, both empty as they must be:

    grep -n -- "--hf-token" packaging/launcher/myscribe_launcher.py          (nothing; ADR-015 Verification)
    grep -nE "[\"']--[a-z0-9-]*(token|key|secret|password)[a-z0-9-]*[\"']" packaging/launcher/myscribe_launcher.py
    grep -nE '^\s*(import|from)\s+scribe' packaging/launcher/myscribe_launcher.py   (ADR-011)

The launcher's prose no longer spells the flag either, so the record's own grep is satisfied
literally rather than in spirit.

### The two tests revised knowingly (criterion 3)

`tests/test_launcher.py:561 test_the_answers_are_handed_to_the_app_not_acted_on_here` and
`:580 test_an_unanswered_question_is_not_passed_at_all`. Both kept their name and their
claim; only the place the answers travel moved.

    -    command = launcher.setup_command(
    -        layout,
    -        {"hf_token": "hf_x", "provider": "ollama", "tier": "max", "diarize": False, "fetch_models": True},
    -    )
    -    assert command[0] == str(layout.env_python)
    -    assert command[1:3] == ["-m", "scribe.setup"]
    -    assert "--hf-token" in command and "hf_x" in command
    -    assert "--provider" in command and "ollama" in command
    -    assert "--tier" in command and "max" in command
    -    assert "--no-diarize" in command and "--fetch-models" in command
    +    command = launcher.setup_command(layout)
    +    assert command == [str(layout.env_python), "-m", "scribe.setup", "--apply-stdin"]

    -    command = launcher.setup_command(layout, {"hf_token": "", "provider": "", "tier": "", "fetch_models": False})
    -    assert command == [str(layout.env_python), "-m", "scribe.setup"]
    +    document = launcher.setup_document(layout, {"hf_token": None, "default_tier": "max"})
    +    assert document["answers"] == {"hf_token": None, "default_tier": "max"}
    +    assert "llm_provider" not in document["answers"]

Seven existing `first_run` tests were revised for the same reason - the new `ask(plan)`
signature and the new order - and the whole TASK-089.25 sitting block moved.
The TASK-089.25 sitting block (`_Sitting`, `_fake_tkinter`, `_drive_setup` and its seven
tests) moved out of `tests/test_launcher.py` into `tests/test_launcher_sitting.py` and was
rewritten against a plan: `test_a_question_nobody_touched_hands_over_nothing`,
`test_the_two_questions_that_can_be_skipped_open_on_their_skip` (which hard-coded
`len(sitting.options) == 7`), `test_a_group_on_its_skip_does_not_open_with_every_circle_filled`,
`test_save_and_start_still_stamps_a_sitting_nobody_touched`,
`test_the_button_that_puts_the_whole_sitting_off_says_that_is_what_it_does`,
`test_a_person_who_does_pick_a_provider_still_gets_one` and
`test_the_provider_question_says_that_picking_chooses_who_answers`. Each claim survives; the
tristate lesson of TASK-089.25 is kept as
`test_a_group_whose_stored_value_is_empty_opens_on_nothing`. `_drive_first_run` now records
`plan` and `prove` as well and returns a namespace instead of a four-tuple.

### Decisions worth disagreeing with

* **Radios open on `current`, never on `default`** - except a yes-no question, which opens on
  the plan's `default`. A preselected default is an answer nobody gave (ADR-016,
  TASK-089.25), but a checkbox has no unanswered state and the one question of that kind
  writes no setting row; Robert ratified that reading on 2026-09-22 and it is kept. The
  consequence is concrete: pressing "Save and start" without touching anything downloads the
  weights now (default yes) and writes no provider and no tier.
* **The conditions URL stays a launcher constant** keyed to the `hf_token` question id. The
  contract's `Question` has no url field and adding one would bump CONTRACT, which reopens
  everybody's gate and belongs to TASK-089.09. A mild tension with ADR-015, recorded rather
  than hidden.
* **`reopen` is read off the engine's prose** - the "still open: " prefix printed by
  `scribe.setup`. One step from what ADR-015 forbids, commented at both ends
  (`Launch.apply`, `REOPEN_PREFIX`); making it a JSON line is a contract change and is
  TASK-089.09's.
* **`InstallLog` creates nothing.** The lines wait until `prepare_home` has made the folder,
  because a MyScribe that already serves and a volume with too little room both end before
  it, and a log file is not a reason to build a folder under either. Two existing tests
  assert exactly that ("nothing was prepared, synced or started") and they still pass.
* **The console door starts no child without a TTY.** The engine would spend about four
  seconds working out a plan nobody can answer; the launcher says the line itself.

### What criterion 6 got that it did not ask for

The criterion enumerates exit codes 3, 2 and 1 and omits 4. `scribe/models.py`'s
`EXIT_CODES` is `{mismatch: 2, token: 3, disk: 4}`, so a full disk during the weights
download exits 4. Reported rather than silently widened: named sentences for 2, 3 and 4, and
a generic one for anything else. Exit 2's sentence names the contract mismatch and the
pinned file, and deliberately not the token refusal, which the launcher can no longer
provoke (`test_the_token_refusal_is_not_among_the_sentences`).

### What this closes for TASK-089.13

Its criterion 1 says the proof "mirrors the report into the install log". That had no
referent: there was no `<home>/logs/launcher.log` and the launcher did not call `--prove` at
all. Criterion 7 here creates the file and `run_prove` reports every line through the tee, so
that sentence is now true.

### What ADR-017 was promised, and what was measured

`Launch.stop_setup` uses `terminate()` on the setup child and never the tree kill
`AppProcess.stop` uses, so that no tree kill can pass over an Ollama a third-party installer
has just started (M10). What a single-process stop can orphan is named in those words: at
most a version probe, which ends by itself within the 15 s timeout of `scribe/doctor.py`'s
`_run`. `test_quit_during_a_download_stops_the_setup_child_alone` starts a real python child
that spawns a real grandchild probe, calls Quit, and asserts the child is gone at once, no
`taskkill` was run, and the probe is gone inside the ceiling - polled, not slept.

ADR-017's other half - "while a third-party installer runs, refuse Quit and say why" - has no
referent here: no installer exists in the engine yet. TASK-089.18 criterion 13 owns it, and
nothing in this task or in the README says Quit is clean.

### What the `--smoke` claim is, exactly

`--smoke` applies `{}` over stdin before `/health` and writes
`<home>/logs/launcher-smoke.log`, which `build_release.smoke_log()` prints on a non-zero
exit - the Windows binary is windowed and has no console, so a failed smoke said only
"exit 1" before. The asserted claim is narrow and read off the engine's own word:
`saved: nothing`, meaning no setting row. `{}` does create the directories, migrate the
database and write a stamp. Nobody should read the smoke as proof that it touches nothing.

### What is half-measured, and which half

Criterion 5's bar and criterion 6's error state are Tk. The fake-tkinter harness in
`tests/test_launcher_sitting.py` proves which widget was built, with which text, which
variable and which command, and what the sitting hands over when a button is pressed - for
the bar, that a `ttk.Progressbar` is built, is given 100, and that a hundred progress lines
produce one bar and no log lines. It says nothing about pixels. Nobody has seen the real Tk
window of this dialog, and whether Tk shows at all under WSL has been tried by nobody. That
is criterion 13's, and criterion 13 is not done.

### Criterion 13: the commands for a person at a real screen

Not run. It needs somebody at a screen, once per operating system.

Windows (Robert), from the repository, with a home nothing else uses:

    .venv\\Scripts\\python packaging\\launcher\\myscribe_launcher.py --home %TEMP%\\myscribe-firstrun --port 4299

Expected: the folder question first (TASK-089.14), then "Installing the speech engine...",
then one window titled "Set up MyScribe" listing what was found on this machine with its
sources and only the questions that are open, each with its own Skip. Type a Hugging Face
token, press "Save and start": a progress bar that moves and a headline naming the
repository, then the proof report, then "MyScribe is running at http://127.0.0.1:4299/".
Afterwards, and this is the point of the whole task:

    findstr /C:"<the token you typed>" %TEMP%\\myscribe-firstrun\\logs\\launcher.log

must find nothing, and the Setup button must reopen the sitting with the tier question asked
again.

Linux, in WSL, through the console door (whether a Tk window shows there at all is unknown,
which is why this is the console):

    python packaging/launcher/myscribe_launcher.py --home /tmp/myscribe-firstrun --headless --port 4299

Expected: the engine's own asker on the terminal, with the token read by `getpass` so it is
not echoed and is not in the shell's scrollback; then the proof report and the app. Without
a terminal (`... --headless < /dev/null`) exactly one line, naming `--apply-stdin`, and no
child at all.

macOS: the Mac is somebody else's (G9, Robert's decision of 2026-09-20). Its steps are the
Windows ones with `~/Library/Application Support` as the home, and they belong to the
bundled macOS list of TASK-089 criterion 10. Reads "not run".

### Not done here, and why

No acceptance criterion is ticked and the task is not Done: the orchestrator verifies. Two
things found outside the criteria and deliberately left alone: `scribe.setup`'s exit 2 is
still overloaded (usage error, contract mismatch, models' mismatch), and the plan document
has no url field for the conditions link. Both are TASK-089.09's.

## Corrections and the evidence that was missing (agent, 2026-09-22, same sitting)

Four things were checked after the notes above were written. Two of them were gaps in the
evidence, two are corrections to what those notes said.

### 1. The red for criteria 2 and 6, reconstructed and labelled as such

Criterion 2 says "Red first" and the method says the same for criterion 6.
`red-criterion-3.txt` was captured live; `tests/test_launcher_sitting.py` was not - it was
written after `ask_setup` had been rewritten, so its first run was a harness being debugged
and not a red. Reconstructed afterwards against the saved pre-change file
(`launcher-before.py`, the launcher of 9f2611a) on a copy under the scratch directory, with
nothing else changed:

    cp launcher-before.py mut/packaging/launcher/myscribe_launcher.py
    cd mut && pytest tests/test_launcher_sitting.py -q ...   ->  51 failed, 1 passed

Collection stopped first on `AttributeError: module 'myscribe_launcher' has no attribute
'GATED_MODEL'`, so the eight missing module constants were appended to the copy - names
only, never behaviour - and the run repeated. The signatures are what the criteria are
about: `TypeError: ask_setup() takes 2 positional arguments but 3 were given` (criterion 2:
the old dialog could not be handed a plan), and `has no attribute` for `setup_failure`,
`ask_failure`, `InstallLog`, `found_lines`, `progress_line`, `plan_command`,
`prove_command`, `console_sitting`, `link_label`, `setup_plan`, `run_prove`, `open_sitting`,
`Launch.apply` and `Launch.remember`. Kept as `red-criteria-2-and-6.txt`.

The one test that passed against the old code is
`test_a_log_that_cannot_be_written_stops_nothing`, and it passed vacuously: the old
`Launch.report` never opened a file, so there was nothing to fail. Its whole claim rests on
the tee existing, and it is the only test in that file that says nothing on its own.

This is reconstructed, not captured live. Said plainly because half the evidence is worse
than none.

### 2. Criterion 12's claim, measured against the real engine

Every test of `--smoke` uses a stub engine that prints "saved: nothing" because it was
written to. The release build fails unless the real engine does, so it was run
(`measured-empty-document.txt`, fresh data directory, fence exported):

    echo '{}' | .venv/Scripts/python -m scribe.setup --apply-stdin
    saved: nothing
    exit code: 0

And what `{}` does write, which the notes above promise to name:

    logs/  media/  models/  work/  myscribe.db  setup.json
    setup.json: {"contract": 2, "ended": 1790110826.15,
                 "questions": {"hf_token": "not_needed", "llm_key_openrouter": "not_needed"}}

So: no setting row, and a stamp, a migrated database and five directories. The smoke asserts
the first and is not proof of the rest.

### 3. Criterion 8: which half is here, and which half is the engine's

The criterion asks for "a test that the gpu-smoke is never called on that path". It is split,
and the split is deliberate: the launcher's half is
`tests/test_launcher_sitting.py::test_the_proof_is_told_which_port_myscribe_answers_on`,
which pins that `--port` is passed and with which number - a launcher on 4299 that told the
engine 4242 would let the child load a model beside a running app. The "never called" half
is the engine's and was measured when `--prove` was built:
`tests/test_setup_prove.py::test_an_app_serving_this_library_gets_the_doctor_job` and
`::test_the_gpu_runtime_line_waits_on_the_same_gate`, both with a model loader that raises.
The launcher imports nothing from `scribe` (ADR-011), so it cannot reach that loader to
double it; asserting it here would be a second copy of somebody else's test.

### 4. Criterion 13's commands, corrected

The first version of the Windows command ran the launcher straight out of the checkout.
That does not work: `payload_dir()` falls back to `packaging/launcher/payload`, which a
checkout has not got, so it would fail on step one. A real first run is the built artifact,
which is also what CI's smoke runs. Replacing the commands in the notes above:

Windows (Robert), the artifact, which is the honest one:

    .venv\\Scripts\\python packaging\\build_release.py --platform windows-x64
    dist\\MyScribe-<version>-windows-x64.exe          (the installer; or run the frozen
    build\\frozen\\MyScribe\\MyScribe.exe --home %TEMP%\\myscribe-firstrun --port 4299)

Or from the checkout, with a payload built first:

    .venv\\Scripts\\python packaging\\build_payload.py --platform windows-x64 --out build\\payload
    .venv\\Scripts\\python packaging\\launcher\\myscribe_launcher.py ^
        --payload build\\payload --home %TEMP%\\myscribe-firstrun --port 4299

Expected, either way: the folder question first (TASK-089.14), then "Installing the speech
engine...", then one window titled "Set up MyScribe" listing what was found on this machine
with its sources and only the questions that are open, each with its own Skip. Type a
Hugging Face token, press "Save and start": a progress bar that moves and a headline naming
the repository, then the proof report, then "MyScribe is running at
http://127.0.0.1:4299/". Afterwards, and this is the point of the whole task:

    findstr /C:"<the token you typed>" %TEMP%\\myscribe-firstrun\\logs\\launcher.log

must find nothing, and the Setup button must reopen the sitting with the tier question asked
again.

WSL, the console door (a payload built the same way, `--platform linux-x64`):

    python packaging/launcher/myscribe_launcher.py --payload build/payload \\
        --home /tmp/myscribe-firstrun --headless --port 4299

Expected: the engine's own asker on the terminal, the token read by `getpass` so it is not
echoed and is not in the shell's scrollback, then the proof report and the app. Without a
terminal (`... --headless < /dev/null`): exactly one line, naming `--apply-stdin`, and no
child at all. Whether a Tk window shows under WSL is still unknown and is why this door is
the console one.

macOS is unchanged: not run, bundled into TASK-089 criterion 10 (G9).

### 5. A risk named rather than fixed

`run_window` does `from tkinter import scrolledtext, ttk`, and `--smoke` never opens a
window - so a `ttk` missing from a frozen build would ship undetected, which is the failure
mode `show_error`'s own docstring reasons about for `messagebox`. `scrolledtext` is imported
by the same statement and does ship today, and the PyInstaller call adds no hidden imports
and relies on the same analysis for both, so there is no reason to expect a difference. If a
frozen build ever disagrees, the fallback is a Canvas-drawn bar, which needs no submodule.
Not measured: nobody has opened this window in a frozen build. Criterion 13 is where that
would show.

### Final runs

    green-test_launcher.txt          99 passed, 1 skipped in 31.10s
    green-test_launcher_sitting.txt  52 passed in 11.19s
    green-test_proxy.txt             29 passed in 16.66s

Two tests were added to `tests/test_launcher.py` while correcting the above, because
criterion 6 asks for them and `open_sitting` alone cannot answer them:
`test_continue_without_starts_myscribe_anyway` and
`test_retry_holds_the_sitting_again_and_then_the_app_starts`. The one in the sitting file
was renamed to what it actually checks:
`test_retry_holds_the_whole_sitting_again`.

## What the review changed (agent, 2026-09-22)

Three verifiers read this. Two majors were right and are fixed; one finding is
rejected with its reason; the rest are corrections to this record.

### Fixed: every setup child is now stoppable (ADR-017's M10)

There are three setup children - `--plan`, `--apply-stdin` and `--prove` - and
only the download was registered with the `Launch`. `stop_setup()` therefore
could not reach the other two. The proof child is the worse of the two: on a
first run nothing answers the port yet, so the engine's gate lets that child
load a model (`scribe/setup.py` `_gate`) - which is what the proof is for - and
Quit during "Checking what this machine can do..." left a python holding the
card behind a launcher that had already gone. The plan child is the cheaper
case: about four seconds, and the same hole.

`Launch.watching(run, ...)` is the one place that keeps a handle and lets it go
again; `Launch.apply`, `open_sitting` and `console_sitting` all go through it,
and `setup_plan` and `run_prove` gained the `on_start` that `run_setup` already
had. The engine's gate is not broken and was not touched: while MyScribe
answers, the same child queues the doctor job (TASK-089.13), so criterion 8's
claim is unchanged.

Red first, and kept: `red-unregistered-setup-children.txt` - 2 failed in 66.60s,
"the proof child was not stopped" and the same for the plan child.

### Fixed: criterion 2's central rule was not measured

The verifier's mutation run showed it: `opening_value` mutated to
`current or default` left the whole suite green. Neither fixture built the
discriminating plan - one had a non-empty `current`, the other no `default` -
so the rule the criterion is about (ADR-016: a window may not answer on
somebody's behalf) was read and not measured.
`test_a_group_whose_stored_value_is_empty_opens_on_nothing` now holds
`a_choice(current="", default="ollama")` and asserts every group variable is
"". The mutation bites: `mutant-opening_default.txt`, 1 failed,
"assert {'ollama'} == {''}".

### Fixed: the plan is read out of the middle of the output

The comment claimed the `text.find("{")` slice protected the plan from the
child's stderr, and it only covered brace-free prose before the document.
Anything printed after it - a `warnings` line, an "Exception ignored in:" at
shutdown - made `json.loads` raise, and the sitting silently did not open with
"could not be read (exit 0)", which reads as a contradiction.
`json.JSONDecoder().raw_decode` from each brace in turn now tolerates both
sides. A loop needs one more rule than a single read did: a stderr line can
itself be a JSON object, and the first thing that decodes would have become the
plan - a sitting saying nothing is open and a stamp for questions nobody was
asked. The candidate has to carry `questions`, which is in every contract-2 plan
and is the key this file already reads. New test with a real child that prints on
both sides, flushed, one of those lines a whole JSON object:
`test_a_plan_is_read_although_the_child_said_something_around_it`. Two mutations
on it: the old read-to-the-end (`mutant-loads_the_tail.txt`, 1 failed) and the
first object that decodes (`mutant-first_object_wins.txt`, 1 failed).

Said plainly because it nearly passed: the first run of the `loads_the_tail`
mutation was GREEN, and the test was vacuous. The child's stdout was block
buffered behind the pipe, so its trailing stderr line arrived before the document
instead of after it and there was nothing on the far side to trip over. Both
prints are `flush=True` now, and the mutation bites.

### Fixed, smaller

* The yes-no Checkbutton got `command=refresh` and the secret Entry a
  `<KeyRelease>` binding. `shown()` is generic over question ids; the wiring
  was not, so a `shown_if` whose subject was not a choice would have revealed
  nothing until some other control was touched. Not reachable today - the
  engine puts `shown_if` on `llm_provider` only - and now it cannot become so
  quietly.
* `test_the_window_has_a_setup_button_beside_the_other_three` now presses the
  button and asserts `force_setup is True`. The label alone would have passed a
  button wired to the start's plan, which is the one route back to a question
  somebody skipped.

### Rejected, with the reason

`console_sitting`'s bare `sys.stdin.isatty()`. The verifier is right that NUL
is a character device on Windows and answers True, so a `--headless` launcher
with NUL on stdin takes the TTY branch. It is not fixed here: criterion 9 scopes
this door to macOS and Linux, the Windows binary is windowed with no console,
and the engine decides for itself in `at_a_terminal` (`scribe/setup.py`) and
prints its own "nothing was asked" line, writing no stamp. Mirroring that check
in the launcher is new untestable surface for a path the criterion does not
cover. The docstring's "four seconds" claim, which was the false part, is gone
and now names the engine as the decider.

### Corrections to the record above

* The Evidence table above said 97 passed and "Five more were revised" before
  naming seven; both were corrected in place on 2026-09-22 rather than left
  standing beside a footnote. The run really read 99 passed, 1 skipped, and
  `tests/test_launcher.py` holds 100 `def test_` lines.
* Of the seven moved tests, one claim did not survive and cannot:
  `test_the_provider_question_says_that_picking_chooses_who_answers` pinned
  wording the launcher no longer owns - the text comes out of `--plan`. That
  claim is the engine's (`scribe/setup.py`'s question text) and is not doubled
  here, per ADR-011.
* Criterion 10's bite is `mutant-tree_kill.txt` (1 failed, on ADR-017's own
  sentence). The `tree_kill` entry in `mutation-runs.txt` is a different, weaker
  run that died on a `FileNotFoundError` from subprocess; read the separate file.
* Criterion 10's orphan probe is a 3 s `time.sleep` stand-in. The 15 s figure is
  `scribe/doctor.py`'s `_run` timeout, not something this test measures; the
  test polls to that ceiling but nothing is measured against it.
* Criterion 4's sharper mutation is the verifier's 2b
  (`saved and any(value is not None ...)`, `mut-verify-2b-all-skips-sitting.txt`,
  3 failed): that one fails
  `test_save_with_every_question_skipped_is_still_a_sitting` itself. The
  mutation reported here is caught by a neighbour instead.

### One more deviation, said where it is decided

An untouched control and an explicitly ticked "Skip this question" are the same
record: both hand over None, `from_document` writes both as `skipped`, and
`skipped` is in `PUT`, so `--plan --unasked-only` never puts that question
again. Pressing "Save and start" past a radio group nobody touched therefore
accepts that question's `if_skipped` consequence, and the Setup button is the
way back to it. The console door of the same engine makes a skip explicit by
typing `s`. Contract 2 has no third state for "on screen, untouched"; adding one
is a CONTRACT bump and TASK-089.09's. `answer_of`'s docstring now says this.

### Green after the review

All behind the TASK-090 fence, one test file per process:

    final-test_launcher.txt          99 passed, 1 skipped in 39.85s
    final-test_launcher_sitting.txt  55 passed in 16.41s
    final-test_proxy.txt             29 passed in 28.31s

Four mutations on a copy, one change per run (`mutate-fix.py`, controls
`mut-fix-baseline-sitting.txt` 55 passed and `mut-fix-control-2.txt` 52 passed):

    opening_default     a group opens on the plan's default   -> 1 failed
    unwatched_children  no setup child is registered          -> 3 failed
    loads_the_tail      the plan is read to the end, once     -> 1 failed
    first_object_wins   any JSON object becomes the plan      -> 1 failed

`grep -rn MUTANT scribe tests packaging` is empty, and the three ADR greps over
the launcher (ADR-015's Verification, its tripwire, ADR-011's scribe import) are
still empty.

Criteria 5, 9 and 13 are unchanged by this review: the unmeasured halves are a
real Tk window, WSL, and the per-OS walkthrough, and all three belong to
criterion 13, which stays unticked.

## Verification (orchestrator, 2026-09-23)

### The gate this task closes, measured

The reason nothing on this branch could be released since TASK-089.09: a Tk
first run in which somebody typed a Hugging Face token lost the whole sitting.
The same sitting, through the door this task builds, fenced:

    document over stdin: {contract: 2, answers: {hf_token: <sentinel>,
                          llm_provider: ollama, default_tier: max}}
    before  exit 2, data directory empty, no row, no stamp
    after   exit 0, rows {llm_provider: ollama, default_tier: max},
            stamp written, sentinel in neither stdout nor stderr,
            "still open: hf_token"

That last line is TASK-089.09's criterion 6 doing its job - the sentinel is
not a real token, the Hub refuses it, it is not saved and comes back in
`reopen` - while the rest of the sitting survives. Before, the refusal took
provider, tier and the stamp with it.

    grep -n -- "--hf-token" packaging/launcher/myscribe_launcher.py   -> nothing
    grep -nE "^\s*(import|from)\s+scribe" packaging/launcher/...       -> nothing

The first is ADR-015's own Verification line; the second is ADR-011's Must.
`setup_command(layout)` no longer takes answers at all: it is
`[env_python, -m, scribe.setup, --apply-stdin]` and its docstring says why no
answer will ever be on that list again.

### Suite, with one honest gap

TASK-089.10 was building in this same working tree while this task closed -
deliberately, to stop serialising - and at this moment it has written its
red-first tests into `tests/test_web_ai.py` and `tests/test_stage_finalize.py`
without yet changing the code they test. Those two files are therefore red by
design and are not this task's. The suite was run over everything else, one
file per process, fenced:

    81 files: 2946 passed, 10 skipped, 0 failed, 0 errors
    tests/test_web_ai.py           SKIPPED (TASK-089.10 is mid-flight)
    tests/test_stage_finalize.py   SKIPPED (TASK-089.10 is mid-flight)
    collection over the same set:  "2956/2966 tests collected (10 deselected)"
    2946 + 10 = 2956

This task's own files: `tests/test_launcher.py` 99 passed, 1 skipped (the
POSIX symlink test), and the new `tests/test_launcher_sitting.py` 55 passed.
The whole suite, both files included, runs again before TASK-089.10's commit
and is recorded there. Only this task's files are staged in this commit; the
three the other agent owns are left in the tree.

### What the fix round caught that would have shipped

ADR-017's Must - stop the setup child alone, never by its tree - covered one
of three children. `--plan` and `--prove` were started without a handle, so a
Quit during either fell outside the single-process stop. That is exactly the
hole through which a tree kill could have passed over an Ollama a third-party
installer had just started. All three are registered now, pinned red first.

And criterion 2's central rule - a radio group opens on `current`, never on
the plan's `default` - was asserted by nothing until the mutation pass put
`default` back and the suite stayed green. It is pinned now.

### Criteria 5 and 9

Ticked. The implementer marked both "partly" and the verifier argued both
read "met" against the criterion text with the pixels and the WSL sitting
carried by criterion 13. I agree: what the criteria ask is built and measured
on the fake-tkinter harness; what nobody has seen is the same thing criterion
13 exists for.

### Criterion 13 stays open

One recorded real first run per OS with a fresh `--home`: Robert on Windows
and in WSL through the console door; macOS is bundled into TASK-089 criterion
10 (G9). Every run here was on the harness; nobody has seen the real window.
The stray `C:\Users\rvdbr\AppData\Local\MyScribe` must be gone before that
run, as TASK-089.14's notes already say.

### Carried, not fixed here

* Contract 2 has no third state for a question that was on screen and
  untouched: a Save past an untouched radio group records `skipped`, and
  `--plan --unasked-only` never puts it again. Fixing it properly is a CONTRACT
  bump, which is TASK-089.09's file and TASK-089.11's gate to change together.
* `scribe.setup`'s exit 2 is overloaded four ways and the engine cannot tell
  a caller which it meant; `reopen` reaches the launcher as prose rather than
  a JSON line. Both TASK-089.09's.
* The launcher now reads `questions` as a fifth contract key; a plan without
  it is reported as unreadable rather than rendered empty.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The launcher's first run is now: location, tools, sync, plan, one sitting, apply, prove, start - one function with injected callables, and a test asserts that order. The sitting is drawn from `scribe.setup --plan`: a "Found on this machine" block with sources only, then only the open questions, each with its own Skip and its if_skipped sentence, radios opening on the current value and never on the plan's default (pinned after a mutation showed nothing held it), shown_if the only condition the launcher interprets. The answers go back as one JSON document on the child's stdin and no secret ever reaches its argv - ADR-015's own grep for --hf-token finds nothing, and a sentinel appears in no argv and in no line of the new <home>/logs/launcher.log, into which every reported line is teed with secrets redacted by value. That log also closes what TASK-089.13 owed: its proof now has an install log to mirror into. JSON progress lines drive a bar and the headline instead of a log line per MiB; exit codes 3, 2, 1 and 4, and any reopen, become an error state with Retry and Continue without; a Setup button beside the other three and --setup reopen the sitting on a full plan, so a skipped question is asked again; while the app runs the launcher passes its port to --prove, which queues the doctor job rather than loading a model, proven with a smoke double that raises. Quit stops the setup child with terminate(), one process and never the tree kill the app gets (ADR-017's Must) - and the fix round found that --plan and --prove were started without a handle, so two of three children fell outside that stop; all three are registered now. --smoke applies {} over stdin before /health and writes launcher-smoke.log, which build_release.py prints on failure. Measured by the orchestrator: the sitting that lost everything since TASK-089.09 - a typed token plus provider and tier - now exits 0 with both rows and the stamp written and the token echoed nowhere, so the release gate that stood since 973a5d5 is closed. Suite over 81 files, fenced, one per process: 2946 passed, 10 skipped, 0 failed, reconciling exactly with 2956 collected; tests/test_web_ai.py and tests/test_stage_finalize.py were deliberately left out because TASK-089.10 was writing its red-first tests there in the same tree, and the whole suite runs again before that task's commit. Criterion 13 stays unticked: one recorded real first run per OS with a fresh --home, Robert's on Windows and in WSL, the Mac somebody else's.
<!-- SECTION:FINAL_SUMMARY:END -->
