---
id: TASK-089.13
title: >-
  The install ends on a proof that never writes to the live library and never
  loads a model beside a running app
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 20:02'
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
- [x] #2 No line of `--prove` or `--check` writes to the real data directory (brief: W1), with one exception that criterion 3 owns: for an app that serves this library, the job rows it queues and what those jobs store. The serve check runs on a scratch SCRIBE_DATA_DIR, or reads 'app: /health already answers' when the app is up. Every other line that needs a writable folder or a database uses that scratch directory or none at all: the doctor's data-dir probe (scribe/doctor.py:187-191), its database check (:267-271, which migrates today) and the gpu-smoke's stage_perf write (:411-422, which migrates too). Lines that are about the real library - its path, its schema version, its settings rows - open it read-only and never migrate. One test runs a full `--prove` with the app down against a planted library one schema version behind, and asserts that no file in that data directory changes size or mtime, that none appears, and that user_version is unchanged. How the read-only open is done is the implementer's; the design spec records a measurement that a plain `mode=ro` open leaves a -shm and a -wal behind on Windows, which was not re-run here and which this test would catch. The `smoke` row is not written by `--prove`. That is a choice: the row is telemetry kept under its own stage name so that it cannot touch the ETAs (scribe/doctor.py:414-421), and a proof that leaves the library byte-identical is worth more than one sample of it. `python -m scribe.doctor` from a terminal keeps writing it, as today. The recorded run names the scratch directory it used.
- [x] #3 `--prove` loads no model while the app may be transcribing. When /health answers on the port it was given (default 4242), it queues the existing `doctor` job the way scribe/web/settings.py:154-160 does, or reports 'not tested (app running)'. `--prove` does not wait on the queue, because behind a long one the wait has no bound: the line reads 'transcription: not tested yet - queued as doctor job N; the result appears under Settings > This machine', where the runner stores it in the `doctor_last` setting (scribe/web/settings.py:10-13). It is never 'ok', and the exit code counts it as not tested. If a wait is wanted after all, it is bounded by a stated number of seconds and ends on that same line. The one-word probe of a ready Ollama follows the same rule. A queued row is only honest in the library the answering app serves, because queue_gpu_checks writes it into the database `--prove` has open (scribe/web/settings.py:154-163). What tells `--prove` is decided - by Robert on 2026-09-20 (brief: G5): the two fields `/health` gains in TASK-089.17: `app_dir`, the source tree the app runs from, and `data_dir`, the data directory it serves (design spec section 3.9 names the keys, and both tasks use those names). `--prove` compares `data_dir` with the data directory it has open, after os.path.normcase(os.path.realpath(...)) on both, the way TASK-089.17 criterion 9 compares paths. A match queues as above. A mismatch queues nothing, loads no model, and reads 'not tested (a MyScribe is running on port N)'. An answer without the fields is doubt and reads the same: that is an older MyScribe, and every MyScribe until TASK-089.17 has landed, because this task is built first. One test per case - match, mismatch, no fields. A test with a fake gpu-smoke that raises proves neither path calls it.
- [x] #4 It also loads nothing while any job row is `running` in this database, and says 'not tested (a job is running)'. That check needs no port. What it still cannot see - an app on another port serving the same library - is said in the report, not implied.
- [x] #5 A machine without NVIDIA hardware is not declared broken. That judgement is TASK-089.12's, which is why this task depends on it: with today's doctor the proof would inherit a required FAIL (scribe/doctor.py:341-347, and exit 1 at :826). Pass or fail comes from the required checks as TASK-089.12 leaves them, and accel says 'transcription on cpu'. It is shown the way TASK-089.12 criterion 1 shows it, with the hardware probe replaced; CUDA_VISIBLE_DEVICES=-1 alone on a machine with a card stays a FAIL, as ADR-012 wants. Nobody has run this on a real machine without NVIDIA hardware. The first that will is a GitHub runner in TASK-089.24, whose install ends on this report ('no runner has a card', .github/workflows/ci.yml:82); until that run exists the notes say so.
- [x] #6 A cloud provider is asked for its one word only on a yes, default No, because it costs money. Otherwise the line reads 'configured, not tested'.
- [ ] #7 The expected report for Robert's machine is written down before the run, so the run can be judged: no credential question, no Ollama question, one found line per credential, and `ollama list` and GET /api/version identical before and after. If the machine differs by then, the notes say how.
- [ ] #8 Two branches cannot be proven on Robert's machine, and every recorded run says so: the absent-Ollama install and the Tk pixels.
- [x] #9 With no app answering and no job running, the setup child runs the gpu-smoke in its own process, as `python -m scribe.doctor` does from a terminal. That is GPU work outside a runner child, and ADR-001's Must lists no exception for it (docs/adr/ADR-001-...md:116-118, :128-131); the doctor command is the precedent, but no ADR records it. ADR-015 records the reading (TASK-089.02 criterion 2): with no app and no running job, nothing else holds the card. If Robert rejects that reading, this line reads 'not tested' and gives the command `python -m scribe.doctor`.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Corrections first, every line re-read on 2026-09-22. setup.py: read_only 301, plan 711, verify_* 755-842, apply 863, main 1284, flags 1285-1310. doctor.py: data-dir probe 202-216, database 282-314, gpu-smoke stage_perf row 566, the no-NVIDIA branch 462-489, exit rule 1153. settings.py: pending_doctor_job 153, queue_gpu_checks 164-173. app.py: lifespan 239-262 (db.migrate 248, sweep_speaker_passes 261), /health 296-298. Launcher: run_setup 1383. install.py does not exist, and /health answers only ok+version, so today every app that answers lands in criterion 3's no-fields branch.
2. doctor gains the read-only mode the spec's 3.8 owes this task: --read-only on the CLI, read_only=False on checks(), and three substitutes that keep the same Check names. data-dir probes a scratch directory the caller gives, never paths.DATA_DIR; database opens through setup.read_only (no migrate, no journal pragma) and reports the schema version, with "no library yet" for an absent file and not a FAIL; gpu_smoke(record=False) skips the stage_perf write. The default run of python -m scribe.doctor keeps writing the smoke row, as criterion 2 says.
3. Check gains tested: bool = True and render() a third mark. "not tested" is a state the dataclass has no room for today - a required untested line renders FAIL (doctor.py:1081-1086), the one word criterion 1 forbids - and criteria 1, 3, 4, 6 and 9 all hang off it. last_run() reads the new field with a default so an old doctor_last row still parses. The exit rule already counts it right: not ok and not optional means 1, so a not-tested required line exits 1. No third exit code.
4. setup gains prove() and --prove, branching in main() beside --plan and above paths.ensure_dirs(): everything below that line may write, and a directory that merely appears already fails criterion 2. One read_only(paths.DB_PATH) connection answers every question about the library - schema version, settings rows, provider, the running job row. db.connect is opened in exactly one branch, the app-answers-and-data_dir-matches one, to enqueue; that is the single exception criterion 2 names.
5. The report renders doctor's own Check objects, in criterion 1's order: environment (python, uv version, uv.lock sha256), ffmpeg and ffprobe with the path which() found, the read-only CPU checks, accel, one line per found_table() row (name and source, never a value), Ollama, the AI provider, transcription, the app, the log path. The log path is named and never written. TASK-089.09 settled what "mirrors into the install log" means - setup writes no log of its own, the install log is the launcher's tee of this child's stdout (its plan, step 13) - and criterion 2 forbids the alternative, since paths.LOGS_DIR is under the data directory.
6. The transcription gate, three branches (criteria 3, 4, 9). A job row that is running in this library: load nothing, "not tested (a job is running)", no port needed. Otherwise GET /health on --port (default 4242) through setup._client, loopback proxy bypassed as ADR-015 requires. No answer and no running job: the smoke runs in this process, which ADR-015 records as inside ADR-001 (its Must binds the web process and the supervisor thread; the doctor CLI is the precedent). An answer whose data_dir equals this library after normcase(realpath()): queue_gpu_checks, no wait, the line names the job id and Settings > This machine. Another data_dir, or no fields: nothing queued, nothing loaded, "not tested (a MyScribe is running on port N)". One line of the report says what this cannot see: an app on another port serving this library.
7. Ollama's one-word probe follows the same gate - same card. The cloud provider's word is asked only on an explicit yes whose default is No: one y/N question at a terminal, never without one, otherwise "configured, not tested". Nothing is sent to a paid endpoint during the build, not once.
8. Criterion 5 is inherited, not re-decided: pass or fail is doctor's required set as TASK-089.12 left it, and accel says what it says. Two tests: nvidia_hardware_present patched False gives no required FAIL and the cpu line; CUDA_VISIBLE_DEVICES=-1 with hardware present stays FAIL.
9. Red first, against today's code and not a scaffold. Plant a library one schema version behind under a tmp SCRIBE_DATA_DIR, snapshot (name, size, st_mtime_ns) per file plus user_version, run doctor.checks(include_gpu=False): red today for at least three reasons (database migrates, the data-dir probe creates the folder and a probe file, and whatever the grep below adds). Keep that output; --read-only turns the same assertions green, and the test then runs a whole --prove. Before implementing, grep the prove path for mkdir, ensure_dirs, NamedTemporaryFile and open-for-write: models.status, diarize.local_weights_dir, credentials.login_file and env.load_dotenv are unverified.
10. Tests, one file per process behind the TASK-090 fence. New tests/test_setup_prove.py: the report's shape and order, exit 0 only when every required line is ok, one not-tested line exits 1 and never reads ok; the no-write test of step 9 over a full --prove; the three /health cases and the running-row case, each with a gpu_smoke double that raises, proving neither path calls it; the cloud default-No case with a transport that raises on any request. tests/test_doctor.py gains the --read-only cases and criterion 5's two.
11. The tests bite, shown on a copy under scratch/mut, one change per run: put mode=ro back in place of immutable=1 and watch the no-write test catch the -wal and -shm; delete the running-row guard and watch the doubles catch the load. grep -rn MUTANT over scribe, tests and packaging after each run.
12. Out of scope, named so nobody looks for it: no --check (install.py is TASK-089.17's and does not exist yet), no /health fields (TASK-089.17), no launcher wiring - no criterion asks for it, and _apply_setup would report a not-tested exit as "Saving your answers failed".
13. What no agent can close: criterion 7's run on Robert's machine (it reads his real credentials and his live Ollama), criterion 8's two branches (an install with no Ollama, the Tk pixels), and criterion 5's first real machine without a card, which is TASK-089.24's runner. Criterion 9's reading is Robert's to accept; ADR-015 records it, and on a rejection that line reads "not tested" and gives the command python -m scribe.doctor. The prediction criterion 7 asks for is in the notes, written before any run.

14. Which lines the exit code judges, decided now because step 3 makes it visible: transcription is required (criterion 3 says the exit code counts it as not tested, so a proof beside a running app exits 1 on purpose); the AI provider line is optional (criterion 6 makes not-testing its default, and ADR-016 treats no provider as a legitimate state); ollama stays optional as check_ollama already is; everything else keeps the required-ness TASK-089.12 left it with. With that table the criterion 7 prediction holds: the provider reads "configured, not tested" and the run still exits 0, because transcription is measured with the app down.
15. prove() takes its three probes as arguments the way apply() takes check= and announce=: the gpu smoke, the Ollama one-word probe and the /health GET, each defaulting to the real one. Without that seam four of the tests cannot run - the no-write test needs a smoke that succeeds without loading a model, and the /health and running-row tests need one that raises if it is called at all.
16. The read-only checks must stay zero-argument functions keyed in CHECK_LABELS: tests/test_doctor.py:890 asserts the labels are exactly CPU_CHECKS + GPU_CHECKS and :893 calls every one of them with no arguments, which rules out functools.partial and a scratch-directory parameter. Build the read-only list inside checks() from named zero-argument functions and extend both the tuple and CHECK_LABELS, moving :890 and :893 with it. In render(), tested is tested before optional, or the gated Ollama probe would print SKIP where criterion 3 asks for the literal "not tested". The read-only data-dir line keeps naming the directory it measured, so a green line about a temp folder can never read as a green line about the library. scribe/templates/_doctor_panel.html:23-26 branches on ok and optional only and needs no change: every check the panel shows has been run, by web_checks() or by the doctor job.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
### Criterion 7: the expected report, written 2026-09-22 before any run

A prediction, not a result. It is written now so the real run can be judged against it rather than described afterwards. Robert's machine, RTX 3080 16 GB, Windows 11, the app on 4242 stopped and no job running, SCRIBE_DATA_DIR pointed at a copy of the library:

- No question is asked: no credential question (HF_TOKEN and at least one LLM key resolve from the environment, .env or the registry) and no Ollama question (Ollama is installed and answering).
- environment: python 3.12, the uv version and the sha256 of uv.lock, all ok.
- ffmpeg and ffprobe: found, with the path which() reports.
- python, sqlite, disk-space: ok. data-dir: the scratch directory, writable. database: the copy's schema version, read-only, unchanged afterwards.
- accel: cuda. gpu-runtime: ok, torch with CUDA on the RTX 3080.
- credentials: one found line per credential, naming only the source. No value, no length, anywhere in the report.
- ollama: ready, with the chat models listed.
- AI provider: whatever the row says; a cloud one reads "configured, not tested" because the default answer is No.
- transcription: measured in this process, the default model on cuda, a 30 s clip, load and wall seconds and a word count over 10.
- app: not tested, nothing answers on 4242 - or "app: /health already answers" if Robert has it running, in which case transcription reads "not tested (a MyScribe is running on port N)" instead, because /health carries no data_dir until TASK-089.17.
- Exit 0.
- ollama list and GET /api/version are identical before and after (ADR-017: an Ollama that is there is left alone).
- The library the copy was made from is byte-identical: same file names, same sizes, same mtimes, same user_version.

If the machine has changed by the time the run happens, the recorded run says how it differed from this.

### Carried warnings

- python -m scribe.doctor with the card hidden segfaults about two runs in three on this machine: an intermittent access violation in the model constructor, measured 2026-09-22, recorded as TASK-093 and pre-existing. A --prove run that dies with exit 139 is very likely that, not this code. Say so with the run; do not chase it here.
- Criterion 5's claim about a real machine without NVIDIA hardware is untested by anybody. The first such run will be a GitHub runner in TASK-089.24.
- Criterion 8's two branches cannot be proven here: an install with no Ollama present, and the Tk pixels.

### Stale facts found while planning

- Every line number in the description has moved. setup.py: read_only 301, plan 711, apply 863, main 1284. doctor.py: data-dir 202, database 282, the stage_perf row 566, the no-NVIDIA branch 468, exit 1153. app.py: db.migrate 248, sweep_speaker_passes 261, /health 296. Launcher: run_setup 1383, --smoke 1792. settings.py:153-165 still holds.
- install.py does not exist yet (TASK-089.17), so criterion 2's mention of python install.py --check describes a door that is not built. --check is that door's flag and stays out of this task.
- /health returns only ok and version, so until TASK-089.17 lands every app that answers is the "no fields" case of criterion 3 - which the criterion already predicts.
- There is no install log file anywhere in the repository. TASK-089.09's plan (step 13) settled what the phrase means: setup writes no log of its own, and the install log is the launcher's tee of this child's output.

Prediction check (criterion 7): the Exit 0 above survives the required/optional table of plan step 14 only because the AI provider line is optional and transcription is measured with the app down. If Robert runs it with MyScribe up on 4242, transcription reads 'not tested (a MyScribe is running on port N)' - /health carries no data_dir until TASK-089.17 - and the run exits 1. That is the predicted behaviour, not a failure.

### Built 2026-09-22 (agent session): --prove, and the doctor's read-only mode

**What changed.** scribe/doctor.py: `Check` gains `tested: bool = True` with an invariant
that refuses `ok=True, tested=False`, so "not tested, never ok" cannot be written rather
than only tested for; `render()` gains a third mark `[----]` chosen before `optional`, and
its closing line counts untested required checks in a sentence of their own; `exit_code()`
is now one function both commands use; `gpu_smoke(record=False)`; four read-only twins
(`check_scratch_dir_writable`, `check_database_read_only`, `check_diarization_read_only`,
`check_gpu_smoke_read_only`) registered in CHECK_LABELS and swapped in by
`checks(read_only=True)` through `READ_ONLY_SUBSTITUTES`; `--read-only` on the CLI;
`last_run()` reads `tested` with a default so an old `doctor_last` row still parses.
scribe/setup.py: `prove()`, `Gate`, the `--prove` and `--port` flags, branching beside
`--plan` and above `paths.ensure_dirs()`.

**A writer the plan had not found.** `check_diarization` reads the settings row through
`credentials.library_db()`, which opens with `db.connect` - a `journal_mode` pragma, which
is a write on the live library. That is why there are four twins and not the three the plan
named. The suite could not see it: conftest stubs `credentials.library_db` autouse, so the
new test for it asks for `library_db_unstubbed`.

**Red first, load-bearing.** `tests/test_setup_prove.py::test_the_checks_a_proof_runs_leave_the_library_byte_identical`
plants a library one schema version behind (through `db.connect`, then closed, so it is in
WAL mode with no sidecars - which is the branch `setup.read_only` promises `immutable=1` on),
snapshots every path's size and mtime_ns plus the directory's own mtime and `user_version`,
and runs the checks. Against today's code it failed with "AssertionError: the library was
migrated / assert 17 == 16". The diff between red and green is one keyword: `read_only=True`.

**The tests bite** (a copy of the working tree under scratch/mut, one change per run,
repository verified clean of MUTANT afterwards):

- `mode=ro` in place of `immutable=1`: the no-write test fails with
  "Left contains 2 more items: myscribe.db-shm (32768 bytes), myscribe.db-wal (0 bytes)" -
  exactly the measurement the design spec recorded.
- the running-job guard deleted: three tests fail, two of them on their doubles -
  "a port was probed; the running-job check needs none" and "the local provider was asked
  beside a running job" - and the third because `transcription` renders OK and exits 0.

**Green, one test file per process, behind the TASK-090 fence** (SCRIBE_DATA_DIR and
SCRIBE_ENV_FILE exported in the same shell as pytest): test_setup_prove 22 passed;
test_doctor 63; test_setup 15; test_setup_plan 109; test_proxy 28; test_web_settings 54;
test_disk_floor 9; test_models 49; test_launcher 98 passed 1 skipped; test_dotenv_commands 9;
test_llm_selftest 14; test_stage_transcribe 69; test_stage_diarize 76; test_credentials 35;
test_env 43 passed 8 skipped; test_feed_backfill 13; test_feed_follow 13; test_ingest_urls 112;
test_runner 13; test_ollama_setup 19; test_cuda_setup 6.

**Two existing tests moved with the change, both understood, neither loosened.**
`test_json_prints_one_object_per_check_and_nothing_else` pinned the JSON key set exactly and
now includes `tested` - the shape grew on purpose, because `--json` is what a program reads
"nobody measured this" from, and the test now also asserts `tested is True` for every line
the doctor's own command prints. Three `doctor.checks` stubs gained the `read_only` keyword.
`test_every_registered_check_is_labelled_with_the_name_it_reports` now covers
`READ_ONLY_CHECKS` and asserts each twin reports the same name as the check it stands in for.

**Real runs on this machine (2026-09-22), not tests.**

1. `python -m scribe.setup --prove` with SCRIBE_DATA_DIR at a scratch library planted at
   schema v16, stdin from /dev/null so no question could be asked: exit 0. transcription read
   "large-v3-turbo on cuda/float16: 75 words from 30s in 11.6s (load 6.0s)"; gpu-runtime read
   "torch 2.10.0+cu128 CUDA 12.8 on NVIDIA GeForce RTX 3080 Laptop GPU (16.0 GB)"; database read
   "schema v16 ... read-only; the app migrates it to v17 the next time it starts"; app read
   "not tested: nothing answers on port 4242". A snapshot of the library taken before and
   after was identical: same files, same sizes, same mtimes, user_version still 16, after a run
   that loaded a model and transcribed a clip.
2. `python -m scribe.setup --prove --port 4299` beside a real `python -m scribe --port 4299
   --no-supervisor --no-browser` on a scratch library of its own: exit 1, with
   "app  /health already answers on port 4299 (version 0.5.1)",
   "transcription  not tested (a MyScribe is running on port 4299)" and the closing line
   "1 required check(s) not tested". That is criterion 3's no-fields branch measured rather
   than predicted - /health carries no data_dir until TASK-089.17 - and the exit 1 is the
   behaviour the prediction above says it is, not a failure. The app was stopped afterwards
   and port 4299 no longer answers. Robert's library at data/ was never opened: its newest
   write is still 2026-09-20. No paid request was made at any point during this build.

**Decisions this build had to make, and why.**

- *Which lines the exit code judges*: `transcription` required (criterion 3 wants a queued
  transcription to count as not tested), `ai-provider` optional (criterion 6 makes not-testing
  its default and ADR-016 treats no provider as legitimate), `app` optional, `blind-spot`
  optional, every credential line optional, everything else as TASK-089.12 left it. The `app`
  line being optional is what makes the criterion 7 prediction's "Exit 0" hold with nothing
  serving.
- *gpu-runtime is not gated*, and the code says so in a comment. `check_accelerators` is a CPU
  check and already imports torch to answer, and criteria 1 and 5 both require the accel line to
  run. Gating one and not the other would buy nothing and hide the reason. The line criterion 3
  actually draws is "loads no model", and that is the line drawn.
- *The queue branch writes two things, not one*: the job row, and a `job.enqueued` line in the
  library's own log, because `jobs.enqueue` calls `applog.log`. Named in `_queue_doctor_job`'s
  docstring as part of criterion 2's exception rather than suppressed: the app that is serving
  holds that file open already, and a queue that leaves no trace in the log is worse.

**What is NOT built, named so nobody looks for it.** Criterion 2's first alternative - a serve
check that starts the app on a scratch SCRIBE_DATA_DIR - is not implemented. The report takes
the criterion's second alternative when an app is up ("app: /health already answers") and,
when none is, reads "not tested: nothing answers on port N" with the exact command to finish
it, which criterion 1 sanctions (never "ok", with where to finish it). Criterion 2 is
therefore partly met, and building the spawn would have falsified the criterion 7 prediction
recorded above before any run - which is what that prediction exists for. No `--check` and no
`install.py` (TASK-089.17). No launcher wiring: no criterion asks for it, and `_apply_setup`
would report a not-tested exit as "Saving your answers failed".

**Still open for a person or another machine.** Criterion 7's run on Robert's own credentials
and live Ollama with `ollama list` and GET /api/version compared before and after - an agent may
not do that read-only here, and the run above used a scratch library instead. Criterion 8's two
branches (an install with no Ollama present, and the Tk pixels). Criterion 5's first real machine
without an NVIDIA card, which is TASK-089.24's GitHub runner - nobody has run it. Criterion 9's
reading is Robert's to accept; ADR-015 records it and the code cites it in `Gate`'s docstring.

**Carried warning, unchanged.** `python -m scribe.doctor` with the card hidden segfaults about
two runs in three on this machine (TASK-093, pre-existing). A `--prove` that dies with exit 139
is very likely that and not this code. It did not happen in either run above, both of which ran
with the card visible.

**Found outside this task's criteria, not fixed.**
`tests/test_feed_first_episode.py::test_a_poll_below_the_disk_floor_queues_nothing_and_says_so`
fails when SCRIBE_DATA_DIR is a long path: the disk-floor sentence is truncated in
`feed.last_result` before the "10" the test looks for. Confirmed pre-existing - the same test
fails identically against a clean export of HEAD with the same fence - and unrelated to this
change.

### Three corrections to the note above, same session

**A third test, and a third mutation.** Nothing pinned that the *registered* twin passes the
keyword: `test_the_smoke_without_record_writes_no_timing_row` calls `gpu_smoke(record=False)`
directly, so a twin written `return gpu_smoke()` would have passed the whole suite and written
a `stage_perf` row into somebody's library on every real `--prove`. The label test explicitly
skips that twin because calling it loads a model.
`tests/test_doctor.py::test_the_registered_twin_is_the_one_that_does_not_record` now asserts the
call rather than the behaviour: `doctor.gpu_smoke` is replaced by a stand-in that records its
keywords, and `check_gpu_smoke_read_only()` must arrive with `record=False` while
`check_gpu_smoke()` must arrive with none. Mutated on the copy (`return gpu_smoke()`), the test
fails with `assert [{}] == [{'record': False}]`; repository verified clean of MUTANT afterwards.
tests/test_doctor.py: 64 passed.

**Criterion 1 is partly met, not met.** "It mirrors the report into the install log" is satisfied
here by an argument and not by a run: TASK-089.09's plan step 13 settled that setup writes no log
of its own and the install log is the launcher's tee of this child's stdout, but nothing in this
task's diff or evidence shows that tee existing, and no launcher wiring was done (plan step 12
keeps it out on purpose). Everything else in criterion 1 - the report, its order, the exit rule,
and "not tested" never reading "ok" - is built and tested. Whoever checks the criterion should
decide whether the launcher clause needs a run of its own.

