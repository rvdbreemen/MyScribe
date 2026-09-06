---
id: "ADR-005"
title: "Diarization receives a preloaded waveform because torchcodec cannot open files here"
status: "Accepted"
date: "2026-09-06"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "diarization"
  - "dependencies"
  - "windows"
aliases:
  - "waveform dict"
  - "torchcodec WinError 127"
  - "pyannote audio loading"
components:
  - "scribe.stages.diarize"
symbols:
  - "diarize.diarize"
  - "load_pipeline"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-005 Diarization receives a preloaded waveform because torchcodec cannot open files here

## Status

Accepted, 2026-09-06.

## Status History

```yaml
status_history:
  - date: 2026-09-02
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-06
    status: Accepted
    changed_by: Robert van den Breemen
    reason: "Accepted by the user in session 2026-09-06 (explicit: 'Accept ADR-005')"
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

The design's rule is "never load an entire media file into RAM (working
memory); ffmpeg
streams, pyannote gets a path". On this machine that rule cannot be kept for
pyannote: torchcodec 0.16.0 (pulled in by torchaudio 2.8) links against
libav* symbols that FFmpeg 8.1 no longer exports, so `import torchcodec` dies
with `[WinError 127] The specified procedure could not be found`, and
pyannote's built-in decoding — which dispatches through torchaudio → torchcodec
— fails on every path. pyannote's own error message names the supported
alternative: a preloaded `{"waveform": tensor, "sample_rate": int}` dict.

The Phase 2 review flagged that the plan's Global Constraint had been amended
to sanction this without a decision record. This is that record.

## Decision Drivers

* Diarization must work on the machine we have, with the FFmpeg we have.
* The exception must be scoped, measured and reversible.
* Transcription — the stage that actually meets four-hour files — must keep
  its memory bounded, whatever the file's length.

## Considered Options

* Feed pyannote a preloaded waveform read from the prepared mono 16 kHz WAV.
* Downgrade system FFmpeg to a version torchcodec supports.
* Pin an older torch/torchaudio that does not route through torchcodec.
* Chunk the audio and diarize windows separately, then stitch.

## Decision Outcome

Chosen option: **preloaded waveform, diarize stage only**. The prepare stage
already produces mono 16 kHz PCM (uncompressed samples), so reading it costs
230 MB (megabytes) per hour of
audio as float32 — held for the duration of the diarize stage only and freed
when it returns, not at the end of the job. The cost is real and named; a
ten-hour file costs about 2.3 GB (gigabytes) for a few minutes, which this
machine has.

Transcription reads samples too, since 2026-09-03, but in bounded windows.
It used to hand faster-whisper a path, and faster-whisper answered by
computing the whole file's spectrogram in one array — 743 MB for a
40-minute interview, which is how this came to light. It now reads the same
prepared WAV in ten-minute windows: 37 MB of samples at a time, the same
number for a ten-minute file and a ten-hour one. So the shape of the rule
holds even though the word "streaming" no longer describes it — diarize is
the only stage whose memory grows with the recording, and that is exactly
what the Must Not below forbids everywhere else.

### Confirmation

`tests/test_stage_diarize.py` proves the path-based pyannote call is not used
and the waveform dict is; the GPU-marked acceptance test runs diarization end
to end on the card.

## Decision Contract

### Must

* `scribe.stages.diarize` passes `{"waveform", "sample_rate"}` to the pipeline.
* The waveform tensor is released (deleted, `gc.collect()`,
  `torch.cuda.empty_cache()`) before the stage returns.
* Only the diarize stage may load a whole audio file into memory.

### Must Not

* Read the whole file into RAM in any other stage.
* Add torchaudio/torchcodec file decoding anywhere; ffmpeg subprocesses remain
  the decoder.

### Exceptions

* None beyond the diarize stage itself.

### Verification

* `tests/test_stage_diarize.py` (waveform-dict path asserted, path call absent).
* `grep -rn "torchaudio.load\|torchcodec" scribe/` returns nothing.

## Consequences

### Positive

* Diarization works today, with the pinned stack, on FFmpeg 8.1.
* The exception is one function in one module.

### Negative

* ~230 MB RAM per hour of audio during diarization. Accepted; revisit when
  torchcodec and FFmpeg agree again, at which point this ADR is superseded by
  "pyannote gets a path".

## Pros and Cons of the Options

### Preloaded waveform

* Good, because it is the documented, supported pyannote input.
* Bad, because RAM scales with file length for the stage's duration.

### Downgrade FFmpeg

* Bad, because the user's system FFmpeg 8.1 serves other tools; the app
  should not dictate system packages.

### Older torch/torchaudio

* Bad, because it forfeits the cu128 wheels and the cuDNN 9 libraries that
  CTranslate2 4.8 needs (ADR-006).

### Chunk and stitch

* Bad, because stitching speaker clusters across windows is a research
  problem, not a workaround.

## Open Questions

- [x] Does the windowed transcribe reader belong under this decision's exception? — **Answered 2026-09-03 by User: Robert van den Breemen:** No, it needs no exception, and saying it did would weaken the rule. The exception exists for memory that grows with the recording: diarize holds the whole file (149 MB measured for 41 minutes, linear). Transcribe holds one ten-minute window - 37 MB, the same for a ten-minute file and a ten-hour one - so it satisfies 'never read the whole file into RAM' as written. What was stale is the sentence claiming transcribe still streams; it stopped streaming on 2026-09-03 when handing faster-whisper a path was found to build a 743 MB spectrogram of a 40-minute interview. The Must and Must Not are unchanged; only the explanation was wrong.

## Related Decisions

* ADR-006 (why the torch pin cannot move to dodge this).

## References

* `scribe/stages/diarize.py` module docstring; `requirements-gpu.txt` header.
* `docs/superpowers/plans/2026-09-02-phase2-pipeline.md` Global Constraints.

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "torchcodec", "path_glob": "scribe/**", "message": "torchcodec is broken against FFmpeg 8.1 here; ffmpeg subprocesses decode (ADR-005)."}
  ],
  "forbid_pattern": [
    {"pattern": "torchaudio\\.load\\(", "path_glob": "scribe/**", "message": "ffmpeg subprocesses decode; only diarize may hold a whole waveform, read from the prepared wav (ADR-005)."}
  ],
  "require_pattern": []
}
```
