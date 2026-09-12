---
id: TASK-036
title: >-
  The look-ahead can raise faster-whisper's log-mel floor and re-decode a whole
  window
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 17:32'
updated_date: '2026-09-12 01:16'
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
- [x] #1 The replica shows, for the 7 windows, that the scaled look-ahead leaves the window's features bit-identical to its features without a look-ahead
- [x] #2 The TASK-030 set (the Hacker History windows that hallucinated at a cut) is decoded with the scaled look-ahead, and the number of end-of-window loops is reported next to the unscaled look-ahead and no look-ahead
- [x] #3 The decision (adopt, reject, or another fix) is recorded with those numbers; if adopted, a red/green test holds that a louder look-ahead does not change the window's features
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC1 measured 2026-09-12, CPU only, read-only (scratchpad t036_scaled.py, t036_table.out, t036_scaled.json; verified independently by a second agent with its own script, t036x_verify.py / t036x_verify.json).

The candidate: scale the look-ahead by a = 10^((Lw - Lc)/2 - 1e-5), where Lw is the window-alone log-mel max and Lc the combined max. Scaling audio by a scales the power by a^2, so a frame inside the look-ahead moves by 2*log10(a). The closed form converged on the first try in all 7 windows; the shrink-and-retry loop never ran.

Result over all 156 look-ahead windows of the 43 media:
- floor bit-equal to window-alone: unscaled false in 7, scaled false in 0.
- window prefix (every frame but the last 3000, size_melmax's definition) bit-identical: unscaled false in 7, scaled false in 0.
- strict check (every frame whose 400-sample STFT support lies in the shared kept window audio): scaled differs in 2 of the 7 raised windows - media 41 w1 and 54 w2, 3 frames each, all within 3 frames of the cut, max difference 1.19e-07 (1 ulp of float32). It is not the floor: appending 1 s of zeros to the window-alone input reproduces the same frames exactly, and the scaled features equal that zero-padded control in 156 of 156 windows; the same effect shows in 73 of the 149 windows the fix does not touch. It is the matrix multiply handling the last columns differently.
- VAD chunks inside the window identical, both definitions, 156 of 156; the look-ahead keeps the same seconds scaled and unscaled (30.00 s, or 25.90 s for media 53 w3); summed duration_after_vad differs by 0.000 s.
- The 149 windows where the look-ahead does not raise the max get their look-ahead back as the very same array (a = 1.000000), so their input is untouched.
- Replica validity: windows() equals the current T.iter_windows on 199 of 199 windows, and reproduces the stored duration_after_vad of both runs with max difference 0.000000 s. iter_windows/quietest_cut are unchanged since 4b2c771.

Cost: about 6 s of CPU per window for the unoptimised version (two VAD + extractor passes), 8.3-11.2 s on the raised windows.

AC2, measured on CPU before spending GPU time: none of the 7 TASK-030 windows (media 46 w0; 28, 29, 40, 43, 55 w1; 57 w2) has a look-ahead that raises the max - a = 1.000000 in all 7, so the scaled input there IS today's input, bit for bit. A three-variant decode of that set cannot show anything but sampling noise. The comparison that does carry information is the 7 raised windows, the only place the candidate changes what the decoder hears; that decode is running (t036_decode_ac2.py 3 --raised).

Residual settled 2026-09-12, recomputed by me (scratchpad t036x_tail.py, run again from this session): the model loads as large-v3-turbo on cuda/float16, and the encoder reads float16. Media 54 w2 frames 59776-59778 and media 41 w1 frames 59576-59578 differ from window-alone by 1 ulp of float32 (5.96e-08 to 1.19e-07) in 1-4 of 128 bins; after the cast to float16, 0 of 128 bins differ. So the residual cannot reach the model.

The two frames that really change are the ones straddling the cut (54 w2: 0.029 and 0.677; 41 w1: 0.015 and 0.372, 97-117 of 128 bins surviving float16). Those hold look-ahead samples by construction, so every look-ahead changes them, scaled or not; that is the price of hearing past the cut, not of this candidate.

AC1 therefore holds as measured: with the scaled look-ahead the window keeps its own floor and its own features, bit for bit, everywhere except the 2 frames (20 ms) that straddle the cut.

AC2 decoded 2026-09-12 on the GPU (scratchpad t036_decode_ac2.py 3 --raised, output t036_ac2_run.txt / t036_decode_ac2.json): the 7 raised windows, three variants (no look-ahead / today's look-ahead / scaled look-ahead), 3 repeats each, 63 decodes of large-v3-turbo on cuda/float16 with production's own decode options, windows and cut.

Per variant, 21 decodes: end-of-window loops (8+ identical words) 0 / 0 / 0; a "Thank you" invented in the last 12 s before the cut 4 / 0 / 0; hotword read-backs 4 / 3 / 3, none of them in the last 30 s for the look-ahead variants; words more than 60 s before the cut identical to the no-look-ahead decode 12/14 / 0/21 / 18/21.

Read that last column the right way: today's look-ahead never reproduces the window's own text far from the cut on these 7 windows, and the scaled one does in 18 of 21. All 3 misses are media 53, whose no-look-ahead decode is not even stable against itself (12 of 14 for the "none" repeats) because that window falls back to T0.6-T0.8; where sampling runs, bit-identical features stop guaranteeing identical text.

The TASK-030 comparison AC2 asks for cannot be run as written: the look-ahead raises the max in none of those 7 windows (a = 1.000000 each), so the scaled input there is today's input, bit for bit, and any difference would be sampling noise. The evidence that the scaled look-ahead still keeps a cut from sounding like the end of a file is in the 7 raised windows above: 0 end-of-window loops and 0 invented "Thank you" in 21 decodes, the same as today, while the window-alone decode invented one 4 times.

In words a person can check (t036_phrases.py, one decode per variant): media 10 window 0 says "Intellivision" 5x / 2x / 5x, "television" 0x / 3x / 0x, "Novell NetWare" 2x / 0x / 2x, "Novell network" 0x / 2x / 0x, "CypherCon" 6x / 5x / 6x, "SeekerCon" 0x / 1x / 0x (none / today / scaled). Media 22: "Maybe a proto-freaker." 1 / 0 / 1. Media 34: "shell script around find" 1 / 0 / 1, "shelf lifter" 0 / 1 / 0, "relearn" 1 / 0 / 1, "really learn" 0 / 1 / 0. So the scaled look-ahead gives back exactly the words the raised floor had cost.

Adopted 2026-09-12 and implemented: scribe/stages/loudness.py (scale_lookahead, log_mel_max) and one call in transcribe.transcribe_audio, which asks the model's own feature extractor - large-v3 has 80 mel bins where large-v3-turbo has 128 (ADR-004), and a maximum over the wrong filterbank is the wrong number. The window's maximum is taken over the audio faster-whisper's VAD will keep, because that is what it extracts features from; the look-ahead's is taken over all of it, which is an upper bound for whatever the VAD keeps there and saves a second VAD pass. On Apple Silicon there is no such extractor (mlx-whisper), so the look-ahead goes as it is.

Red then green: tests/test_stage_loudness.py failed on the import and now passes 11, including a test that the bug is real (a louder look-ahead without the fix changes every frame of the window) so the main assertion cannot pass by saying nothing, and two pipeline tests in tests/test_stage_transcribe_windows.py (the stage hands a quietened look-ahead to a model that has an extractor; a model without one gets it untouched). Mutation on a copy: 8 of 8 caught, after three that survived the first round were closed - the window measured over everything instead of over the speech, the power law dropped from the exponent (a look-ahead quieter than it needs to be is its own bug: TASK-030 exists because a cut that sounds like the end of a file hallucinates), and the margin removed.

Real run, media 10 (49 min), both ways in one process (scratchpad t036_realrun.py): unscaled reproduces the stored run exactly - 9987 words, "Intellivision" 2x, "television" 3x, "Novell network" 2x - and scaled gives 9969 words with "Intellivision" 5x, "television" 0x, "Novell NetWare" 2x, "Novell network" 0x. Cost on that recording: 199.9 s -> 217.6 s, +17.7 s or +8.9 % (xrt 14.84 -> 13.64), which is the VAD and extractor pass per window, about 3 s each.

Still to decide by Robert: the seven recordings (10, 22, 34, 41, 50, 53, 54) keep the damaged text until they are re-transcribed, and a re-transcription is a change to his library plus a speakers pass each.

The cheaper shape was measured and rejected (scratchpad t036_vadcost.py, all 156 look-ahead windows). Taking the window's maximum over everything instead of over the VAD-kept speech would save the VAD pass - median 2.13 s of the 2.84 s this costs per window (extractor 0.66 s on the window, 0.05 s on the look-ahead) - and the two maxima agree in only 88 of 156 windows. They differ both ways, up to 0.125 decades: the VAD-kept audio is a concatenation of speech chunks, so a frame at a join mixes audio from either side and can be louder than anything in the window itself. Where the raw maximum is the higher of the two, a look-ahead scaled to it stays above the floor the decoder will really use, which is the bug this task is about. On this library the cheap rule happens to reach the same verdict in 155 of 156 windows and misses none of the 7 raised ones, but "happens to" is not a reason to aim at the wrong number. The exact version stands, and the price is the +8.9 % measured on media 10.

Landed 2026-09-12 as 34af00c on feat/feed-episode-import. Two failures the suite caught before the commit, both fixed: scribe/stages/loudness.py imported faster_whisper at module level, which would pull a model runtime into the web process and import CTranslate2 before cuda_setup.ensure_cuda_libs (ADR-006) - the import now happens inside the call; and the docstring spelled the default model name, which the package is only allowed to do in transcribe.py. Suites after the fix: Windows 1235 + 851 passed; Linux (WSL) 1225 passed 10 skipped and 834 passed 17 skipped, the same totals. adr-judge: 0 violations, 0 advisory.

Decision recorded for AC3: adopt. The candidate keeps everything the look-ahead was for (0 end-of-window loops and 0 invented "Thank you" in 21 decodes of the windows it touches, the same as today) and gives back the window's own reading everywhere else, at +8.9 % on one measured recording. The alternative - leave it - keeps a recording's words hostage to audio that is thrown away, and nobody can tell from the transcript that it happened.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A window was being re-decoded because of audio it throws away: faster-whisper floors every log-mel bin at 8 decades under the loudest bin of the whole input, and since the look-ahead that input includes 30 s of the next window. Measured over 43 recordings the floor rose in 7 of 156 windows, and those windows lost real words ("Intellivision" -> "television", "Novell NetWare" -> "Novell network", a dropped sentence). scribe/stages/loudness.py now quietens a look-ahead that would raise the floor, using the model own extractor and the audio the VAD keeps, and hands back the same array in the other 149 windows. Verified three ways: the replica shows the window features bit-identical to a decode with no look-ahead at all (the two frames straddling the junction aside, and a 1-ulp residue that the encoder float16 erases); 63 GPU decodes show the end of the window as clean as today and the text far from the cut back to what the window says on its own, 18 of 21 against 0 of 21; and a real run of media 10 gives "Intellivision" 5x where today gives 2x. Cost: +8.9 % on that recording, one VAD and extractor pass per window; the cheaper variant was measured and rejected. Red/green, mutation 8 of 8, suites green on Windows and Linux, committed as 34af00c. The seven affected recordings keep their damaged text until Robert re-transcribes them.
<!-- SECTION:FINAL_SUMMARY:END -->
