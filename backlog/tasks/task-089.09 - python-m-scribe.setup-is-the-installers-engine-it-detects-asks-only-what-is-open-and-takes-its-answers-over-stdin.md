---
id: TASK-089.09
title: >-
  python -m scribe.setup is the installer's engine: it detects, asks only what
  is open, and takes its answers over stdin
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 07:54'
labels:
  - packaging
  - llm
  - security
dependencies:
  - TASK-089.02
  - TASK-089.04
  - TASK-089.06
  - TASK-089.08
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 146000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
This closes the confirmed API-key gap and gives requirement 6 its asker. Today `python -m scribe.setup --provider openrouter` exits 0 with 'saved: provider', no credential and no warning: Answers has no key field (scribe/setup.py:46-53), there is no key flag (:160-167), and the first Summary fails as a job on the board. The one secret it does take rides on argv (`--hf-token`, :161; the launcher passes it at packaging/launcher/myscribe_launcher.py:475-476), where it sits in the process list for the length of a 1.6 GB download. Run bare, it prints 'saved: nothing' and stamps setup as done (:142-154), after which the launcher never asks again.

The questions live only in frozen Tk code (myscribe_launcher.py:515-594). A terminal installer has nothing to reuse, and a fifth question can be forgotten in one of two places. apply() also writes the token before it checks the provider (:114-121), so a bad `--provider` leaves a half-applied sitting.

The engine makes the question list data. `--plan` prints what was found and where - never a value - the Ollama state, and only the open questions, each with its default, what skipping costs and where to answer it later. `--apply-stdin` takes the answers as one JSON document. Run bare on a terminal, it asks the same questions itself. The proof that ends an install is TASK-089.13.

Ordering (brief: M9). This task does NOT wait for the models re-pin in TASK-089.16. That one needs pins nobody has collected and a Mac that was not available in the design run; the key question, the found table and the Ollama state need none of it. Until TASK-089.16 lands, `--plan` reports today's catalogue and says what it is.

It does follow TASK-089.08. The sentence under the token question says that a job which still asks for speakers keeps its transcript, and that is only true once TASK-089.08 has landed. No sitting writes default_diarize (brief: W6): setup never switches the speakers default off or on by itself. The typed `--diarize/--no-diarize` flag stays, as an explicit choice and nothing else - decided by Robert on 2026-09-20 (brief: G3) - and it is the one writer of that row setup keeps (criterion 17). Today even a tier-only answer writes the row, because save_defaults writes all three rows at once (scribe/web/transcribe_dialog.py:97-108); that is fixed here (criterion 9).

`--hf-token` goes in this task - decided by Robert on 2026-09-20 (brief: G4). The flag is still recognised and is refused with a sentence saying where a token belongs, exit 2 (criterion 5). 'A secret never rides on a command line' then has no exception.

OpenRouter checks a key for free. The agent established that during the grill of 2026-09-20, as a fact and not a decision, from OpenRouter's documentation and a probe that sent no key: an authenticated request to https://openrouter.ai/api/v1/key costs nothing and answers 401 for a bad key (ADR-015, Open Questions). Verified by nobody: that request with a real key, valid or revoked. Its first run here is the proof still owed.

