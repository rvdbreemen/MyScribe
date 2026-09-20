---
id: TASK-089.16
title: >-
  Downloading the models fetches what this platform and tier actually load, and
  the loaders read that copy
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - transcribe
  - packaging
  - bug
dependencies:
  - TASK-089.09
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 153000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Requirement 4 cannot be met today. scribe/models.json pins two repositories: mlx-community/whisper-large-v3-turbo, the Apple MLX weights, and pyannote/speaker-diarization-community-1. catalogue() applies no platform filter (scribe/models.py:89-102). On CUDA and CPU the transcribe stage hands faster-whisper a model NAME, not a folder (scribe/stages/transcribe.py:583), and faster-whisper resolves it into the Hugging Face cache by itself.

What a Windows or Linux user hits: they tick 'Download the model weights now (about 1.6 GB)', wait for it, and the first transcription still stalls on a second download of the real weights, inside the job and with no progress shown. The doctor then reports '1.6 GB still to download' for ever on a machine that transcribes fine. A reader saw exactly that on Robert's machine on 2026-09-20: `python -m scribe.models` listed the MLX repo as MISSING while the hub cache held the faster-whisper turbo repo.

Tier 'max' is pinned nowhere, so choosing Maximum and ticking download fetches the turbo file, and the literal in the dialog says '1.6 GB' whatever was picked (packaging/launcher/myscribe_launcher.py:574).

On a Mac the copy that ensure() writes is not the copy that is loaded: the MLX backend passes a hub id as path_or_hf_repo (scribe/stages/mlx_backend.py:76, :92), and the only stage that reads MODELS_DIR is diarize (scribe/stages/diarize.py:255). That was established by reading; nobody ran it on a Mac.

Three smaller faults in the same file. ensure() checks `present(model, where=base, ...)` with `base = where or root()` (scribe/models.py:245-251), so it re-downloads what status() counts as present in the hub cache. It walks the catalogue in JSON order, so with no token the public 1.6 GB arrives before the gated 33 MB can refuse. And the Authorization header is sent whenever a token exists, gated repo or not (:206). A reader's local-server probe also showed a dropped connection reported as a pin mismatch - 'does not match its pin ... it was deleted rather than used' - which reads as tampering, not as 'your connection dropped'.

The critic re-checked that WhisperModel accepts a local directory (faster_whisper/transcribe.py:678). So local-first loading mirrors what pyannote already does. Whether mlx-whisper loads from a local folder is verified by nobody.

This is NOT on the engine's critical path (brief: M9). It follows TASK-089.09 and takes over the catalogue line that `--plan` reports until then.

Needs a real machine: Robert's RTX 3080 for the offline transcription on Windows, and his WSL for Linux. A real Apple Silicon Mac for the MLX half: Robert, if the Mac of TASK-040.07 (a session on 2026-09-19) is still his to use - not confirmed; otherwise reported as not run. Nobody has verified that mlx-whisper loads from a local folder.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Catalogue entries carry platforms or backends, and a tier. Pins (revision, per-file sha256, size) exist for the CT2 turbo repo under the id faster-whisper requests, for Systran/faster-whisper-large-v3 and for mlx-community/whisper-large-v3-mlx. The pins were not collected in the design run; the notes say where each came from and when. The diff of `python -m scribe.models` output on Windows is shown, with the reason it moved.
- [ ] #2 status(), present() and ensure() agree: a hub-cache snapshot at the pinned revision counts as present. On Robert's machine `--fetch` downloads nothing. Red first: today ensure() would download pyannote again.
- [ ] #3 ensure() fetches gated entries first. With no token, the first and only request is for the gated repo, and the probe output is shown.
- [ ] #4 Red first, with the truncating local-server probe: a short read raises reason 'offline', not 'mismatch'. A .part file resumes with Range, with up to 3 retries.
- [ ] #5 Free space is checked on the volume the files land on, before the first byte. When it is too small, one sentence gives both numbers and no download is attempted. ENOSPC surfaces as disk-full, not as 'offline'.
- [ ] #6 transcribe.load_model and the MLX backend load from the pinned local folder when it is complete, and behave as today otherwise. tests/test_stage_transcribe.py:43-54 (ADR-004) stays green.
- [ ] #7 Needs a real GPU: with a fresh HF_HOME and a scratch data directory on Windows, fetch turbo, then transcribe tests/fixtures/clip30.wav with HF_HUB_OFFLINE=1. The word count and the seconds are printed. The same run is done in WSL. Robert runs both, on his RTX 3080.
- [ ] #8 Needs a real Mac: the same offline run on Apple Silicon answers whether mlx-whisper loads from a local folder. Robert answers it if the Mac that TASK-040.07 records a session on (2026-09-19) is still his to use; that was not confirmed when these tasks were written, and no Mac was available in the design run. If it is not run, the box stays unticked and the parent's final summary lists it. Until it is run the MLX half is not called done.
- [ ] #9 401 and 403 produce different sentences in models.fetch_file and in doctor._gated_repo_reachable.
- [ ] #10 The Authorization header is sent only for gated repos and is dropped on a cross-host redirect. The two-server probe is red, then green.
- [ ] #11 Choosing tier max downloads large-v3 and nothing for turbo. This makes TASK-040.06 AC5 true.
- [ ] #12 `--plan`'s downloads now gives real bytes for this platform and tier, and the 'not pinned yet' line that TASK-089.09 carried is gone. The diff of its output is shown.
<!-- AC:END -->