**A measured cost worth writing down.** On this machine a loopback GET to a port with nothing
listening answers `ConnectTimeout`, not `ConnectionRefused` - measured 2026-09-22 against 4242
and 4299. So every `--prove` on a machine with no app serving pays `HEALTH_TIMEOUT` (2.0 s)
before the gate opens. It is not a hang and the direction is the safe one: a firewall that
blackholes the port is indistinguishable from "nothing there", and the gate then opens and the
smoke runs locally, which is the same answer it would give if the app really were down. It is
the same class of fact as the blind-spot line the report prints, so it is recorded here rather
than rediscovered by whoever wonders about the pause.

### What the review changed (2026-09-22, second agent session)

Three verifiers reviewed the build. Twenty findings; what follows is what moved.

**One blocker was wrong in its evidence and right in its conclusion, and the truth is
worse than reported.** It said `data/` holds a `-wal` and no `-shm`, so a `--prove` on the
live library would create one. There is a `-shm`, 32,768 bytes, 15 Sep. Measured here
instead, three shapes, snapshot before and after (`walprobe.txt`):

- no `-wal`: `immutable=1`, nothing appears and nothing changes.
- `-wal` and `-shm`, the shape `data/` is in today: the `-shm`'s bytes and mtime *change*.
  The database and the `-wal` do not.
