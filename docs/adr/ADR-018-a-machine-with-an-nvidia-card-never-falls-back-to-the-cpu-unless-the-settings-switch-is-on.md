---
id: "ADR-018"
title: "A machine with an NVIDIA card never falls back to the CPU unless the Settings switch is on"
status: "Proposed"
date: "2026-09-23"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "gpu"
  - "accelerator"
aliases:
  - "cpu fallback"
  - "cpu_fallback"
  - "broken driver"
components:
  - "scribe.accel"
symbols:
  - "transcription_backend"
  - "diarization_device"
  - "cpu_fallback_allowed"
  - "GpuUnreachable"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-018 A machine with an NVIDIA card never falls back to the CPU unless the Settings switch is on

## Status

Proposed, 2026-09-23.

## Status History

```yaml
status_history:
  - date: 2026-09-23
    status: Proposed
    changed_by: Claude (agent, session 2026-09-23)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

`accel.transcription_backend()` answered `cuda` when torch could open a device
and `cpu` otherwise, and `diarization_device()` did the same. The "otherwise"
covers two very different machines: a laptop that never had a card, and a
machine whose NVIDIA driver broke this morning. The second transcribed about
thirty times slower and said so nowhere (TASK-092, measured before the change:
a job on a card CUDA could not reach ended `done` on `cpu`).

CPU transcription is a supported mode (README.md:46, :52-53), so the laptop
must stay quiet. TASK-089.12 gave the doctor the probe that tells the two
apart: `nvidia_hardware_present()`, from `nvidia-smi` being installed and the
PCI vendor id, never from `CUDA_VISIBLE_DEVICES`, with every doubt answering
True. It lived in `scribe/doctor.py`.

## Decision Drivers

* A broken driver must be impossible to miss (Robert, 2026-09-22).
* A machine with no NVIDIA card must not notice the change.
* Somebody with a broken driver and no time to fix it can still work.

## Considered Options

The task records that Robert was asked with three options and their
consequences; their wording is not in the repository. The list below is the
agent's reconstruction from the task text, for Robert to correct.

* Refuse the job behind an unreachable card, with a Settings switch, off by
  default, that allows the CPU.
* Refuse the job behind an unreachable card, with no way round it.
* Keep falling back to the CPU and say so on the job and the run.

## Decision Outcome

Chosen option: **refuse, with a switch that is off by default**, decided by
Robert on 2026-09-22 (TASK-092 description). The reason as the task puts it:
somebody on a train with a broken driver can still work, but only after
saying so.

### Confirmation

TASK-092's tests in `tests/test_cpu_fallback.py` (red first, 2026-09-23) and
the moved probe tests in `tests/test_accel.py`. Nobody has run it behind a
really broken driver yet: TASK-092 criterion 8 names the run.

## Decision Contract

### Must

* With CUDA unreachable, `transcription_backend()` and `diarization_device()`
  raise `GpuUnreachable` when the probe finds NVIDIA hardware, unless the
  switch is on or `CUDA_VISIBLE_DEVICES` is set.
* The switch is one `setting` row, `cpu_fallback`. Exactly `1` is on; a
  missing row or any other value is off, the shape ADR-016 gives the provider.
* The refusal is raised before any model is built. The job ends `failed` with
  `GPU_UNREACHABLE`, exit 1, and a sentence naming the driver, `nvidia-smi`,
  the switch and where it is in Settings.
* A run records the backend it used (`params_json.device`).
* The probe lives in `scribe/accel.py`; the stages do not import the doctor.

### Must Not

* Answer `cpu` behind present-but-unreachable hardware with the switch off.
* Refuse a machine where the probe finds no NVIDIA hardware, or a Mac.
* Read `CUDA_VISIBLE_DEVICES` as a broken driver: setting it is a choice.

### Exceptions

* macOS: it has no CUDA, and the probe answers True for an operating system
  it was not taught. Whether Metal gets the same rule is left open.
* An explicit device in the job's parameters is never second-guessed.

### Verification

* `tests/test_cpu_fallback.py` and `tests/test_accel.py`, one pytest process
  per file.

## Consequences

### Positive

* A dead card shows up on the first job as a failed job with a sentence, not
  as a slow transcript somebody notices a week later.
* The laptop without a card, and the Mac, behave exactly as before.

### Negative

* Every doubt in the probe counts as a card. A Linux box whose `/sys` cannot
  be read, or an operating system the probe does not know, gets refusals until
  somebody turns the switch on. The sentence says where.
* A job whose driver dies between the transcribe and diarize stages fails at
  diarize with the transcript written, as any diarize failure does today.

## Pros and Cons of the Options

### Refuse, with a switch (chosen)

* Good, because the broken case is loud and still has a way through.
* Bad, because a doubtful probe can refuse a machine that has no card.

### Refuse, with no way round

* Good, because nothing can hide a dead card.
* Bad, because a broken driver on the road stops all work.

### Fall back and say so

* Good, because no job ever fails for it.
* Bad, because a line on a finished job is easy to miss for a week.

## Open Questions

- [ ] Robert: do the three options above match the ones you were asked with on 2026-09-22?

## Related Decisions

* ADR-012 (the pinned stack): its doctor check stays the judge of the install;
  this record decides what a job does at run time.
* ADR-016 (no provider row): the missing-row-means-off shape is borrowed.

## References

* Backlog TASK-092, TASK-089.12, TASK-093.
* `scribe/accel.py`, `scribe/stages/transcribe.py`, `scribe/stages/diarize.py`.
