---
id: TASK-089.15
title: >-
  The launcher's first run syncs, shows what was found, asks what is open and
  shows failures
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
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

Needs a real machine: A person at the screen once per OS: Robert on Windows, and in WSL for the Linux console door. macOS: Robert, if the Mac of TASK-040.07 (a session on 2026-09-19) is still his to use - not confirmed; otherwise reported as not done. Nobody has run the real Tk window.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The first run is one function with injected callables, in this order: location, tools, sync, plan, sitting, apply, prove, start. A test asserts that order.
- [ ] #2 Red first (today a re-run with Save resets the provider to ollama and the tier to turbo): the dialog is drawn from `--plan`. It shows a 'Found on this machine' block with sources only, then only the open questions. Each question has its own Skip and its if_skipped sentence, and radios start on the current values. shown_if is the only condition the launcher interprets.
- [ ] #3 Answers reach the child on stdin. A test asserts that no secret appears in the child's argv. setup_command's answer flags go (packaging/launcher/myscribe_launcher.py:474-484), `--hf-token` (:475-476) and `--diarize/--no-diarize` (:481-482) among them, and tests/test_launcher.py:400-407, which pins them, is revised knowingly with its diff shown.
- [ ] #4 'Save and start' stamps the sitting and 'Ask me next time' does not, as TASK-089.11 defines them. Neither writes a speakers guard: there is none (brief: W6).
- [ ] #5 JSON-line progress drives a progress bar and the headline. There are no per-MiB log lines.
- [ ] #6 Exit codes 3, 2 and 1, and any `reopen`, become a visible error state with Retry and Continue without.
- [ ] #7 Every reported line is teed to <home>/logs/launcher.log with secrets redacted.
- [ ] #8 A Setup button and `--setup` reopen the sitting on current state. The button is new: today the window has 'Open MyScribe', 'Open data folder' and 'Quit' (packaging/launcher/myscribe_launcher.py:613-623). A question recorded as skipped is asked again in a sitting opened from the button; that route's test lives here and not in TASK-089.11, which is built first. A sitting opened from the Setup button while the app runs loads no model in the setup child: the launcher passes its port, and the proof follows TASK-089.13. A test asserts the gpu-smoke is never called on that path.
- [ ] #9 On macOS and Linux with --headless, or without Tk, the console asker runs with the terminal attached when stdin is a TTY. Without a TTY one line says nothing was asked, and how to ask. The Windows binary is windowed and has no console; Tk is always there.
- [ ] #10 Quit during a MyScribe download stops the setup child, and a test shows that no orphaned python process survives. How: the launcher keeps the child's handle and stops that one process - terminate(), not the tree kill it uses for the app (packaging/launcher/myscribe_launcher.py:376-380) - so that no tree kill passes over an Ollama the installer has just started. The design spec and ADR-015's Must say the same, and the notes confirm it or say what changed. What a single-process stop can orphan is said in those words: at most a version probe that ends by itself within 15 s (scribe/doctor.py:116-125), and the test waits that long. What Quit may promise around a freshly installed Ollama is for TASK-089.18 to establish first (brief: M10).
- [ ] #11 The Hugging Face conditions link is clickable. The hard-coded provider tuple (:561) and the '1.6 GB' literal (:574) are gone from the launcher.
- [ ] #12 `--smoke` applies `{}` over stdin before /health, and asserts that no setting row was written and that the app still serves. It writes <home>/logs/launcher-smoke.log, which build_release.py prints on failure.
- [ ] #13 Needs a person at the screen: one recorded real first run per OS with a fresh --home. Robert does Windows, and Linux in WSL through the console door; whether the Tk window shows under WSL has been tried by nobody. macOS needs a real Mac. Robert answers it if the Mac that TASK-040.07 records a session on (2026-09-19) is still his to use; that was not confirmed when these tasks were written, and no Mac was available in the design run. If it is not run, the box stays unticked and the parent's final summary lists it.
<!-- AC:END -->
