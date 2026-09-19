---
id: TASK-040.06
title: First run asks what it needs and proves the machine can transcribe and diarize
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-18 20:26'
updated_date: '2026-09-19 05:54'
labels:
  - packaging
dependencies:
  - TASK-040.05
references:
  - packaging/launcher/myscribe_launcher.py
  - scribe/doctor.py
  - scribe/web/ai_ui.py
parent_task_id: TASK-040
priority: high
ordinal: 131000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The launcher's first run prepares an environment and opens a browser; it asks nothing and proves nothing. Two failures on 2026-09-18 came straight out of that gap: scribe.doctor reported 'All required checks passed' on a machine where diarization was impossible (it inventories cached models but never checks the diarize pipeline can load), and diarize.py's own error tells the user to 'store the token as the hf_token setting' - a setting the Settings page has no field for, so the only route is raw SQL or .env.

Four things the first run should settle, chosen by Robert on 2026-09-18: validate transcribe and diarize for real, ask for the Hugging Face token when one is still needed, ask which provider answers questions about a transcript (local Ollama or a cloud one), and let the user pick a model tier rather than committing a small machine to a 1.5 GB download and a 31 GB resident model.

Settings has the same reporting gap: it shows a default provider and a model per provider, but never states what is actually in force. On this machine that is Ollama with qwen3.8:27b-q8_0 at a 32768-token window, and reading it takes three controls and the knowledge that each question can override the default.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 scribe.doctor fails, not passes, on a machine where the diarize pipeline cannot load, and says which of the three routes to fix it
- [x] #2 First run asks for a Hugging Face token only when diarization still needs one, writes it to the per-user .env, and links the conditions that must be accepted
- [x] #3 Settings has a field for the token the diarize error already tells people to set
- [ ] #4 First run asks which provider answers questions, and Settings states the effective provider, model and window in one line
- [x] #5 First run offers a model tier and does not download a tier the user did not choose
- [x] #6 The setup can be re-run from the app afterwards, and every answer it writes is visible in Settings
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Done so far, verified.

AC1 - doctor.check_diarization(): the three routes from _no_weights_hint asked cheaply (a local pipeline directory, or a HEAD for the gated config with the token), never a pipeline load. Optional, because transcription works without it, but always reported: SKIP with the reason beats a green card and a failed job an hour later. Kept out of WEB_SAFE_CHECKS for ADR-001's reason - it imports the diarize stage and reaches the network.

It also caught a second false negative while being written: python -m scribe.doctor never loaded .env (only scribe/__main__.py did), so it reported 'no Hugging Face token' with a token in the file beside it. doctor.main() now loads it, and the card reads 'diarization  pyannote/speaker-diarization-community-1 reachable with this token'.

AC3 - the token field, beside 'Recognise speakers' rather than with the AI providers, because that is what it buys. Its own form: a secret is not a preference. Same manners as a provider key - the value is written to setting, never rendered back, shown as dots with the name of the source that answered, so a stale HF_TOKEN outranking a token just typed is visible. Verified through the running app: save -> 303 and the row is stored, the page shows the mask and switches its source line from HF_TOKEN to settings, the value appears nowhere in the HTML, clear -> the row is dropped and the environment decides again.

Suite 2411 passed; 9 new tests (4 doctor, 5 settings).

Left: the first-run questions themselves (AC2, 4, 5, 6) - they live in the launcher's Tk window and are the larger half.

First run asks the four questions in one modal window before the app starts, so a token given there is in .env before anything reads it. Skippable, re-runnable with --setup, and every answer is in Settings afterwards. The launcher only asks: it hands the answers to python -m scribe.setup in the app's environment (ADR-011 keeps it frozen and stdlib-only), and a blank answer is not passed at all so it cannot overwrite a setting the user already had.

Two things the tests caught while being written. TranscribeOptions is a pydantic model, not a dataclass, so dataclasses.replace raised - model_copy keeps its validation in play. And paths.MODELS_DIR is a module constant computed at import, so moving DATA_DIR in a test does not move it: needed() was answering about the real pipeline on this machine rather than the temporary one.

Suite 2431 passed; 10 new tests (7 setup, 3 launcher).

Left: AC1 is done and AC3 was done earlier; what remains is a visible launch of the Tk dialog, which needs a person at the screen - the window is unverified the same way TASK-040.03's notes say the launcher's own window is.
<!-- SECTION:NOTES:END -->
