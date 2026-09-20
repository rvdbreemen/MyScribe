---
id: TASK-089.13
title: >-
  The install ends on a proof that never writes to the live library and never
  loads a model beside a running app
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-20 21:48'
labels:
  - packaging
  - tests
  - ops
dependencies:
  - TASK-089.12
  - TASK-089.09
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 150000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An install that ends on 'done' proves nothing. Today no code path runs the doctor after setup, and the launcher discards run_setup's result (packaging/launcher/myscribe_launcher.py:659-663). From a clone there is no 'it serves' check at all: the doctor never starts the app, and `--smoke` lives in the frozen launcher (:739-753). `python -m scribe.setup --prove` ends every sitting on measurements, and `python install.py --check` is the same report on demand.

The plan described that proof as one that 'touches nothing' and 'never disturbs port 4242'. Both overstate (brief: W1). Its serve check starts `python -m scribe --no-supervisor` on the real data directory, and the lifespan always runs db.migrate, supervisor.reconcile, recording.sweep and finalize.sweep_speaker_passes (scribe/app.py:247-261). That last one queues an LLM job for every diarized recording that was never asked. With `--no-supervisor` those jobs sit in the shared database until the live app runs them. The serve check is not the only line that writes. The doctor's data-dir check creates the folder and a probe file in it (scribe/doctor.py:187-191), its database check connects and migrates (:267-271), and the gpu-smoke connects, migrates and writes a `smoke` row to stage_perf (:411-422). So with the app down, today's doctor inside `--prove` would migrate the real database before the serve check is even reached. After a `git pull`, a check would migrate the live library under an older app that is still running. Robert's own rule is that proof runs on a copy of the library and never on the live one.

The plan also claimed ADR-001 was respected, and it was not (brief: W5). ADR-001 says GPU work runs only inside `scribe.runner` children, and at most one at a time (docs/adr/ADR-001-...md:114-118). Settings therefore queues its GPU checks as a `doctor` job (scribe/web/settings.py:8-14, :154). The plan wired `--prove` to a Setup button that exists only while the app runs. That loads the gpu-smoke in the setup child, and asks a ready Ollama for a word, outside the claim in scribe/jobs.py:142-150 and possibly beside a running transcription. The repository's one measurement of that collision is scribe/llm/ollama.py:221-224: the 9B and the 12B both failed to start while 12.7 GB of the 16 GB card was in use.

The report does read the real library: the credentials by source and the AI provider are settings rows. It reads them without writing, which is why the title says 'never writes to' and not 'never opens'.

`python -m scribe.doctor` from a terminal has the same property and stays as it is. What is new is a button that is only reachable while the app runs.