- `-wal` and no `-shm`: a `-shm` appears.

So criterion 2's literal wording - no file changes size or mtime, none appears - does not
hold for the library Robert has today. What holds is narrower and is now written down:
the library's own data is untouched, and SQLite's wal-index is the exception. Opening
`immutable=1` always was measured and rejected, because it lies: on a library whose schema
version and provider row were committed after the last checkpoint it reported
`user_version` 0 and the superseded row (`staleprobe.txt`). A report that says v0 about a
working library is worse than a wal-index that was rewritten. `read_only`'s docstring now
says all of this, and `test_a_library_with_a_wal_keeps_its_own_bytes_and_is_read_fresh`
pins both wal shapes. **Whether the narrowed claim satisfies criterion 2 is the
orchestrator's call, not an agent's.**

**Behaviour fixed, red first** (`final-red-prove.txt`: seven failures, one per change):

- A library from a *newer* MyScribe read `[OK  ] database  schema v18 ..., read-only; the
  app migrates it to v17 the next time it starts`. Nothing migrates downwards -
  `db.migrate` walks upwards and the range is empty - so a required FAIL had become a
  required PASS with a detail that cannot happen. Now a FAIL that says which way round it
  is.
- `check_gpu_runtime` ran ungated in the setup child. It is a member of `GPU_CHECKS`,
  which is this repository's own word for "not in this process", and it asks
  `get_device_name(0)` and `get_device_properties(0)` - torch's lazy init, a CUDA context
  on device 0. It now waits on the same gate as the smoke, and on the queue branch it says
  where its answer will appear. Optional only while untested, so the withholding is counted
  once, by `transcription`; that is why the closing line of the recorded run beside the app
  on 4299 still reads "1 required check(s) not tested", and a test pins it.
