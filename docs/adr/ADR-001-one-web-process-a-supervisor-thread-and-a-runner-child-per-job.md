---
id: "ADR-001"
title: "One web process, a supervisor thread, and a runner child per job"
status: "Accepted"
date: "2026-09-03"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-015"
  - "ADR-022"
topics:
  - "process-architecture"
  - "job-orchestration"
  - "gpu"
aliases:
  - "supervisor"
  - "runner child"
  - "process model"
components:
  - "scribe.app"
  - "scribe.supervisor"
  - "scribe.runner"
symbols:
  - "Supervisor"
  - "runner.main"
  - "reconcile"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-001 One web process, a supervisor thread, and a runner child per job

## Status

Accepted, 2026-09-03.

## Status History

```yaml
status_history:
  - date: 2026-09-02
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-03
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert in an interactive session on 2026-09-03. Claims verified against the code: no model imports in app/supervisor/web, the supervisor spawns the runner child, reconcile runs at startup, and four job types now run in children. The CPU-prework exception was removed in favour of describing what is actually built."
    changed_via: adr-kit lifecycle
  - date: 2026-09-20
    status: Accepted
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-015
    changed_via: adr-kit lifecycle
  - date: 2026-09-26
    status: Accepted
    changed_by: Claude (agent, session 2026-09-26)
    reason: Related to ADR-022
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

The app runs GPU (graphics processing unit) work - faster-whisper, pyannote - on one
RTX 3080 Laptop with 16 GB (gigabytes) of VRAM (video memory), for one user, on localhost. Two facts measured on this machine
decide the shape:

* faster-whisper does not reliably release VRAM in-process
  (SYSTRAN/faster-whisper#71); process exit is the only guaranteed free.
* Model load is cheap: large-v3 constructs in 17–19 s warm or cold (CTranslate2, the
  inference engine under faster-whisper, maps the weights from disk), large-v3-turbo in about 10 s. Paying that per
  job is affordable; a CUDA out-of-memory taking the web app down is not.

The question is how many processes own what: the queue, the HTTP (web) surface,
and the GPU.

## Decision Drivers

* A CUDA (NVIDIA's compute platform) out-of-memory error, or a driver hiccup,
  must kill only the job, never the app. CPU (processor) work is unaffected.
* One command starts everything; a single user should not manage a worker.
* Exactly one GPU stage in flight at a time on a 16 GB card.
* Development restarts happen constantly; the cost of losing a running job on
  restart must be bounded and visible.

## Considered Options

* One FastAPI process with a supervisor thread that spawns one runner child
  per job.
* WHYcast's three-process model: web process, separate worker process, runner
  child per job.
* One process holding the models warm across jobs.

## Decision Outcome

Chosen option: **one FastAPI process + supervisor thread + runner child per
job**, because it keeps the GPU isolation the measurements demand with the
fewest moving parts. The web process never imports a model; the supervisor
claims a job from SQLite and runs `python -m scribe.runner <job_id>`; the
child loads models, runs stages, writes its own verdict, and exits — VRAM is
freed by the operating system, not by a library we cannot trust to do it.

The accepted cost is that restarting the app kills a running job. Startup
reconciliation marks orphaned `running` rows `interrupted` with a one-click
retry, so the loss is visible and bounded to one job's elapsed time.

### Confirmation

`tests/test_supervisor.py` drives the supervisor against scripted runner
scripts: queued → running → done, a runner dying without a verdict becoming
`failed/RUNNER_DIED`, cancel killing a hung child within the grace period, and
`reconcile()` flipping a dead-pid running row to `interrupted`.
`tests/test_pipeline_e2e.py::test_the_whole_pipeline_on_the_gpu_with_the_default_model`
(marker `gpu`) proves large-v3-turbo and pyannote run in one job on the 16 GB
card, which is only possible if the transcribe stage's VRAM was actually freed
before diarization loaded.

## Decision Contract

### Must

* GPU work runs only inside `scribe.runner` children; the web process and the
  supervisor thread never import `faster_whisper`, `ctranslate2`, or `torch`.
* The supervisor spawns at most one GPU runner at a time.
* A runner writes its own terminal verdict; the supervisor only supplies the
  `RUNNER_DIED` safety net when the child exits without one.
* Startup runs `reconcile()` before accepting work.

### Must Not

* Keep a model resident in the web process between jobs.
* Add a second long-lived worker process without superseding this ADR.

### Exceptions

* None. Overlapping the next job's CPU prework with the running GPU job was
  considered and deliberately not wired (see Open Questions).

### Verification

* `tests/test_supervisor.py`, `tests/test_runner.py`, `tests/test_app.py::test_startup_reconciles_orphaned_running_jobs`.
* `grep -n "faster_whisper\|ctranslate2\|import torch" scribe/app.py scribe/supervisor.py scribe/web/` returns nothing.

## Consequences

### Positive

* One start command; a crashed job is a row with an error code, not a dead app.
* No lock files, no broker, no second service to monitor.

### Negative

* A running job dies with the app. Mitigated by reconciliation and retry, and
  by the fact that model load is ~10 s, so a retry loses only elapsed stage
  time.
* Every job pays the model load. Accepted: at 14.5x realtime, load time is a
  small fraction of any job longer than a couple of minutes.
* The GPU idles while a job's ffmpeg runs. Measured on a 40-minute interview:
  `prepare` took seconds against 500 s of GPU work, so the loss is around 1%.
  `jobs.claim_next(mode="cpu-prework")`, the `job.cpu_prework_done` column and
  `mark_prework_done()` exist and are tested, but nothing in production calls
  them - a prepared path, kept deliberately unused.

## Pros and Cons of the Options

### One FastAPI process + supervisor thread + runner child

* Good, because OOM (out-of-memory) isolation and video-memory release come
  from process exit.
* Good, because there is exactly one thing to start and stop.
* Bad, because the app's restart kills the current job.

### Three processes (WHYcast)

* Good, because restarting the web app leaves the worker and its job running.
* Bad, because it needs a pid-file lock, a second start command, and separate
  restart discipline — WHYcast has this because its web UI (user interface) was added on top of
  a CLI (command-line interface) tool; this app is designed whole.

### Warm model in one process

* Good, because it skips the per-job load.
* Bad, because faster-whisper#71 means VRAM leaks across jobs, and one CUDA
  fault takes the whole app down; on a 16 GB card shared with a local LLM (large
  language model) this
  is the fragile option.

## Open Questions

- [x] Should the CPU-prework overlap be part of this decision? — **Answered 2026-09-03 by User: Robert van den Breemen:** No. Robert chose to describe what is built (2026-09-03). The machinery is written and tested but unwired, and the ADR now says so under Consequences instead of granting an exception for behaviour that does not run. Measured upside is about 1% of a job's wall time, against a second claim mode in the one place - the atomic queue claim - where a race would be most expensive. Revisit if a batch of many short files ever makes the idle GPU visible; the column and the mode are already there, so it is an afternoon rather than a redesign.

## Related Decisions

* ADR-002 (SQLite's WAL (write-ahead log) mode is the only coordination between
  these processes).
* ADR-006 (the DLL (dynamic link library) directory registration the runner
  child performs before loading a model).

## References

* `docs/superpowers/specs/2026-09-01-myscribe-design.md` §1.
* `scribe/supervisor.py`, `scribe/runner.py`, `scribe/app.py` (lifespan).
* SYSTRAN/faster-whisper issue #71, video memory not released on model
  deletion: https://github.com/SYSTRAN/faster-whisper/issues/71
* `scribe/supervisor.py:162` (`_spawn`, the one place a runner child is
  started) and `scribe/app.py:195` (`reconcile` in the lifespan).

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "faster_whisper", "path_glob": "scribe/app.py", "message": "The web process never loads a model (ADR-001)."},
    {"pattern": "faster_whisper", "path_glob": "scribe/supervisor.py", "message": "The supervisor never loads a model (ADR-001)."},
    {"pattern": "faster_whisper", "path_glob": "scribe/web/**", "message": "Web routes never load a model (ADR-001)."},
    {"pattern": "ctranslate2", "path_glob": "scribe/app.py", "message": "The web process never loads a model (ADR-001)."},
    {"pattern": "ctranslate2", "path_glob": "scribe/web/**", "message": "Web routes never load a model (ADR-001)."},
    {"pattern": "pyannote", "path_glob": "scribe/app.py", "message": "The web process never loads a model (ADR-001)."},
    {"pattern": "pyannote", "path_glob": "scribe/web/**", "message": "Web routes never load a model (ADR-001)."}
  ],
  "forbid_pattern": [],
  "require_pattern": []
}
```