Needs a real machine: Robert's machine for the found table, and a real terminal (PowerShell or cmd) for the console asker. The OpenRouter key check has never been run with a real key.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `--plan` prints JSON with the contract number (`contract`, the name the design spec and ADR-015 use), found[] (name, found, source, also_in, conflict, and the proxy entry from TASK-089.05), ollama, questions[] and downloads. Each question carries id, kind, text, choices, current, default, shown_if, if_skipped and answer_later. No secret value appears anywhere in it. A main()-level test pins the shape; today no test calls setup.main at all. The wall time on Robert's machine is measured and reported.
- [x] #2 `--plan` writes nothing. On an empty scratch SCRIBE_DATA_DIR the directory is still empty afterwards, and on a copy of a library no file's size or mtime moves. Today main() creates the directories and migrates the database before it answers `--status` (scribe/setup.py:171-174).
- [ ] #3 A question is present only when it is open. A real run on Robert's machine, output shown, has no credential question and no Ollama question.
- [x] #4 Until TASK-089.16 lands, `downloads` reports today's catalogue from scribe/models.json and labels it. On a platform whose transcriber is not MLX (accel.transcription_backend()), the mlx-community entry reads 'not loaded on this platform' and is left out of the offer and of the total. One line says this platform's Whisper weights are not pinned yet and still arrive inside the first job. No number is shown as a download size that is not one.
- [x] #5 `--apply-stdin` reads one JSON document. A missing or null answer means skipped and writes nothing for that question. No secret flag exists, and `--hf-token` goes now: decided by Robert on 2026-09-20 (brief: G4), so that 'a secret never rides on a command line' has no exception. Red first: today the flag is accepted and the token is saved (scribe/setup.py:114-117 and :161 at d80360a; TASK-089.03 shortens the file, so these lines are re-pointed after it lands). After the change the flag is still recognised and is refused - with a sentence, not an argparse error - saying that a token on a command line can be read by other processes and ends up in shell history, and that the token belongs in `HF_TOKEN`, in the environment or in `.env`, where setup now finds it by itself, or in the document piped to `--apply-stdin`; exit 2. Nothing is written, and the value is never echoed: a SENTINEL test reads stdout and stderr. README.md:224 is corrected, and the diff is shown. There is no deprecation window: launcher and app ship together, the flag has existed since 2026-09-19 (f2eba18; ADR-015's signed answer says 2026-09-18 and stays as signed), and nobody is known to rely on it. No code in this task passes a secret on argv. The launcher's existing use (packaging/launcher/myscribe_launcher.py:475-476) goes in TASK-089.15 criterion 3, and the assertion that pins it (tests/test_launcher.py:404) moves with the launcher there. install.py never has one (TASK-089.17).
- [ ] #6 A typed Hugging Face token is checked with one announced HEAD before it is saved: 401 reads 'token not recognised', 403 reads 'conditions not accepted' with the URL. A refused token is not saved, and its id comes back in `reopen`. A typed OpenAI key is checked with the authenticated model list, and a rejected key is not written. For OpenRouter the free key check established during the grill of 2026-09-20 is used: an authenticated request to https://openrouter.ai/api/v1/key, which answers 401 for a key that is missing, invalid or disabled (ADR-015, Open Questions). It was read from OpenRouter's documentation and probed without a key, never run with a real one, so its first run with a real key is the proof still owed and the notes show it. If it does not behave as documented, the paid one-word probe runs on consent only, default No.
- [x] #7 Typed secrets are written to the settings rows only (hf_token, llm_key_<provider>): the rows Settings writes and can clear. `.env` is not rewritten for a secret. A SENTINEL test covers stdout, setup.json and the install log.
- [x] #8 Red first (today: exit 0, 'saved: provider', no warning): choosing openrouter or openai with no key found and the key question skipped saves the provider, states in words that AI actions will show a no-key card until a key exists, and says where to add one.
- [x] #9 A sitting never writes default_diarize: not as a guard, not for `{}` answers, and not as a side effect of saving the tier. A test reads the row before and after a tier-only sitting on a fresh database, and finds it absent both times.
- [ ] #10 Run bare on a TTY, it is the console asker over the same questions: Enter accepts the shown default, `s` skips, secrets go through getpass. On a non-TTY it never calls getpass, prints the found table and what is open, exits 0 and writes no stamp. Red first: today it prints 'saved: nothing' and writes one. The TTY run needs a real terminal - PowerShell or cmd, not Git Bash - and the notes say which was used.
- [x] #11 A stamp is written when a sitting ends, including after a failed or skipped download, and it lists the answered and the skipped ids. Its format, its gate and its migration are TASK-089.11's. Exit codes 3, 2 and 1 are preserved and tested.
- [x] #12 `--provider` rejects an unknown value before anything is written. Red first: today a token given together with a bad provider is already saved when the ValueError arrives (scribe/setup.py:114-121).
- [x] #13 Progress is JSON lines when stdout is a pipe, at most one line per whole percent. On a TTY it is a bar with speed and ETA.
- [x] #14 An all-skipped run in a scratch directory exits 0, writes no settings row at all, leaves `.env` unchanged, and the stamp lists every id as skipped.
- [x] #15 The 'Four answers, and no more than four' docstring (scribe/setup.py:9), CHANGELOG.md:107-110 and the tests that state that number are revised knowingly, and the diff is shown. Two more recorded items are reversed by criterion 7 and are named with them: TASK-040.06's ticked criterion #2, 'writes it to the per-user .env', and write_token's docstring, which gives the reason - the first run happens before there is a browser to type into, and `.env` is the file the per-user home already carries (scribe/setup.py:85-93). Today apply() writes both `.env` and the row (:115-116). The reason for the reversal: a settings row is what Settings can show and clear, and a typed secret is not copied to a second place. TASK-040.06 is not edited; this task's notes say what changed and why.
- [x] #16 The key question itself (requirement 2), red first: today no key question exists at all. With provider openrouter chosen and no key in any source the resolver reads, `--plan` contains the question llm_key_openrouter with kind secret and a shown_if on the provider answer; the same for openai. With a key planted in any one source - the settings row, the process environment, `.env`, the registry - the question is absent and the found table names the source. A table-driven test covers both providers and every source. Ollama has no key question.
- [x] #17 `--diarize/--no-diarize` stays, as an explicit choice and nothing else: decided by Robert on 2026-09-20 (brief: G3). Somebody who types `--no-diarize` is choosing, not guarding; the flag has shipped in v0.5.0 and v0.5.1 (scribe/setup.py:164-165 at d80360a; TASK-089.03 shortens the file), and an unattended install on a machine that will never separate speakers is the case that wants it. What W6 removed stays removed: setup never switches the speakers default off or on by itself. The flag is the one writer of default_diarize that setup keeps, and the row is written through no other path: no `--plan` question asks it, a diarize answer in an `--apply-stdin` document writes nothing, and no door sends it - the Tk form never sets it (packaging/launcher/myscribe_launcher.py:579-584), and the launcher's pass-through (:481-482, pinned by tests/test_launcher.py:407) goes in TASK-089.15. Still to fix here, red first: a tier answer alone writes the row today, because the tier and the diarize default are saved together (scribe/setup.py:125-136 at d80360a); after the change `--tier` alone leaves the row as it was (criterion 9). One test shows that `--no-diarize` and `--diarize` each write the row. One runs every other setup path - a tier-only answer, `{}` answers, a stdin document carrying a diarize key, an all-skipped run - and finds the row untouched. README.md:224 keeps documenting the flag. The design spec (its section 3.1) and ADR-015 say the same; a grep shown in the notes finds no sentence in either that removes the flag.
- [x] #18 The model question for an Ollama that was already there (the design spec's question 9). It is present only when the provider is Ollama, Ollama was already there and is running, no llm_model_ollama row exists, the default qwen3.5:4b is not pulled and another chat-capable model is (TASK-089.06). It lists chat-capable models only, and its default is skip - TASK-054: never save a model nobody chose. An answer writes MyScribe's own llm_model_ollama row, the row Settings writes (scribe/web/settings.py:909-911), and changes nothing in Ollama: a MockTransport test asserts that the sitting sends Ollama no request besides the reads TASK-089.06 makes. Skipped, nothing is written. One test for the question being present, and one each for the four conditions that keep it out of `--plan`.
- [x] #19 The provider question (the design spec's question 5). It is present only when no llm_provider row exists; a re-run shows the stored value and never resets it. Its default is Ollama when TASK-089.06 reports it ready, and otherwise 'decide later', which writes no row - and no row means no provider (TASK-089.07). A cloud provider is never the default that Enter accepts, and each cloud choice carries the sentence that transcript text leaves this machine and that recordings pinned private are always refused. One test per default, over MockTransport states, and one that a skipped or 'decide later' answer leaves the row absent.
- [x] #20 No question is asked about a proxy. TASK-089.05 detects one and reports it; the found table shows it, and `--plan` carries no proxy question and no proxy answer. A test asserts that over the whole question list, so that this stays true as questions are added.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Order is the orchestrator's: engine core first, because every dependent task hangs on the contract.