- The provider's one word was asked *before* the transcription smoke, on the same card,
  with ollama's `KEEP_ALIVE = "5m"` holding the model it answered with. Execution order and
  report order are now separate: the smoke measures first, the report still reads in
  criterion 1's order.
- A provider row naming something `PROVIDERS` does not have died with a `ValueError` from
  the one command whose job is to always print a report. It is a line now.
- The `log` line read "named here, not written" on the one branch where `jobs.enqueue`
  appends `job.enqueued` to that file. It says so now, and the test asserts the file rather
  than the wording.
- `_blind_spot` also names the doubt `_health` cannot see: an answer on the port that is
  not a MyScribe `/health` reads as nothing there, and opens the gate.

**Tests added where a mutation had walked straight through.** Each was replayed in a fresh
copy at `mut-fix/`, one change per run (`mutfix-replay.txt`; baselines 40, 64, 29):
`_confirm` itself - the production default of No, the EOF branch and the y/yes parse, where
both of the verifier's flips now bite and the empty line is covered; `setup._health`'s
`trust_env=False`, cloned from the pattern `tests/test_proxy.py` already uses for the
launcher; the no-write promise at `setup.main(["--prove"])` and not only at `prove()`; the
closing sentence and the skip roll-up; `_app_line`'s "no port was asked". Fourteen
mutations applied, fourteen red.

