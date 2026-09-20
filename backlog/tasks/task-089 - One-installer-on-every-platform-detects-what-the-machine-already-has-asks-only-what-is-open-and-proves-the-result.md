---
id: TASK-089
title: >-
  One installer on every platform detects what the machine already has, asks
  only what is open, and proves the result
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - ux
  - llm
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
priority: high
ordinal: 137000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert asked on 2026-09-20 for one installation that works on all platforms. It asks for API keys and for the Hugging Face token only when none is found, every question can be skipped, it downloads the models, and it offers to install Ollama with a model - but only when Ollama is absent: 'Als Ollama er al is, dan niets doen.' His rule for the scope: 'Liever een paar extra vragen tijdens installatie, dan weer los extra dingen doen.' A standalone Python installer for a plain clone comes alongside the per-OS installers, not instead of them.

Ten readers mapped today's onboarding and found 112 gaps. The ones that decide this work were each opened and confirmed when these tasks were written:
- A release user's first 'Save and start' cannot work: begin() runs scribe.setup with the environment's python before launch.run() has created that environment (packaging/launcher/myscribe_launcher.py:651-668 against :433-438).
- Choosing OpenRouter or OpenAI saves the provider and asks for no key: Answers has no key field (scribe/setup.py:46-53), and ai_run queues without looking for one (scribe/web/ai_ui.py:815-870).
- A machine where nobody chose a provider falls through to OpenRouter (scribe/llm/tasks.py:456, scribe/llm/__init__.py:175).
- Nothing on the install path looks at Ollama, while 'Ollama, on this machine' is the preselected radio (myscribe_launcher.py:558).
- A clone user is never asked anything: scribe.setup takes flags only (scribe/setup.py:158-168), and the start scripts still point at requirement files that no longer exist (scripts/start.sh:35-40).

The shape, proposed in ADR-015: `python -m scribe.setup` becomes the one place that detects, asks, writes and proves, behind a JSON contract (`--plan` out, answers in over stdin). Three doors render it: the frozen launcher, a stdlib `install.py` for a clone, and the headless launcher on a terminal.

Decided by Robert on 2026-09-20. No provider row means no provider (R1). An Ollama that is already there is left alone, in every state (R2). Three more questions go in: adopt an existing library, a folder to watch, start MyScribe at login (R3). This step records only: a design spec, these tasks and ADR-015 as Proposed, and no code (R4).

Declined, and not to be built: a start script or shortcut for a clone. Robert did not select it.

Deliberately not asked, so nobody adds them later: the transcription language (removed on evidence on 2026-09-02, scribe/web/transcribe_dialog.py:64-69), a timezone, a UI language, and a GPU-versus-CPU choice (ADR-012: one lock, a per-platform torch source) (brief: U9). 'Where should Ollama keep its models?' is Ollama's own configuration and is not included; it stays an open question for Robert (brief: U7).

TASK-089.01 lives under this parent only, although it ships alone as a patch release (brief: W7). None of these tasks closes TASK-040's open hardware criteria.

Verified by nobody when these tasks were written, and every task that leans on one says so: the real Tk window; anything on macOS; whether `OllamaSetup.exe /VERYSILENT` starts the daemon; an OpenRouter key-check endpoint; mlx-whisper loading from a local folder; process lineage under `taskkill /T` after the Ollama installer exits; how MyScribe's own loopback probes behave behind a proxy - the two library defaults they are built from were measured once, on Robert's Windows machine on 2026-09-20 (TASK-089.05), and that the product's probes behave the same is an inference. The Ollama install commands per OS were read by a reader agent from Ollama's README and the GitHub API in the design run, and must be re-verified before anything relies on them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Needs a person at the screen: a release install on a Windows machine with a fresh home, watched by Robert. The location question appears before anything is downloaded, the other questions after the sync, and the app runs afterwards. The recording or the notes show it; nobody has seen the real Tk window before this.
- [ ] #2 `python install.py` in a fresh clone ends on the proof report on Windows and in WSL, with full transcripts shown. macOS needs a real Mac. Robert answers it if the Mac that TASK-040.07 records a session on (2026-09-19) is still his to use; that was not confirmed when these tasks were written, and no Mac was available in the design run. If it is not run, the box stays unticked and this task's final summary lists it.
- [ ] #3 On Robert's machine the sitting asks no credential question and no Ollama question, and `ollama list` and GET /api/version are identical before and after.
- [ ] #4 Every question can be skipped. A sitting with every question skipped writes no setting row and leaves `.env` unchanged, and afterwards nothing is sent to any provider until somebody chooses one.
- [ ] #5 No proof in any subtask ran on the live library: each one names the scratch or copied data directory it used.
- [ ] #6 No secret appears on a command line, in setup.json, in a log or in any printed output, and no MyScribe code path - launcher, install.py or engine - passes one on argv. The one standing exception is named here so nobody finds it by surprise: the documented `--hf-token` flag (README.md:224), which a person may still type until Robert decides whether it survives (TASK-089.09 criterion 5, and ADR-015's open question). Nothing in the product uses it. One SENTINEL test covers all of them, and it is shown to bite on a COPY of the repo.
- [ ] #7 ADR-015 is Accepted by Robert, or the subtasks that depend on it are revised to match what he changed.
- [ ] #8 Each item in the 'verified by nobody' list is either answered by a named run in a subtask's notes, or still listed as unverified in this task's final summary. Every criterion that needed a Mac or other hardware and was not run is listed there by its task key.
- [ ] #9 The design spec records what was declined and what is deliberately not asked, with the reason for each, and the open question about where Ollama keeps its models.
<!-- AC:END -->