Needs a real machine: Robert's machine (RTX 3080) for the transcription line. Nobody has run this on a real machine without NVIDIA hardware; the first will be a GitHub runner in TASK-089.24.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `python -m scribe.setup --prove` prints one report: the environment, ffmpeg and ffprobe with where each came from, the doctor's CPU checks, accel, the credentials by source, the Ollama state, the AI provider, transcription, the app, and the log path. It mirrors the report into the install log. It exits 0 only when every required line is ok. Anything skipped reads 'not tested' with where to finish it, never 'ok'. A test pins that.
- [ ] #2 No line of `--prove` or `--check` writes to the real data directory (brief: W1), with one exception that criterion 3 owns: for an app that serves this library, the job rows it queues and what those jobs store. The serve check runs on a scratch SCRIBE_DATA_DIR, or reads 'app: /health already answers' when the app is up. Every other line that needs a writable folder or a database uses that scratch directory or none at all: the doctor's data-dir probe (scribe/doctor.py:187-191), its database check (:267-271, which migrates today) and the gpu-smoke's stage_perf write (:411-422, which migrates too). Lines that are about the real library - its path, its schema version, its settings rows - open it read-only and never migrate. One test runs a full `--prove` with the app down against a planted library one schema version behind, and asserts that no file in that data directory changes size or mtime, that none appears, and that user_version is unchanged. How the read-only open is done is the implementer's; the design spec records a measurement that a plain `mode=ro` open leaves a -shm and a -wal behind on Windows, which was not re-run here and which this test would catch. The `smoke` row is not written by `--prove`. That is a choice: the row is telemetry kept under its own stage name so that it cannot touch the ETAs (scribe/doctor.py:414-421), and a proof that leaves the library byte-identical is worth more than one sample of it. `python -m scribe.doctor` from a terminal keeps writing it, as today. The recorded run names the scratch directory it used.
- [ ] #3 `--prove` loads no model while the app may be transcribing. When /health answers on the port it was given (default 4242), it queues the existing `doctor` job the way scribe/web/settings.py:154-160 does, or reports 'not tested (app running)'. `--prove` does not wait on the queue, because behind a long one the wait has no bound: the line reads 'transcription: not tested yet - queued as doctor job N; the result appears under Settings > This machine', where the runner stores it in the `doctor_last` setting (scribe/web/settings.py:10-13). It is never 'ok', and the exit code counts it as not tested. If a wait is wanted after all, it is bounded by a stated number of seconds and ends on that same line. The one-word probe of a ready Ollama follows the same rule. A queued row is only honest in the library the answering app serves, because queue_gpu_checks writes it into the database `--prove` has open (scribe/web/settings.py:154-163). What tells `--prove` is decided - by Robert on 2026-09-20 (brief: G5): the two fields `/health` gains in TASK-089.17: `app_dir`, the source tree the app runs from, and `data_dir`, the data directory it serves (design spec section 3.9 names the keys, and both tasks use those names). `--prove` compares `data_dir` with the data directory it has open, after os.path.normcase(os.path.realpath(...)) on both, the way TASK-089.17 criterion 9 compares paths. A match queues as above. A mismatch queues nothing, loads no model, and reads 'not tested (a MyScribe is running on port N)'. An answer without the fields is doubt and reads the same: that is an older MyScribe, and every MyScribe until TASK-089.17 has landed, because this task is built first. One test per case - match, mismatch, no fields. A test with a fake gpu-smoke that raises proves neither path calls it.
- [ ] #4 It also loads nothing while any job row is `running` in this database, and says 'not tested (a job is running)'. That check needs no port. What it still cannot see - an app on another port serving the same library - is said in the report, not implied.
- [ ] #5 A machine without NVIDIA hardware is not declared broken. That judgement is TASK-089.12's, which is why this task depends on it: with today's doctor the proof would inherit a required FAIL (scribe/doctor.py:341-347, and exit 1 at :826). Pass or fail comes from the required checks as TASK-089.12 leaves them, and accel says 'transcription on cpu'. It is shown the way TASK-089.12 criterion 1 shows it, with the hardware probe replaced; CUDA_VISIBLE_DEVICES=-1 alone on a machine with a card stays a FAIL, as ADR-012 wants. Nobody has run this on a real machine without NVIDIA hardware. The first that will is a GitHub runner in TASK-089.24, whose install ends on this report ('no runner has a card', .github/workflows/ci.yml:82); until that run exists the notes say so.
- [ ] #6 A cloud provider is asked for its one word only on a yes, default No, because it costs money. Otherwise the line reads 'configured, not tested'.
- [ ] #7 The expected report for Robert's machine is written down before the run, so the run can be judged: no credential question, no Ollama question, one found line per credential, and `ollama list` and GET /api/version identical before and after. If the machine differs by then, the notes say how.
- [ ] #8 Two branches cannot be proven on Robert's machine, and every recorded run says so: the absent-Ollama install and the Tk pixels.
- [ ] #9 With no app answering and no job running, the setup child runs the gpu-smoke in its own process, as `python -m scribe.doctor` does from a terminal. That is GPU work outside a runner child, and ADR-001's Must lists no exception for it (docs/adr/ADR-001-...md:116-118, :128-131); the doctor command is the precedent, but no ADR records it. ADR-015 records the reading (TASK-089.02 criterion 2): with no app and no running job, nothing else holds the card. If Robert rejects that reading, this line reads 'not tested' and gives the command `python -m scribe.doctor`.
<!-- AC:END -->