**Corrected in the record.** `READ_ONLY_SUBSTITUTES` said "the three checks that write"
with four in the dict; the section header now names the fourth, the settings-row read
behind `check_diarization`. The green list above says test_doctor 63; it was 64.
`tests/test_doctor.py::test_every_registered_check_is_labelled_with_the_name_it_reports`
stubbed `_diarization_token` while the loop calls the twin, which resolves through
`_diarization_token_read_only` - so every run of that file made an authenticated HEAD to
huggingface.co with this machine's real token, the invariant conftest exists to keep. Both
seams are stubbed now.

**Criterion 1's "it mirrors the report into the install log" has no implementation and no
referent.** There is no install log and no tee: `run_setup` hands each line to
`report("busy", line)`, a status callback that opens no file
(`packaging/launcher/myscribe_launcher.py:440-451`, `:1447`), and a grep for the phrase
across `packaging/`, `scribe/` and the design spec matched only the docstring that made the
claim. That docstring now says what is true. Whether the clause is deferred to the
launcher-wiring task or needs one of its own is the orchestrator's call.

**Struck as circular.** The earlier note argued the scratch-`SCRIBE_DATA_DIR` serve check
was not built partly because building it would have falsified the criterion 7 prediction. A
prediction is something to be tested against, not a constraint on the build. The plain
statement is this: `--prove` exits 0 with `[----] app  not tested`, so it can end an
install green on a machine where nothing has ever been shown to serve. That is the
deviation from criterion 2's first alternative, and accepting it or reopening it as its own
slice is the orchestrator's.

