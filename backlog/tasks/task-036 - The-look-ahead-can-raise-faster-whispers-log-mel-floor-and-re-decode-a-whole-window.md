---
id: TASK-036
title: >-
  The look-ahead can raise faster-whisper's log-mel floor and re-decode a whole
  window
status: To Do
assignee: []
created_date: '2026-09-11 17:32'
labels:
  - transcribe
  - investigation
dependencies: []
ordinal: 77000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-11 by the verification of the 43 re-transcriptions (jobs 206-248). A side effect of the look-ahead (TASK-030), measured, not yet decided.

Mechanism (package source, faster-whisper 1.2.1): features are extracted once over the whole input the decoder is handed (faster_whisper/transcribe.py:916), and every log-mel bin is clamped at log_spec.max() - 8.0 (feature_extractor.py:227). Since TASK-030 that input is the window plus 30 s of the next one. When the look-ahead holds a louder bin than the window, the floor rises for the whole window and its features change from frame 0.

Scope (in-memory replica with the real audio and faster-whisper's own VAD and extractor; it reproduces the stored duration_after_vad of both runs of all 43 exactly - scratchpad size_melmax.py, size_windows.py): the global max rose in 7 of 156 look-ahead windows - media 10 w0 1.4402->1.4786, 22 w0 1.3378->1.3652, 34 w3 1.5514->1.7095, 41 w1 1.7057->1.8725, 50 w0 1.8704->2.0480, 53 w3 1.5592->1.7124, 54 w2 1.7832->1.7950. All 7 changed away from the cut: 140 stretches, 237 tokens. The other 149 have bit-identical features and no change away from a cut except at temperature-fallback segments.

Damage both ways, judged from context, nobody listened: media 10 w0 "Intellivision" -> "television" x3, "Novell NetWare" -> "Novell network" x5, "CypherCon" -> "SeekerCon"; media 34 w3 "shell script around find" -> "shelf lifter on find", "relearn" -> "really learn"; media 22 w0 dropped "Maybe a proto-freaker." (398.1 s). Gain: media 53 w3 recovered 25 words where the old run had a failed T1.0 segment. The previous runs are still in the database.

Candidate fix, untested: scale the look-ahead so its loudest mel bin never exceeds the window's, which would keep the window's own features bit-identical. It changes what the decoder hears past the cut, so it must be measured against what the look-ahead is for: the end-of-window hallucinations of TASK-030.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The replica shows, for the 7 windows, that the scaled look-ahead leaves the window's features bit-identical to its features without a look-ahead
- [ ] #2 The TASK-030 set (the Hacker History windows that hallucinated at a cut) is decoded with the scaled look-ahead, and the number of end-of-window loops is reported next to the unscaled look-ahead and no look-ahead
- [ ] #3 The decision (adopt, reject, or another fix) is recorded with those numbers; if adopted, a red/green test holds that a louder look-ahead does not change the window's features
<!-- AC:END -->
