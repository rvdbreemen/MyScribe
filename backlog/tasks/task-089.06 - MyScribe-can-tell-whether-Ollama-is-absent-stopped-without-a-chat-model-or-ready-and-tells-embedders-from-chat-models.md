---
id: TASK-089.06
title: >-
  MyScribe can tell whether Ollama is absent, stopped, without a chat model, or
  ready - and tells embedders from chat models
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - llm
  - settings
  - bug
dependencies:
  - TASK-089.05
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 143000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert's rule depends on a distinction nothing can make today: 'Als Ollama er al is, dan niets doen', and an offer only when it is absent. The one existing probe is OllamaProvider.available() (scribe/llm/ollama.py:384-409). A refused connection reads 'Ollama is not running', which is the same answer for 'not installed' and for 'installed but stopped'. An installer built on it would offer to install Ollama on a machine where somebody stopped it on purpose to free VRAM. scribe/doctor.py and scribe/setup.py do not mention Ollama at all.

'Has a model' is the wrong test too. available() accepts any pulled name (:403), and Settings' refresh stores every name it is given (scribe/web/settings.py:974-977). An embedder can therefore be picked as the chat model, everything shows green, and the first summary fails inside a job. A reader measured on Robert's machine on 2026-09-20 that five of its eight models are embedders, and that `OllamaProvider(model='bge-m3:latest').available()` answers ready.

OllamaProvider(conn) also ignores the saved llm_model_ollama: `conn` is 'accepted and unused' (:249-250) and the model is `model or self.default_model` (:254). provider_rows builds `cls(conn)` (scribe/web/ai_ui.py:1146), so Settings tests the class default qwen3.5:4b and not the model the app will use. Pull gemma4:12b and save it, and Settings still says qwen3.5:4b is not pulled and tells the user to download a model they do not need - about 3.4 GB, a figure a reader took from Ollama's library in the design run of 2026-09-20; it is not in the repository and was not re-verified.

Detection only. Nothing here installs, starts, pulls or configures anything; that is TASK-089.18. It comes early and depends on nothing heavy (brief: M9). It does follow TASK-089.05: the 'no API answer' leg is only trustworthy once a proxy cannot sit in front of the loopback probe.

Needs a real machine: Robert's machine for the ready state, and his WSL for the Linux install locations. The macOS locations need a real Mac: Robert, if the Mac of TASK-040.07 (a session on 2026-09-19) is still his to use - not confirmed; otherwise they are reported as not verified.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 scribe/ollama_setup.state() returns absent, installed_not_running, running_no_chat_model, ready or unknown (criterion 3), with the binary path, the version and the chat model names. It reuses OllamaProvider's GET /api/tags, which now returns the payload. There is no second HTTP client.
- [ ] #2 'absent' requires ALL of these: no `ollama` through shutil.which; none in the known install locations; no answer at 127.0.0.1:11434; and no OLLAMA_* variable in the process environment, `.env` or the registry. Any doubt reports present. The task notes say which macOS and Linux locations were verified against Ollama's own docs and which were not. On a real machine, Linux is checked by Robert in WSL. macOS needs a real Mac. Robert answers it if the Mac that TASK-040.07 records a session on (2026-09-19) is still his to use; that was not confirmed when these tasks were written, and no Mac was available in the design run. If it is not run, the box stays unticked and the parent's final summary lists it.
- [ ] #3 Chat-capable means 'completion' is in capabilities, and ['tools', 'embedding'] is not chat-capable. If the key is absent it falls back to POST /api/show. If neither answers the result is 'unknown': never 'ready', never 'absent', and treated as present wherever the state is used. MockTransport tests cover: five embedders only gives running_no_chat_model; the missing-key case; and a refused connection with a stripped PATH and empty fake directories gives absent.
- [ ] #4 Red first: with llm_model_ollama saved as gemma4:12b, OllamaProvider(conn).model is that tag. It is qwen3.5:4b today. The Settings readiness line tests the saved model.
- [ ] #5 Settings' model refresh stores chat-capable models only, so an embedder cannot be picked as the chat model.
- [ ] #6 `python -m scribe.doctor` gains an optional 'ollama' line that reports the state in words and never fails the gate.
- [ ] #7 A real run on Robert's machine reports ready, lists only chat-capable models and no embedder, and GET /api/version and `ollama list` are identical before and after.
- [ ] #8 The installed_not_running state is shown on a real machine only if Robert stops his own Ollama for it; MyScribe never stops or starts it. Otherwise the fake-directory test stands alone, and the notes say so.
- [ ] #9 .env.example no longer advertises OLLAMA_HOST (:24-26), which nothing reads.
<!-- AC:END -->