**Re-run after the review**, one test file per process, with `SCRIBE_DATA_DIR` and
`SCRIBE_ENV_FILE` printed into the head of each evidence file so the TASK-090 fence is
visible rather than asserted: test_setup_prove 40 passed; test_doctor 64; test_proxy 29;
test_setup 15; test_setup_plan 109; test_web_settings 54.

**Real run of the changed command** (`final-real-prove-run.txt`): `--prove` on a scratch
library planted at schema v16, stdin from /dev/null. Exit 0, with `transcription
large-v3-turbo on cuda/float16: 75 words from 30s in 12.0s (load 10.4s)` and `gpu-runtime`
measured ungated because nothing was serving. The library was byte-identical afterwards -
same files, same sizes, same mtimes, `user_version` still 16
(`fixrun-before.json` == `fixrun-after.json`). Robert's own `data/` was not opened:
`myscribe.db` is still 15 Sep 23:19 and the `-wal` still 20 Sep 19:57, the timestamps this
session started with. No paid request was made at any point, and TASK-093's segfault did
not appear - the card was not hidden in this run.

**Still not an agent's to close:** criterion 7's run on Robert's own library and live
Ollama, criterion 8's two branches, criterion 5's first machine without a card, and whether
the criterion 7 prediction preceded the run - the transcript settles that, the artifacts
cannot.