1. Contract core (#1, #2, #16, #19, #20). Add CONTRACT = 2 as a constant in scribe/setup.py - where the payload keeps that number is TASK-089.11's to settle, so no setup_contract.json is created here. Add plan(conn, *, unasked_only=False) -> dict returning contract, found[] (credentials.find_all rows plus the credentials.proxies rows from TASK-089.05), ollama (ollama_setup.state), downloads and questions[] with id, kind, text, choices, current, default, shown_if, if_skipped, answer_later. Questions become one data table, a predicate per question. Build the provider question (#19, default ollama only when state is ready, else decide-later which writes no row, cloud never the Enter default), the key question (#16, kind secret, shown_if on the provider answer, absent when credentials.resolve finds a key in any source), and no proxy question (#20, asserted over the whole list).

2. Nothing is written by a plan (#2). Move env.load_dotenv/ensure_dirs/db.migrate behind the branch, so --plan AND --status both answer without creating anything. Read-only open with mode=ro&immutable=1 when no -wal file stands beside the database, plain mode=ro when one does, never db.connect - a plain mode=ro on a cleanly closed WAL database leaves a -shm and a -wal behind (spec 0's measurement), which is exactly what criterion 2 forbids. A missing database is no rows: every question open, nothing created.

3. Answers in, secret out (#5, #7, #15). Add --apply-stdin reading one JSON document (contract, answers); missing or null is a skip that writes nothing. Typed secrets go to the settings rows only (hf_token, llm_key_<provider>); apply stops writing .env, so setup.write_token goes with it - env.write_env owns the behaviour since TASK-089.03 and tests/test_env.py:137 and :150 already cover the BOM and the spaced line. Keep --hf-token RECOGNISED and refuse it with a sentence naming HF_TOKEN, the environment, .env and the stdin document, exit 2, never an argparse error and never echoing the value. Revise the four-answers docstring (setup.py:9), CHANGELOG.md:107-110 and README.md:224, and show each diff. TASK-040.06 is not edited; the notes say what was reversed and why.

4. The bugs, red first (#12, #9, #17, #8, #14, #10-non-TTY). Validate the provider BEFORE anything is written (today the token is saved at :106-107 before the check at :111-112). Write the tier row alone through a narrow writer beside SETTING_TIER in transcribe_dialog, so save_defaults' three-row executemany stops making a tier answer write default_diarize; --diarize/--no-diarize stays as the one writer setup keeps. Tests read the row with SQL, not read_defaults, which substitutes a default for a missing row. A cloud provider saved with no key says in words that AI actions show a no-key card and where to add one. Non-TTY: print the found table and what is open, exit 0, write no stamp, never call getpass.

5. Credential checks (#6). One announced HEAD for a typed Hugging Face token, 401 reads token not recognised and 403 reads conditions not accepted with the URL - setup gets its own check, the doctor's shared sentence at doctor.py:541-559 is TASK-089.12's and is left alone. OpenAI through the provider class's authenticated model list. OpenRouter through GET https://openrouter.ai/api/v1/key. A refused credential is not saved and its id comes back in reopen. No trust_env=False on these remote calls - that bypass is for the loopback only.

6. The rest (#4, #11, #13, #18). downloads reports today's catalogue from models.json and labels it; the mlx-community entry reads not loaded on this platform off an MLX backend (accel.transcription_backend) and is out of the offer and the total, with one line that this platform's Whisper weights are not pinned yet. A stamp is written when a sitting ends, failed or skipped download included, listing answered and skipped ids; its format, gate and migration stay TASK-089.11's; exit codes 3, 2 and 1 keep their meaning. Progress is JSON lines on a pipe, at most one per whole percent, a bar on a TTY. The Ollama model question for an already-present Ollama, over MockTransport, asserting the sitting sends Ollama no request beyond the reads of TASK-089.06.

7. Proof. Every pytest process under the TASK-090 fence, one test file per process, output to a file. Red first for #5, #8, #10, #12, #16, #17 and #2's directory check: the failing output is kept. One table-driven SENTINEL test covers #1 and #7 - every credential in every source the resolver reads, the marker absent from stdout, stderr, setup.json and the plan JSON. The mutation proof runs on a copy outside the repository, and a grep for MUTANT in the repository is empty afterwards.

8. Not closable by an agent, built anyway and written up with the exact command and expected output. #3 the real found table on Robert's machine (his credentials, his Ollama - read-only or not at all). #6's OpenRouter half: implemented as documented and probed without a key, never run with a real one, and the paid one-word probe is NOT run. #10's TTY half: needs a real PowerShell or cmd window. #1's wall time is measured here under the fence on a scratch data directory, which is the same CPU and disk.

If room runs out, stop after step 4 and report steps 5 and 6 as untouched - criteria 4, 6, 11, 13 and 18 - rather than half-doing twenty.

Corrections made before any code (each one checked in the tree today):

9. Step 2 amended: env.load_dotenv() STAYS in front of the branch. It writes nothing (it fills os.environ through setdefault) and the spec's 3.1 requires it - .env takes effect before DATA_DIR is fixed, so a SCRIBE_DATA_DIR in .env decides which library a plan reads. It also feeds env.applied(), which is how credentials tells a variable that arrived from .env apart from a true process variable; without it criterion 16's source attribution is wrong. Only paths.ensure_dirs() and db.migrate() move behind the branch.

10. Step 3 amended: the four-answers sentence in the CHANGELOG is NOT at 107-110. grep finds it at 212-214 (First run asks four things ...), with two more recent mentions at 61 and 64. The diff shown is of what grep finds, and the notes say the task's pointer was stale.

11. Criterion 12 is validated at two layers, or it is only half closed. Argparse choices on --provider catches the flag; an --apply-stdin document can still carry a bad provider that argparse never sees, so apply() keeps its ValueError and it moves ahead of every write. A test per entry point.

12. The shape of found[] is decided here because TASK-089.15 and TASK-089.17 both render it: every entry carries a kind discriminator (credential or proxy) beside the keys criterion 1 names, rather than coercing credentials.Proxy rows into the credential key set. The notes record it as the contract.

13. Criterion 7's SENTINEL surfaces: setup writes no log of its own, and the install log is the launcher's tee of this child's output, so stdout plus stderr is what that log can hold. Covered here: stdout, stderr, setup.json and the plan JSON. The launcher's own handling of the stdin document is TASK-089.15's.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation 2026-09-22 (agent)

The engine is built: `scribe/setup.py` 208 -> 1182 lines, `plan()` + `--plan`,
`--apply-stdin`, the console asker, the three credential checks, the narrow
defaults writers, and the four recorded bugs fixed. 17 of 20 criteria hold
evidence here; 3 need a person (below). Nothing is committed and no criterion
is checked - that is the orchestrator's.

### Red first (kept, all under the TASK-090 fence)

`red-1-bugfixes.txt`: 5 failed, 10 passed against the tree as it stood -
`--hf-token SENTINEL` exit 0 not 2; the bad-provider run with the row already
written (`hf_token` = the sentinel); a tier-only answer leaving
`default_diarize` = "1"; a bare run writing the stamp; `--provider openrouter`
printing only "saved: provider". Each failure is the defect its criterion names.

Criterion 2 has no in-tree red, because `--plan` did not exist: it is proven by
two mutants on a COPY at `mut/` (one change per run; the repository is verified
clean afterwards - `grep -rn MUTANT scribe tests packaging` finds nothing):

* `red-2-mutant-plan-writes.txt` - `ensure_dirs`/`migrate` put back ahead of the
  branch: the empty-directory test fails with a `myscribe.db` that was created.
* `red-3-mutant-immutable.txt` - `mode=ro` without `immutable=1`: the copied
  library grows a `-shm` of 32,768 bytes and a `-wal` of 0, which is spec
  section 0's measurement to the byte. Re-measured here on 2026-09-22 before
  the code was written: `immutable=1` creates nothing, and a missing file
  raises and creates nothing.

### Green (one test file per process, output in the build directory)

test_setup 14, test_setup_plan 74 (new file), test_web_transcribe_dialog 54,
test_credentials 35, test_env 43 + 8 skipped, test_dotenv_commands 9,
test_launcher 49 + 1 skipped, test_proxy 28, test_ollama_setup 19, test_doctor 23,
test_web_settings 54. No existing test was deleted or loosened.

### The contract, settled here because six tasks render it

`CONTRACT = 2`; no `scribe/setup_contract.json` (spec section 2 leaves the
file-versus-constant question to TASK-089.11). Keys: `contract`, `found`,
`ollama`, `downloads`, `questions`.

* `found` carries a kind discriminator, `credential` or `proxy`; credential rows
  add `label` and `note`, proxy rows add `host`. A proxy row is not coerced into
  the credential key set.
* `shown_if` is filled only while the question it depends on is in the same
  plan - a field conditioned on a question nobody is shown could never appear -
  and is null otherwise. So: no provider row gives a key question per cloud
  provider, each with `shown_if` on the provider answer; a stored provider gives
  one key question with `shown_if` null. Both cases are tested.
* `libraries` is deliberately absent (spec 3.1 lists it, criterion 1 does not,
  and adopting a library is TASK-089.19). The result carries `wrote`, `reopen`
  and `notes`; no `relocate` (TASK-089.14 / .19).
* `plan()` makes no remote call at all: settings rows, environment, `.env`,
  registry, login file, catalogue, and TASK-089.06's two Ollama reads. The
  credential checks fire only on a typed answer.

### Deliberate deviations, each with its reason

1. `--status` keeps its prologue; only `--plan` is read-only. Criterion 2's
   subject is `--plan`, stated twice; the `--status` sentence locates the old
   code rather than asking for a change. Making `--status` read-only would break
   TASK-089.03's `tests/test_dotenv_commands.py:77`, which asserts it creates the
   library, for no criterion. The recorded plan said "both", so this is the one
   place the plan was not followed.
2. A plan no longer imports torch. `accel.transcription_backend()` asks CUDA
   first and `cuda_available()` imports torch: measured at 4.9 s of a 9.5 s
   `--plan`, against 51 ms for all the rest of the work. `scribe/accel.py`'s own
   docstring keeps torch out of a process that only wants an answer (ADR-001).
   The new `setup.transcriber()` asks the real probe only where MLX is possible
   at all (`accel.mlx_available()`, Apple Silicon only, where there is no CUDA)
   and answers `not-mlx` elsewhere. Criterion 4's parenthesis names
   `accel.transcription_backend()`; it is still called, on the one platform
   where the answer can be `mlx`. Result: `--plan` 9.5 s -> 3.9 s.
3. CHANGELOG.md:212-214 is inside the released `[0.4.0]` section and is a true
   record of what that release did, so it stays; the revision is three new
   Unreleased entries plus five under Fixed. (The task's pointer to
   CHANGELOG.md:107-110 is stale - that holds the HF_TOKEN prefix fix.)

### A bug found by running, not by reading

`python -m scribe.setup < /dev/null` walked into the console asker and died with
`EOFError`, exit 1, having printed a question nobody could answer. The cause: on
Windows the NUL device answers `isatty()` with True (measured; `GetConsoleMode`
on it is False), and NUL is exactly what `subprocess.DEVNULL` gives a child -
which is what the launcher hands this one. `setup.at_a_terminal()` now asks
`GetConsoleMode` on Windows; a stream with no file descriptor is taken at its
word (a test's), and anything that cannot be asked reads as "no terminal",
because that path asks nothing and writes no stamp. Pinned by two tests; the
real child now prints the found table and exits 0 with no stamp
(`real-child-bare-run.txt`).

### Criterion 15: what was reversed, and what was not

The `scribe/setup.py:9` docstring, README.md:224 and the CHANGELOG are revised,
and the diffs are in the branch. `write_token` is gone (`env.write_env` has owned
the behaviour since TASK-089.03). Four tests moved, not the two the criterion
names: `tests/test_setup.py` :31, :43, :67 and :78 all pinned the `.env` write,
and :67 and :78 called `write_token` directly. :61 and :72 are retired rather
than moved, because `tests/test_env.py:137` and `:150` already pin the BOM and
the spaced line against `env.write_env`; :31 is rewritten as "the row, and no
second place". TASK-040.06 is not edited; its ticked criterion #2 ("writes it to
the per-user .env") and `write_token`'s docstring reason are the two recorded
items reversed, and the reason is in the new test's docstring and in the
CHANGELOG. The lookup order was verified in code before the write was deleted:
`credentials._sources` reads the settings row first and `resolve` returns it, so
a runner child needs nothing from the environment.

### Criterion 17: the grep

`criterion-17-grep.txt`. ADR-015:164 ("--diarize/--no-diarize stays"), :274 ("the
one writer setup keeps") and spec:702 ("It stays, as an explicit choice and
nothing else"). No sentence in either removes the flag.

### What an agent cannot close - the exact steps

* #3, a real run on Robert's machine. Read-only by construction: `--plan` never
  calls `db.connect` and opens the library `mode=ro&immutable=1`, proven by the
  mutant above. Run with no SCRIBE_DATA_DIR and no SCRIBE_ENV_FILE, so his real
  `.env` and his real library answer:
  `cd <repo> && .venv/Scripts/python -m scribe.setup --plan`
  Expected: no `hf_token` question (HF_TOKEN in the environment, with the
  "`.env` defines a different value" note on that row), no `llm_key_*` question
  (both keys found), no Ollama question (ready, qwen3.5:4b pulled), no
  `llm_provider` question (his row says openrouter). Only `default_tier` should
  remain, plus `fetch_models` if weights are missing. A scratch-directory run of
  the same command is in `plan-run-1.json` and `real-child-bare-run.txt`, and it
  already shows no token question, no OpenRouter key question and no Ollama
  question.
* #6, the OpenRouter half. Implemented as documented (GET
  https://openrouter.ai/api/v1/key, 401 for a key that is missing, invalid or
  disabled) and tested over MockTransport. It has never been run with a real key
  by anybody - that first run is the proof still owed. The paid one-word probe
  was deliberately not run.
* #10, the TTY half. Needs a real PowerShell or cmd window, not Git Bash, where
  MSYS's pty makes `isatty()` False and the asker never starts. In PowerShell:
  `cd <repo>; $env:SCRIBE_DATA_DIR="<a scratch dir>";
  .venv\Scripts\python -m scribe.setup`. Expected: each open question with its
  choices, then `[default] (s to skip):`; Enter takes the shown default, `s`
  skips, a secret is read with `getpass` and never echoed, and the sitting ends
  on "saved: ...". Everything but the real `getpass` is tested in
  `tests/test_setup_plan.py`: Enter, `s`, the secret going through `getpass`, and
  an empty answer being a skip.
* #1's wall time, measured here under the fence on a scratch SCRIBE_DATA_DIR -
  same CPU and disk: `python -m scribe.setup --plan` = 3815 / 3864 / 4128 ms
  (3.9 s), of which the plan's own work is 51 ms and the rest is interpreter
  start and imports. Before deviation 2 it was 9548 / 10477 / 9346 ms.

### For the orchestrator, before committing

* ADR-015's second Enforcement tripwire will flag `scribe/setup.py`. The pattern
  is a quoted `--*token*` literal, and the `--hf-token` argparse line is in the
  diff because its help text changed. ADR-015's Enforcement paragraph expects
  exactly this ("flagged when touched, like line 476"). The refusal sentence
  itself is worded so the flag name is never a bare quoted literal.
* Accepted intermediate breakage: the frozen launcher still passes `--hf-token`
  at `packaging/launcher/myscribe_launcher.py:645`, so a Tk first run where
  somebody types a token now gets exit 2 until TASK-089.15 removes the
  pass-through. No test goes red - `tests/test_launcher.py:449` only inspects
  the command string.
* `scribe/setup.py` is now 1182 lines. It is one engine by design (ADR-015), but
  whether the question table should move to a module of its own is a call for a
  follow-up task, not something done silently here.

### Addendum, same session

* **On `--status`, more precisely than above.** Both of criterion 2's testable
  claims are about `--plan` - "on an empty scratch SCRIBE_DATA_DIR" and "on a
  copy of a library" - and both are proven. That `--status` still creates the
  library it is asked about is pre-existing behaviour that TASK-089.03's
  `tests/test_dotenv_commands.py:77` pins on purpose and that no criterion here
  asks to change. It is a scope boundary, not a deviation; the recorded plan's
  "both" is the only thing it disagrees with.
* **Criterion 11 was half-built and is now whole.** A failed download let
  `models.ModelError` escape `apply()` before the stamp was written, so a
  sitting that ended in a failed download was not recorded - which the criterion
  names explicitly. `apply()` now stamps and re-raises, so the caller still gets
  its exit code. New test: a failed download still ends the sitting, with
  `answered` = default_tier, fetch_models and `fetched` = [].
* **The credential check is now tested through the door it is used from.** Every
  other refusal test replaces `setup.verify`, which left `main` ->
  `apply(check=verify)` -> `CHECKS[name]` -> the checker asserted by nothing: a
  name that did not match a key in `CHECKS` would have saved an unchecked
  credential in silence. One test drives that whole path with a MockTransport
  answering 403, and asserts exactly one HEAD, no row written, and the value in
  no output. Audited at the same time: no test anywhere reaches the real
  network - every path with a typed secret either replaces `verify`, fails
  validation first, or calls `apply()` directly, where `check` defaults to None.
* **For TASK-089.15.** `setup.at_a_terminal()` exists because NUL answers
  `isatty()` with True on Windows, and `run_streaming` hands the child
  `DEVNULL`. When .15 gives the child a real pipe for the answers document,
  that guard answers False - a pipe is not a terminal - and the engine takes
  the `--apply-stdin` path, which is correct. The guard is not in the way; it
  is why a child with no stdin at all stops instead of hanging.

## Review of 2026-09-22: what it changed

Three verifiers reported 18 findings. Eleven changed the code or a test, five
are recorded, two were partly rejected with the evidence. Every affected test
file was re-run one per process under the TASK-090 fence afterwards: test_setup
15 passed, test_setup_plan 86 passed, test_credentials 35, test_web_transcribe_dialog
54, test_env 43 passed 8 skipped, test_dotenv_commands 9, test_launcher 49 passed
1 skipped, test_proxy 28, test_ollama_setup 19, test_doctor 23, test_web_settings 54.

Fixed, red first for each behaviour:

1. BLOCKER: the console door had no error handling. _sitting applied its own
   answers outside main's one place that maps a failure to a sentence and an
   exit code, so a first run that pressed Enter through the questions (which
   accepts the download) with no token for the gated weights ended in a
   traceback and exit 1 instead of the sentence and exit 3. _sitting now
   returns the answers and main applies them.
2. MAJOR: the asker took any typed word. "Max" reached _validate and raised;
   an Ollama model name nothing validates was written as typed. A choice
   question now accepts only a listed value, case-insensitively, asks again up
   to three times and then takes the shown default.
3. MAJOR: the WAL half of read_only's branch was untested - forcing
   immutable=1 unconditionally kept every test green, and that branch is the
   one the launcher plans in while the app holds the library. Pinned with a
   live -wal; the mutation now fails.
4. read_only yielded twice: an error from the caller's with-body was caught by
   its own except and answered with a second yield, which contextlib turns
   into "generator didn't stop after throw()". Connect in its own try, yield
   outside every handler.
5. The no-key card was suppressed when a key was refused by the service - the
   likelier way to end up with a provider and no key. It is now decided on
   what was written, not on what was offered.
6. The incoming contract number was never read. A document claiming another
   number is refused with exit 2 and nothing applied; one claiming none is
   applied. The stamp's gate stays TASK-089.11's.
7. unasked_only had no caller and no test; pinned, and the mutation bites.
8. tests/test_setup_plan.py's library fixture patched two of the five path
   constants ensure_dirs iterates, so four directories were created in the
   real data directory. All five are patched now.
9. Criterion 12's regression test did not bite: with _validate moved below the
   token write the test stayed green, because the live credential check
   refused the sentinel first. The check is stubbed now, the mutation fails
   with the original red's own words, and the suite no longer asks
   huggingface.co anything.
10. CHANGELOG corrected: --plan did not exist before this task, so it cannot
    have created the data directory - setup did, before it answered anything.
11. Criterion 1's wall time now has an artifact: 4223 / 4165 / 4147 ms on a
    scratch data directory that stayed at 0 entries.

For the orchestrator, before committing: the --hf-token refusal costs more
than the first note said. It returns 2 before any other answer is read, and
the frozen launcher puts --hf-token first in the argv it builds, so a Tk first
run in which somebody types a token loses the whole sitting - provider, tier,
diarize, fetch_models - not just the token. Criterion 5 gives the pass-through
to TASK-089.15, so this is ship ordering, not a defect here: land .09 with .15,
or .15 first. Shipping .09 alone means saying "a first run where a token is
typed applies nothing until .15".

Recorded, not changed: --status still creates the library (TASK-089.03 pins
that on purpose); a library from before the 2026-09-06 rename reads as no
library, because making only the plan read it would report answers against a
library the next write orphans - the orphaning is pre-existing and belongs to
the task that owns adoption. Criterion 3's residue is one read-only command on
Robert's own library; criterion 10's TTY half still needs a real console
window.

Evidence, commands and outputs:
C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/96fe3055-12a8-4348-a17a-5699a082adf4/scratchpad/build/TASK-089.09/notes-review-fixes.md

### Correction and two labels on the note above

Correction to point 2: the asker's fix is the CONSOLE door only, and the first
wording claimed more. --apply-stdin still writes any single-line
llm_model_ollama - _validate checks only that it is one line. Measured:
from_document({"llm_model_ollama": "gemma-typo-nobody-has"}) then _validate ->
accepted. Refusing there means asking a live Ollama inside _validate, a network
read on the write path, which is not something to add unasked. TASK-089.10
renders that question; TASK-089.18 owns the Ollama side.

Point 6, the contract gate, is review-driven and criterion-free: no acceptance
criterion asks for a document-level gate, and ADR-015 gives the gate it names
(the stamp's) to TASK-089.11. It is one branch in main and two tests, so it
reverts in one go if .11 should own it instead.

Also left open, and deliberately not done: the plan still carries no field
saying the library existed but could not be read. Adding one widens the
top-level keys that six tasks render and that criterion 1's test pins exactly -
the orchestrator's call, not a fixer's. The degraded behaviour is pinned
instead: exit 0, every question open.

The eleven test files re-run were derived, not inherited: everything importing
scribe.setup (test_credentials, test_dotenv_commands, test_env, test_launcher,
test_proxy, test_setup, test_setup_plan) plus test_web_transcribe_dialog and
test_web_settings for the transcribe_dialog change, and test_doctor and
test_ollama_setup defensively. Nothing that imports it falls outside them.

One test added after the review's own list: a sitting at a terminal that
succeeds - main([]) with answers, writing the tier row, leaving the provider
row absent for "decide later", printing "saved: defaults" and stamping
default_tier. The failure path was pinned; the success path was not.
tests/test_setup_plan.py is 87 passed now.

## Verification (orchestrator, 2026-09-22)

Measured here, not taken from the build report. Every run fenced
(`SCRIBE_DATA_DIR` and `SCRIBE_ENV_FILE` on a scratch directory).

### Criterion 2 - a plan writes nothing

The claim that rests on a measurement, re-measured on this machine with
SQLite 3.45.3, on a freshly created and cleanly closed WAL database:

    mode=ro                after = {lib.db: 8192, lib.db-shm: 32768, lib.db-wal: 0}
    mode=ro&immutable=1    after = {lib.db: 8192}

So the plain read-only open really does create two files and leave them, and
`immutable=1` creates none. The branch in `read_only` is right for the reason
its docstring gives: immutable lets SQLite read past the write-ahead log, so
the promise is only safe where no `-wal` stands beside the database - and
where one does, the plain open is the one that reads the truth.

A real `--plan` on an empty scratch directory holding only `.env`:

    exit 0
    dir after plan: ['.env']

Nothing created. The whole run took about 4.2 s on this machine (the build's
`timed-plan.txt` reports 4223 / 4165 / 4147 ms; mine agrees).

### Criterion 7 - no value escapes

Sentinels planted in `.env` for Hugging Face and OpenRouter, then `--plan`:

    SENTINEL in stdout: False | in stderr: False

The `found` rows carry `kind`, `name`, `label`, `found`, `source`, `also_in`,
`conflict` and `note`, and no value. Independent of the build's own SENTINEL
test.

### Criteria 1, 16, 20 - the contract's shape

    top-level keys: ['contract', 'downloads', 'found', 'ollama', 'questions']
    contract: 2
    question ids: ['llm_provider', 'llm_key_openai', 'default_tier']
    proxy question present: False

`llm_key_openai` carries `shown_if = {question: llm_provider, equals: openai}`,
so the key question exists per provider and is conditional; the Hugging Face
and OpenRouter key questions are absent because both were found. No proxy
question anywhere, which is criterion 20.

### Criterion 4 - the downloads block

    backend: not-mlx
    mlx-community/whisper-large-v3-turbo -> loads_here false, "not loaded on this platform"
    note: "the Whisper weights this platform loads are not pinned yet ... (TASK-089.16)"
    total_bytes: 0

The mlx entry is excluded from the total, and no number is presented as a
download size that is not one.

### Criteria 9, 12, 14, 17 - measured on both doors

A tier-only sitting on a fresh database:

    after: {'default_tier': 'max'}     - no default_diarize (criterion 9)

The stdin door, with the document shape `{"answers": {...}}`:

    llm_provider "not-a-provider"  -> exit 1, "unknown provider", nothing written
    default_tier "ludicrous"       -> exit 1, "unknown tier", nothing written
    default_diarize true + a tier  -> exit 0, writes default_tier ONLY  (criterion 17)
    every answer null              -> exit 0, "saved: nothing", nothing written (criterion 14)

The flag door refuses an unknown provider with argparse's own exit 2 before
anything is read. Both doors close criterion 12.

One correction to myself: my first attempt at the stdin door used a flat
document and got "saved: nothing", which looked like a silent accept. It was
my document that was wrong - `from_document` reads a nested `answers` key,
`scribe/setup.py:888`. Re-run with the right shape, the refusals are there.

### The suite reaches no network

The build fixed a test that was making a real request to huggingface.co.
Proven independently rather than taken on report: with `HTTP_PROXY`,
`HTTPS_PROXY` and `ALL_PROXY` all pointed at a closed port (127.0.0.1:9),
`tests/test_setup.py` and `tests/test_setup_plan.py` give **102 passed in
11.68s**. An outbound request would have failed or hung.

### Whole suite

One file per process over 80 files, fenced: **2846 passed, 10 skipped, 0 failed, 0 errors** over 81 files (tests/test_setup_plan.py is new). It reconciles exactly with collection: `pytest -q --collect-only tests` reports "2856/2866 tests collected (10 deselected)" and 2846 + 10 = 2856, so nothing stalled or collected short. That is +92 on the run after TASK-089.25 (2764 collected): 87 in the new `tests/test_setup_plan.py` and 5 in `tests/test_setup.py` (10 -> 15).

### Criteria 3, 6 and 10 stay open

**#3** needs a run against the live library, which I may not touch. The
found table half is already answered against Robert's real environment,
registry and Ollama (`real-child-bare-run.txt`, with a scratch library):

    Hugging Face   HF_TOKEN (environment), also in the registry and under two other names
    OpenRouter     OPENROUTER_TOKEN (environment), also in the registry
    OpenAI         not found
    Ollama         Ollama 0.34.2 is running with gemma4:12b, qwen3.5:4b, qwen3.5:9b

No Hugging Face question, no OpenRouter key question, no Ollama model
question. **But the criterion's wording does not fit this machine**: it
expects "no credential question", and `llm_key_openai` is in the list because
Robert has no OpenAI key. It is conditional - `shown_if` names the provider
answer nobody has given - but the console rendering prints it flat under
"Still open" without saying so. Whether that is the right rendering is the
criterion author's call, not mine, so the box stays unticked.

The command, read-only by construction and about four seconds:

    cd D:/Users/Robert/Documents/GitHub/RvdB/MyScribe
    .venv/Scripts/python -m scribe.setup --plan > plan-on-the-real-library.json

with no `SCRIBE_DATA_DIR` and no `SCRIBE_ENV_FILE`. Expected: exit 0 and the
`data/` file list unchanged.

**#6** the OpenRouter check has still never been run with a real key by
anybody. It ships documented and probed without a key. The paid one-word
probe was deliberately not run.

**#10** the TTY half needs a real PowerShell or cmd window; Git Bash's pty
answers `isatty()` False. The non-TTY half is tested and green.

### Ship ordering - this task must not be released alone

Measured, and worse than the task text suggests. `main` returns 2 on
`--hf-token` before it reads anything else (`scribe/setup.py:1153-1155`), and
the launcher puts that flag first in the argv it builds
(`packaging/launcher/myscribe_launcher.py:644-645`) - but every other flag is
in the same command. So a Tk first run in which somebody types a token loses
the whole sitting, not just the token:

    setup.main(['--hf-token', 'hf_SENTINEL...', '--provider', 'ollama',
                '--tier', 'max', '--fetch-models'])
    exit code: 2
    data dir after: []

Provider, tier and the download are dropped with it, and no test catches this
(`tests/test_launcher.py:449` inspects the command string only). TASK-089.15
removes the pass-through. Nothing may be released between the two.

### Found and not fixed, recorded on TASK-089.19

`python -m scribe.setup` orphans a library from before the 2026-09-06 rename,
silently, and it is pre-existing: `db.connect(paths.DB_PATH)` stands at
`setup.py:1168` today, `:163` on HEAD and `:172` in the v0.5.1 tag, while
`db.connect` adopts only when given no path. Reproduced on a scratch copy:
a fresh `myscribe.db` appears beside the legacy `scribe.db`, after which
`adopt_legacy_db()` returns False for ever. The full reproduction is in
TASK-089.19's notes.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
`python -m scribe.setup` is the installer's engine. `--plan` prints one JSON document (contract 2) with what was found and where - never a value - the Ollama state, what this platform would download, and only the questions nobody has answered, each with its default, what skipping costs and where to answer it later; `--apply-stdin` reads the answers back as one document; run bare on a terminal it asks the same list itself, with getpass for secrets, and on a non-TTY it prints what is open and writes no stamp. Four recorded bugs go with it: a plan that migrated the database before answering, a token written before the provider was validated, a tier answer that wrote default_diarize as a side effect, and a bare run that stamped itself done having asked nothing. `--hf-token` stays recognised and is refused with a sentence and exit 2, so a secret never rides on a command line; the token is written to its settings row and no longer to `.env`. Verified by the orchestrator rather than taken on report: `mode=ro` on a cleanly closed WAL database really does create a 32,768-byte `-shm` and an empty `-wal` while `mode=ro&immutable=1` creates nothing (SQLite 3.45.3), a real `--plan` leaves a scratch directory untouched in about 4.2 s, planted sentinels appear in neither stdout nor stderr, a tier-only sitting writes only default_tier, the stdin door refuses an unknown provider and an unknown tier with exit 1 and writes nothing, a diarize key in a document writes nothing, an all-skipped run writes nothing, and choosing OpenAI with no key saves the provider and says what that costs. With every outbound request pointed at a closed port the two setup test files still give 102 passed, so the suite reaches no network. Whole suite over 81 files: 2846 passed, 10 skipped, 0 failed, reconciling exactly with 2856 collected. Criteria 3, 6 and 10 stay unticked: the live-library run, the OpenRouter check with a real key, and a sitting in a real PowerShell window. MUST NOT BE RELEASED ALONE - the refusal costs the whole sitting, because the launcher puts --hf-token in the same argv as the other answers, so TASK-089.15 ships with it. Found and recorded on TASK-089.19, pre-existing and not fixed here: setup orphans a library from before the 2026-09-06 rename.
<!-- SECTION:FINAL_SUMMARY:END -->