### Three corrections to the note above

**Where the evidence actually is.** The measurements are under
`.../scratchpad/build/TASK-089.13/`, and two of them are one level down:
`fix/walprobe.txt` (the three library shapes) and `fix/staleprobe.txt` (immutable reading
`user_version` 0 past a `-wal`). In the build root: `mutfix-replay.txt`,
`mutfix-baseline.txt`, `final-red-prove.txt`, `final-real-prove-run.txt`,
`fixrun-before.json`, `fixrun-after.json`, `fixrun-verdict.txt` and one `final-<file>.txt`
per test file.

**The `_confirm` mutations were applied one at a time, not as the verifier's pair.** Their
mut-verify-2 flipped the no-terminal default *and* the y/yes parse in one run; here m2a and
m2b are separate runs, which covers each flip on its own and is why the y/yes parse shows
up as three red parametrisations rather than one. The combined mutation was not replayed.

**One decision that is the orchestrator's, not an agent's:** `gpu-runtime` was a required
line and it stays required when it is tested - a card present and unreachable is still a
required FAIL, as criterion 5 wants. What changed is that the *gated* line is optional, so
a withheld driver question is counted once, by `transcription`, instead of twice. That is a
required-to-optional narrowing on one branch, argued from what the report already counts,
and it deserves to be looked at rather than taken on the prose above. The alternative is
one line in `_gpu_runtime_line` and a closing line that reads "2 required check(s) not
tested" in every gated run - including the one already recorded beside the app on 4299.

## Verification and the four calls the build left (orchestrator, 2026-09-22)

### Call 1 - the scratch serve check: built, not waved through

The build deliberately did not build it, so `--prove` exited 0 with
`[----] app not tested` and handed the person a command to run themselves.
That proves nothing about the install that just finished, and "the install
ends on a proof" is this task's title. Criterion 2 allows the check and says
how: on a scratch `SCRIBE_DATA_DIR`.

Built here, red first. `serve_check(data_dir, timeout=90)` starts
`python -m scribe --port <free> --no-supervisor --no-browser` with
`SCRIBE_DATA_DIR` on a `TemporaryDirectory`, polls `/health`, and stops the
child in a `finally` - killing it if it will not stop, because a proof that
leaves a server behind is worse than no proof. Port 0 asks the operating
system for one nobody is using, so it can disturb neither the library nor an
app on 4242. `SCRIBE_ENV_FILE` is pointed into the scratch folder too: an
`.env` naming another library would send the child there, which is the one
thing this check exists to avoid.

Red before: `TypeError: prove() got an unexpected keyword argument 'serve'`.
Green after. The real function has a test that lets it run:
`test_the_real_serve_check_starts_an_app_and_stops_it` - ok, `/health` in the
detail, and `myscribe.db` in the scratch folder and nowhere else, 8 s.

Two mutations on a copy outside the repository, one change each, both killing
`test_the_app_line_starts_one_where_it_can_do_no_harm`:

    the serve callable ignored           -> FAILED
    it serves from the real library      -> FAILED

`grep -rn MUTANT scribe tests packaging` is clean afterwards.

**One thing my change broke and I fixed rather than left:** fourteen existing
tests began starting a real app each, about five seconds apiece, because they
inject `health=` into `prove()` while `serve_check` uses the module-level
probe. The stub now lives in this file's shared `_quiet` helper with the
reason written next to the others, and the two tests that are about the serve
check pass their own. `tests/test_setup_prove.py` went 13.6 s -> 81 s -> 14.8 s.

### Call 2 - the WAL caveat: measured, and it does not apply here

The build qualified criterion 2 with "SQLite's wal-index is the exception" and
called it live rather than hypothetical. It is not live on this machine. The
library is in the shape where BOTH sidecars already exist:

    data/myscribe.db       106,844,160   mtime 2026-09-15 23:19
    data/myscribe.db-shm        32,768   mtime 2026-09-15 07:32
    data/myscribe.db-wal     4,120,032   mtime 2026-09-20 19:57

Rebuilt that shape on a scratch database and read a settings row through
`setup.read_only`:

    appeared : none
    myscribe.db      same size, same mtime
    myscribe.db-shm  same size, same mtime
    myscribe.db-wal  same size, same mtime
    read through setup.read_only: ollama   (from the WAL, not a stale checkpoint)

So criterion 2's wording - no file changes size or mtime and none appears -
holds on the shape that exists. The `-shm` creation the build worried about
needs a `-wal` with no `-shm` beside it, which TASK-089.09 already documents
as a half-copied or restored library. Criterion 2 is ticked on that
measurement plus the serve check above. Robert's library was not opened for
this; the shape was rebuilt.

### Call 3 - the install log: not this task's to mirror into

"It mirrors the report into the install log" has no implementation and no
referent: no install log exists. TASK-089.15 criterion 7 creates
`<home>/logs/launcher.log` and tees every reported line into it, which is
where this clause lands. Criterion 1 therefore stays **unticked**, and that
one clause is the only thing missing from it - the report itself, its order,
its "not tested" lines and its exit code are built and tested.

### Call 4 - gpu-runtime required-when-tested, optional-when-withheld

Accepted as the build decided it. A withheld measurement is counted once, by
`transcription`; the alternative prints "2 required check(s) not tested" in
every gated run, which reads as two problems where there is one condition.

### Criterion 9

Ticked. The reading is stated in `Gate`'s own docstring with its citation:
ADR-001's Must binds the web process and the supervisor thread and its
Exceptions read "None"; ADR-015 records that with nothing answering and no job
running, a one-shot command measuring in its own process is the existing
precedent, `python -m scribe.doctor` from a terminal. Robert accepted ADR-015
on 2026-09-21, so the reading is his. No ADR was weakened and no exception
invented.

### Whole suite

One file per process over 82 files (`tests/test_setup_prove.py` is new):
**3041 passed, 10 skipped, 0 failed, 0 errors**, reconciling exactly with
"3051/3061 tests collected (10 deselected)" since 3041 + 10 = 3051. That is
+52 on the run after TASK-089.14 (2999 collected): 43 in the new
`tests/test_setup_prove.py`, 8 in `tests/test_doctor.py` (56 -> 64) and 1 in
`tests/test_proxy.py`.

**A first run of this count was thrown away rather than reported.** Two suite
runs had written into the same output file, giving 98 file lines where there
are 82 and 3803 passed where collection expects 3051. Nothing in it said
"failed", so it looked like a green suite; only the reconciliation against
`--collect-only` caught it. The runner now writes `suite-$$.txt` and copies at
the end, and a repeat shows as a doubled `DONE`.

### Criteria 7 and 8 stay open

Criterion 7 needs the run on Robert's own machine - it reads his real
credentials and his live Ollama, and has to show `ollama list` and
GET /api/version identical before and after. The expected report is written
down in the build's notes above, before any run, which is the half an agent
can do. Criterion 8's two branches - an install on a machine with no Ollama,
and the Tk pixels - exist on neither this machine nor in this session.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
`python -m scribe.setup --prove` is the report an install ends on: the environment, ffmpeg and ffprobe with where each came from, the doctor's CPU checks, accel, the credentials by source, the Ollama state, the AI provider, transcription, the app and the log path - rendered over the doctor's own Check objects, with a third mark so a line nobody measured reads "not tested" and never "ok", and exit 0 only when every required line is ok. Two things it must not do shaped it. It does not write to the library it reports on: the doctor gains a read-only mode (no migrate, no probe file, no stage_perf row), every library question goes through setup.read_only, and the one exception is the job row queued for an app that is provably serving this library. And it does not load a model beside something that might be using the card: with /health answering on a matching data directory it queues the existing doctor job the way Settings does and says so with the job id, with any job row running it loads nothing, and only with neither does the smoke run in this process - the reading ADR-015 records and Robert accepted, with ADR-001 left as it is and no exception invented. A cloud provider is asked for its one word only on an explicit yes whose default is No; a transport that raises on any request proves nothing is sent otherwise. Built by the orchestrator on top of the round, because the build had deliberately left it out: the scratch serve check criterion 2 allows - it starts a MyScribe on an empty directory of its own and a port the operating system picked, asks /health, and stops it, so the last line of an install is measured rather than suggested. Red first, and two mutations on a copy kill its test. The WAL caveat the build attached to criterion 2 was measured rather than accepted: on the shape this library is actually in, both sidecars already present, a read through setup.read_only appears no file, changes no size and moves no mtime, and still reads out of the WAL. Whole suite over 82 files: 3041 passed, 10 skipped, 0 failed, reconciling exactly with 3051 collected - after a first count was thrown away because two runs had written into one file and looked green at 3803. Six of nine criteria are ticked. Criterion 1 is not: its report is built and tested, but "mirrors into the install log" has no referent until TASK-089.15 creates launcher.log. Criteria 7 and 8 need Robert's machine and a machine without Ollama.
<!-- SECTION:FINAL_SUMMARY:END -->
