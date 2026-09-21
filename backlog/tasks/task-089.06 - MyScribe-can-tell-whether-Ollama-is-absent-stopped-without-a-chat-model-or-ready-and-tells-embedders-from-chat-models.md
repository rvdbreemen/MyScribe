---
id: TASK-089.06
title: >-
  MyScribe can tell whether Ollama is absent, stopped, without a chat model, or
  ready - and tells embedders from chat models
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-21 23:37'
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

Needs a real machine: Robert's machine for the ready state, and his WSL for the Linux install locations. The macOS locations need a real Mac: the Mac is somebody else's (brief: G9, decided by Robert on 2026-09-20); its points are bundled for the Mac's owner in TASK-089 criterion 10, and until that sitting they are reported as not verified.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 scribe/ollama_setup.state() returns absent, installed_not_running, running_no_chat_model, ready or unknown (criterion 3), with the binary path, the version and the chat model names. It reuses OllamaProvider's GET /api/tags, which now returns the payload. There is no second HTTP client.
- [ ] #2 'absent' requires ALL of these: no `ollama` through shutil.which; none in the known install locations; no answer at 127.0.0.1:11434; and no OLLAMA_* variable in the process environment, `.env` or the registry. Any doubt reports present. The task notes say which macOS and Linux locations were verified against Ollama's own docs and which were not. On a real machine, Linux is checked by Robert in WSL. macOS needs a real Mac. The Mac is somebody else's, decided by Robert on 2026-09-20 (brief: G9), so this point is not asked on its own: it goes into the bundled macOS list of TASK-089 criterion 10 with its command and its expected output, and reads 'not run' until that sitting. If it is not run, the box stays unticked and the parent's final summary lists it.
- [x] #3 Chat-capable means 'completion' is in capabilities, and ['tools', 'embedding'] is not chat-capable. If the key is absent it falls back to POST /api/show. If neither answers the result is 'unknown': never 'ready', never 'absent', and treated as present wherever the state is used. MockTransport tests cover: five embedders only gives running_no_chat_model; the missing-key case; and a refused connection with a stripped PATH and empty fake directories gives absent.
- [x] #4 Red first: with llm_model_ollama saved as gemma4:12b, OllamaProvider(conn).model is that tag. It is qwen3.5:4b today. The Settings readiness line tests the saved model.
- [x] #5 Settings' model refresh stores chat-capable models only, so an embedder cannot be picked as the chat model.
- [x] #6 `python -m scribe.doctor` gains an optional 'ollama' line that reports the state in words and never fails the gate.
- [x] #7 A real run on Robert's machine reports ready, lists only chat-capable models and no embedder, and GET /api/version and `ollama list` are identical before and after.
- [x] #8 The installed_not_running state is shown on a real machine only if Robert stops his own Ollama for it; MyScribe never stops or starts it. Otherwise the fake-directory test stands alone, and the notes say so.
- [x] #9 .env.example no longer advertises OLLAMA_HOST (:24-26), which nothing reads.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Facts re-checked against the live daemon (read-only, evidence in the build dir): version is 0.34.2, not the 0.34.0 the description and design spec line 245 say; 8 models, 5 embedders, 3 with 'completion' (qwen3.5:4b, qwen3.5:9b, gemma4:12b); EVERY model carries `capabilities` on /api/tags at this version, so /api/show is the old-daemon path only. `shutil.which` finds %LOCALAPPDATA%\Programs\Ollama\ollama.EXE; four OLLAMA_* names sit in HKCU Environment and in the process env, so this machine can never read absent. Cited lines moved: `self.model = model or self.default_model` is ollama.py:270 (task says :254), available() :400-424, provider_rows ai_ui.py:1169 (task says :1146). .env.example:24-26 is the OLLAMA_HOST block and nothing reads it.
2. scribe/llm/ollama.py seam, all on the one existing client (trust_env=False, no second HTTP client): `tags()` returns the /api/tags payload rows; `models()` keeps its meaning (all pulled names) but is built on tags(), so available() stays one cheap GET on a settings render; `chat_models()` filters on 'completion' in capabilities with a PER-MODEL POST /api/show fallback and reports unknown when neither answers; `version()` is GET /api/version on the same client, None when the daemon is silent. No subprocess: the binary is never run, not even `ollama --version` (overrule cheaply if the orchestrator wants the binary's own version in the stopped state).
3. Criterion 4, red first: a test that `OllamaProvider(conn).model` is the saved `llm_model_ollama` (gemma4:12b); keep the failing output; then read the row in __init__ the way ceiling() reads llm_num_ctx (db.LOCK, guarded), with the key from one shared constant instead of a third spelling of 'llm_model_' (it lives twice today: llm/__init__.py:145 and web/ai_ui.py:73; ollama.py cannot import llm/__init__ - circular - so base.py is the candidate). queue_provider_test already resolves the saved model, so this fixes the readiness line only, not the test button.
4. New scribe/ollama_setup.py: a frozen State (state, binary path, version, chat model names, the OLLAMA_* variable NAMES and their sources - never a value - and a `present` property that is True for everything but absent). state() composes shutil.which, a per-platform table of known install locations (injectable for the fake-directory test), the loopback probe through OllamaProvider, and OLLAMA_* from the process env, credentials.dotenv_values and the registry. Any doubt is present; unknown is never ready and never absent. Nothing installs, starts, pulls or configures.
5. The registry needs a new seam in scribe/credentials.py that ENUMERATES names under a prefix (registry_hits reads one name). It gets a stub in tests/conftest.py beside registry_hits/dotenv_values/login_file in the same change, or this machine's four real OLLAMA_* names make the 'absent' test impossible here.
6. Criterion 5: `chat_models()` gets a default on base.Provider (return self.models()) and the refresh endpoint calls that, so no `if provider ==` branch. The two monkeypatches on OllamaProvider.models in tests/test_web_ai.py (:987, :1206) are updated deliberately.
7. Criterion 6: an optional doctor check that reports the state in words; optional=True renders SKIP and never fails the gate. WEB_SAFE_CHECKS is COMPUTED (doctor.py:628), so adding to CPU_CHECKS also hands it to the web process - decided explicitly and only on the cheap single-GET path.
8. Criterion 9: remove .env.example:24-26. No test asserts that block.
9. Tests (MockTransport, one file per pytest process, fence exported): five embedders only gives running_no_chat_model; a tags row without capabilities falls back to /api/show; neither answering gives unknown, never ready, never absent; a refused connection with a stripped PATH and empty fake directories gives absent. Plus the TASK-089.05 regression guard no criterion lists: a dead HTTP_PROXY/HTTPS_PROXY with the loopback server of tests/test_proxy.py:136, asserting every path state() uses arrives at 127.0.0.1. Bite shown by mutating a COPY under the scratchpad.
10. Criterion 7, read-only on this machine: GET /api/version, `ollama list` and `ollama ps` captured before and after a state() run and diffed; state() reports ready and lists the three chat models and no embedder.
11. Not built here, and that is a decision, not an omission: scribe/ollama_release.json, install_plan(), the marker, and anything that installs, downloads, pulls or starts - all TASK-089.18, ADR-017.
12. An agent cannot close: criterion 8's real stopped Ollama (Robert stops his own), criterion 2's Linux locations (Robert in WSL) and its macOS locations (G9, bundled into TASK-089 criterion 10, reads 'not run'). The install-location table's sources are recorded per platform in the notes, verified or not.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (agent, 2026-09-22) - detection only, nothing installs

Evidence lives in the session build directory `.../scratchpad/build/TASK-089.06/`;
file names are given per claim below.

### What changed

* **`scribe/ollama_setup.py`, new.** Frozen `State` (state, binary, version, chat_models,
  variables, `present`). `state()` composes four signals - `shutil.which`, a per-platform
  install-location table, a loopback probe through `OllamaProvider`, and `OLLAMA_*` from the
  process, `.env` and the registry - and reports absent only when all four are silent.
  `describe()` holds the sentence, so the doctor and the later setup sitting (ADR-015) share
  one wording. It takes no connection: that is what makes a real run trivially read-only.
  `locations`, `environ` and `host` are seams, and `shutil.which` is given `path=` from the
  same environ, so one seam answers for one machine.
* **`scribe/llm/ollama.py`.** `tags()` returns the `/api/tags` rows on the one existing client
  (`trust_env=False`, no second HTTP client); `models()` keeps its meaning - every pulled name -
  and is built on `tags()`; `chat_models()` filters on `completion` with a per-model
  `POST /api/show` fallback and returns `None` when nothing could be read; `version()` is
  `GET /api/version`. `__init__` reads the saved `llm_model_ollama` (criterion 4).
* **`scribe/llm/base.py`.** `MODEL_SETTING_PREFIX` and `model_setting_key()` move here - it was
  spelled in `llm/__init__.py` and `web/ai_ui.py`, and `ollama.py` can import neither.
  `Provider.chat_models()` defaults to `self.models()`, so the refresh endpoint needs no
  `if provider ==` branch and no cloud provider changes.
* **`scribe/credentials.py`.** `registry_names(prefix)` enumerates variable NAMES under a prefix
  (`registry_hits` reads one name). Names and hives only - it never reads a value. Stubbed in
  `tests/conftest.py` beside the other four seams.
* **`scribe/web/settings.py`.** The model refresh asks `chat_models()`; `None` stores nothing and
  says so, rather than storing an empty list.
* **`scribe/doctor.py`.** Optional `check_ollama` in `CPU_CHECKS`, imported inside the function
  the way `check_models` imports `scribe.models`.
* **`.env.example`.** The `OLLAMA_HOST` block is gone - lines 24-27 including the trailing blank,
  because cutting only 24-26 would have left a double blank line.

### A defect the tests found, and the decision it forced

`_raise_for_status` maps an HTTP 500 to `Unreachable`, and so does a refused connection - so a
daemon answering 500 read as **absent**, which is exactly the state the install offer is gated on
(ADR-017). Fixed with a narrow subclass `base.NothingAnswered(Unreachable)`, raised only by the
transport path: every `except Unreachable` and every retry behaves as before, and only `state()`
reads the difference. Found by `test_a_daemon_that_answers_nonsense_is_present_and_never_ready`.

### A claim of mine the mutation proof refuted

I first wrote that a test for the absence of `embedding` would have let `qwen3-embedding:0.6b`
through. **That is wrong**: it reports `['tools','thinking','embedding']`, so an absence test
excludes it too. On all eight models here the two tests agree exactly, and the mutant swapping one
for the other passed every test - `red-mutation-proof.txt`, first run. The docstrings are corrected
and `test_a_model_that_is_neither_a_chat_model_nor_an_embedder_is_not_offered` now covers the case
where they really differ: a model carrying neither capability.

### Install locations: what is verified and what is not

Both install scripts were fetched again on 2026-09-22 and are byte-identical to the repository's
record (sha256 25f64b81..., 310071580b...), so its line numbers still hold. Full record:
`install-locations-evidence.txt`.

* **Windows - VERIFIED, two sources.** `install.ps1:115-118` gives `%LOCALAPPDATA%\Programs\Ollama`,
  and `shutil.which` on this machine resolves `C:\Users\rvdbr\AppData\Local\Programs\Ollama\ollama.EXE`.
  They agree.
* **macOS - sourced from `install.sh:66-83`, NOT run.**
  `/Applications/Ollama.app/Contents/Resources/ollama` and `/usr/local/bin/ollama`. Nobody here has
  a Mac; this goes into TASK-089 criterion 10's bundled list (G9) and reads 'not run' until then.
* **Linux - sourced from `install.sh:159-175`, NOT run.** `/usr/local/bin`, `/usr/bin`, `/bin` - the
  script takes whichever is on PATH. Robert's WSL is what would verify this.
* **Deliberately absent, because no source was read:** Homebrew, Snap, Flatpak, distribution
  packages, hand-built copies. They are claimed nowhere, and the other three signals still catch
  them - which is the reason absent needs all four.
* `%LOCALAPPDATA%\Ollama` (`install.ps1:285`) is Ollama's state folder, not a binary, and is kept
  out of the table: a leftover one would make an absent machine read present.

### Evidence

* **Criterion 4, red first** - `red-llm-ollama-saved-model.txt`:
  `AssertionError: assert 'qwen3.5:4b' == 'gemma4:12b'`, 1 failed 41 passed. Green afterwards in
  `green-test_llm_ollama.txt`: 50 passed.
* **Criterion 7, real read-only run** - `real-run-this-machine.txt`. `state()` reports `ready`,
  version 0.34.2, the binary path above, chat models `['gemma4:12b','qwen3.5:4b','qwen3.5:9b']` and
  no embedder among them. `GET /api/version`, `ollama list` and `ollama ps` are captured before and
  after and the diff reads `identical, identical, identical` - the pinned `qwen3-embedding:4b` is
  still loaded 'Forever', untouched. No database was opened, and the product code runs no
  subprocess at all; the two `ollama` commands are the criterion's own evidence, not the installer
  path ADR-017 governs.
* **Criterion 6, rendered line** - `real-doctor-line.txt`:
  `[OK  ] ollama       Ollama 0.34.2 is running with gemma4:12b, qwen3.5:4b, qwen3.5:9b`, then
  `All required checks passed`, exit 0.
* **Mutation proof** - `red-mutation-proof.txt`, on a copy under `mut/`: five mutants, all five now
  bite (4 of 5 in the first run; see above). `grep -rn MUTANT scribe tests` finds nothing.
* **Test files run, one per process, fence exported** (`green-*.txt`): test_ollama_setup 17,
  test_llm_ollama 50, test_web_ai 124, test_doctor 22, test_proxy 28, test_credentials 34,
  test_llm_tasks 159, test_web_settings 46, test_web_transcript 98, test_stage_transcribe 66,
  test_ingest_urls 112, and test_disk_floor, test_feed_backfill, test_feed_follow, test_llm_chat,
  test_llm_chunking, test_llm_cleaning_gate, test_llm_headroom_script, test_llm_labels,
  test_llm_live_log, test_llm_privacy, test_llm_providers, test_llm_selftest, test_llm_speakers,
  test_llm_task_providers, test_models, test_setup, test_env, test_launcher, test_dotenv_commands,
  test_paths - all passed. test_llm_live: 7 deselected (gpu).

### Two things a reviewer should see

1. **`tests/test_web_ai.py`'s `no_ollama` fixture guarded the wrong seam.** It stopped `models`, and
   the refresh now calls `chat_models()` -> `tags()`, so
   `test_an_unreachable_provider_leaves_the_model_field_as_free_text` reached **this machine's live
   daemon** and stored its three real chat models. The fixture now patches `tags`, the method that
   actually opens the socket. The monkeypatch in `test_refreshing_a_providers_model_list_...` moved
   to `tags` too, deliberately: patching `chat_models` there would have left the test green while
   proving nothing about the filter that criterion 5 is about.
2. **`check_ollama` is excluded from `WEB_SAFE_CHECKS`, and that is a decision, not an oversight.**
   The loopback GET itself would be safe - `provider_rows` already makes it on every settings render
   - but the `/api/show` fallback is one POST per model on an old daemon, and a page render that
   quietly became nine requests is what that tuple exists to prevent. Pinned by
   `test_the_web_process_never_asks_about_ollama`, not only by a comment.

### Not done here, and why

* Criterion 2's **Linux** half needs Robert in WSL; its **macOS** half needs a real Mac (G9) and
  reads 'not run'. The sources for both are recorded above and in the evidence file.
* Criterion 8's **real stopped Ollama** needs Robert to stop his own; MyScribe never starts or stops
  one (ADR-017) and an agent must not. The fake-directory and refused-connection tests cover
  `installed_not_running` on their own: `test_the_binary_on_the_path_alone_makes_it_present` and
  `test_a_binary_at_a_known_install_location_alone_makes_it_present`.
* Nothing that installs, downloads, pulls, starts or configures was built: that is TASK-089.18.
  `scribe/ollama_release.json` is not created here.

### Found outside the criteria, not fixed

* `tests/test_feed_first_episode.py::test_a_poll_below_the_disk_floor_queues_nothing_and_says_so`
  fails under the long fenced `SCRIBE_DATA_DIR` this session must use: `feeds.poll` truncates
  `last_result` to about 120 characters and the long path pushes the '10 GB' out of the message.
  Proven environmental and not caused by this change - the same file passes with a short fence:
  `green-test_feed_first_episode-short-fence.txt`, 13 passed.
* The doctor's summary suffix reads '(N optional check(s) not wired yet)', which will read oddly
  once it counts an Ollama that is simply not installed. Pre-existing wording; not touched.

### Addendum after review (same sitting)

* **A third defect, found by asking for the words of every state.** `describe()` on a
  hand-built `READY` with no model names rendered `"Ollama is running with "` - a dangling
  "with" and a trailing space. `state()` cannot produce that State, but `describe()` takes any
  State because a setup front-end holds one it was handed (ADR-015), so the sentence is this
  function's job to get right. Fixed, and pinned by
  `test_every_state_has_a_sentence_of_its_own`, which renders all five states from hand-built
  States and asserts they are five different, non-empty, non-dangling lines.
  `test_a_stopped_ollama_says_where_it_was_found` covers the two halves of the stopped
  sentence: the binary when there is one, the variable name otherwise - which is the half this
  machine would report.
* **How criterion 3's "stripped PATH" is implemented**, since a reader checking the criterion
  against the test will look for something else: `state()` passes `path=` to `shutil.which`
  from the same `environ` it was given, so the test hands over an empty machine rather than
  mutating `os.environ["PATH"]` and hoping. The fixture `no_ollama_anywhere` does both - it
  points the real PATH at an empty folder as well - because `shutil.which` with no `path=`
  reads `os.environ` whatever the caller said.
* Re-run after these changes: test_ollama_setup 19 passed, test_doctor 22, test_llm_ollama 50,
  test_proxy 28. The real read-only run was repeated and still reports `ready` with the three
  chat models and `identical, identical, identical` (`real-run-this-machine.txt`).

## After review (fixer, 2026-09-22)

Three reviews, twelve findings: seven changed code, five were answered without
one. Every code change below has a mutant that bites
(`red-mutation-proof-review.txt`: seven of seven) or a tripwire run.

* **The readiness line no longer goes green for an embedder** (two reviewers,
  major). Criterion 4 made `OllamaProvider(conn)` report on the *saved* model,
  and `available()` still asked only "is it pulled" - which every embedder is.
  Red first: `red-available-embedder.txt`, `assert True is False`. It now reads
  `capabilities` off the row the one GET already returned, so a settings render
  still costs one request and no `/api/show` fan-out; a row without the key
  keeps the old answer. Live daemon, read-only
  (`real-available-after-review.txt`): `bge-m3:latest` reads "not a model you
  can chat with: it reports embedding, not `completion`", `qwen3.5:4b` ready.
* **Six tests in tests/test_doctor.py were answered by this machine's daemon**
  (major). `check_ollama` joined CPU_CHECKS and nothing stubbed
  `ollama_setup.state`. Shown with a tripwire rather than by reading:
  `trip-doctor.txt`, 6 failed. Now stubbed in tests/conftest.py beside the
  credential seams, with `ollama_state_unstubbed` for the two files whose
  subject it is (tests/test_ollama_setup.py autouse, tests/test_proxy.py's
  detector test). After: `trip2-test_doctor.txt`, 22 passed under the same
  tripwire.
* **`credentials.registry_names` was asserted by nothing** (major): a version
  returning `()` passed every file that names it, and it is a leg the install
  offer stands on (ADR-017). One Windows-only read-only test now runs the
  shipped reader against the real hives - names and a prefix derived from them,
  never a value - through `registry_names_unstubbed`, the `library_db_unstubbed`
  idiom.
* `chat_models()` answers unknown whenever a row could not be read *and*
  nothing chat-capable was seen, not only when every row is unreadable. The old
  rule told a user to `ollama pull` a model they may already have under the
  name nobody could read.
* The doctor's mark is READY and no longer `present`: `[OK  ] ollama installed
  at ... but not answering` contradicted the sentence beside it. The install
  hint is printed for `absent` only, because `render()` prints a hint for every
  check that is not ok and ADR-017 forbids acting on that advice in the other
  states. This machine still reads `[OK  ] ollama  Ollama 0.34.2 is running
  with gemma4:12b, qwen3.5:4b, qwen3.5:9b`, exit 0
  (`real-doctor-line-after-review.txt`).
* `_saved_model()` reads the row inside the `try`, the way `ceiling()` does: a
  connection without `row_factory` raised `TypeError` out of `__init__`, which
  is the settings render. No caller reaches it today.
* `base.Provider.chat_models()` claimed every model a cloud provider lists can
  be chatted with. It cannot - `openai_like.models()` passes `/v1/models`
  through unfiltered. Reworded to what was actually decided; no code change.
* tests/test_web_ai.py: the new cloud-refresh test asked for no `no_ollama`,
  and the monkeypatch in `test_pressing_test_asks_no_provider_anything_in_the_web_process`
  had to move from `models` to `tags`, because `available()` reads the rows now.
  Both shown by the same tripwire (`trip3-test_web_ai.txt`).
* Criterion 2's macOS point now carries the command and the expected output
  that TASK-089 criterion 10's bundled list needs
  (`install-locations-evidence.txt`). Its box still stays unticked.

Answered without a change:

* Criterion 9's `.env.example` removal stays unasserted, deliberately: it is a
  documentation file and the repository asserts no other line in it. Saying so
  here is the point, so the next reader does not rediscover it.
* Two tests in tests/test_web_ai.py (:1487, :1500) and eighteen in
  tests/test_web_settings.py render the settings page without `no_ollama` and so
  reach this machine's daemon. **Not caused by this task**: the same tripwire on
  a clean HEAD fails the same eighteen (`trip-base-test_web_settings.txt`).
  Reported, not widened - a settings render has always made that one GET.

Run after the fixes, one process per file, both fence variables exported
(`final-*.txt`): test_ollama_setup 19, test_llm_ollama 55, test_doctor 23,
test_credentials 35, test_proxy 28, test_web_ai 124, test_web_settings 46,
test_llm_tasks 159, test_llm_chat 27, test_stage_transcribe 66,
test_llm_providers 52, test_llm_selftest 14 - all passed.

### Three things the orchestrator needs in front of it

* **The behaviour change, stated as before and after.** The task description's
  measured fact of 2026-09-20 was `OllamaProvider(model='bge-m3:latest').available()`
  **answers ready**. On the same daemon today it answers **False**: "Ollama is
  running at http://127.0.0.1:11434 but 'bge-m3:latest' is not a model you can
  chat with: it reports embedding, not `completion` - pick another in Settings"
  (`real-available-after-review.txt`, read-only; `ollama ps` unchanged, the
  pinned `qwen3-embedding:4b` still loaded "Forever"). `qwen3.5:4b` and
  `gemma4:12b` still read ready.
* **Criterion 5, both halves.** The refresh stores chat-capable models only, and
  a typed embedder can no longer show a green readiness row. It can still be
  *saved*: `scribe/templates/_settings_llm.html` offers the free-text field
  outside the `{% if p.model_groups %}` block, and the POST does not
  re-validate. Worth a follow-up under TASK-089, next to criterion 10's "an AI
  action that cannot be answered shows what is missing".
* **One counter moved.** `render()`'s summary counts optional checks that are
  not ok. With the mark now READY-only, a machine whose Ollama is installed but
  stopped reads `All required checks passed (2 optional check(s) not wired
  yet).` where it read `(1 ...)` before; `absent` already counted, and this
  machine is ready so its line is unchanged. Measured in
  `doctor-summary-counter.txt`. The wording is pre-existing and shared by every
  optional check, so it is reported and not touched.

Verified independently by the orchestrator on 2026-09-22.

The suite, one file per process, fenced at a short path: 2685 passed, 0 failed, 10 skipped over 79 files, against 2641 before this task. The live library carries the same modification time before and after.

Measured by me against the real daemon, read-only, rather than taken from the report:
- The split is right. Of the eight models on this machine, chat_models() returns three - gemma4:12b, qwen3.5:4b, qwen3.5:9b - and filters five embedders: bge-m3, embeddinggemma, granite-embedding and qwen3-embedding twice. Found through the completion capability, not by guessing at names.
- The hole the review closed is really closed. With llm_model_ollama set to bge-m3:latest, available() answers False; with qwen3.5:4b it answers True with 'ready'. Before the fix an embedder saved as the chat model rendered a green ready row, and it was criterion 4's own change that made that path reachable from the settings page. Two reviewers saw it; one wanted it filed as a follow-up and the fixer repaired it instead, which was the right call - it is the false green this task exists to prevent.

Criterion 2 stays unticked by its own terms: the Linux locations need Robert in WSL and the macOS ones need the Mac, which is somebody else's (G9). They go into the bundled macOS list of TASK-089 criterion 10 and read 'not run' until that sitting.

Criterion 8 is ticked on the terms its own text allows: the fake-directory test stands alone, because showing installed_not_running on a real machine would mean stopping Robert's daemon, which MyScribe never does (ADR-017) and an agent must not.

One number corrected, mine against the agent's: the fix pass reports that feeds truncates last_result at about 120 characters. It is 200 (scribe/ingest/feeds.py:458 and :464), and the threshold at which the disk floor falls out of the sentence is a data path of 142 characters - the figure in TASK-091, which is therefore right as written.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
MyScribe can now tell what Ollama is doing, and an embedder can never be picked as the chat model.

scribe/ollama_setup.py reports one of five states - absent, installed but not running, running without a chat model, ready, or unknown - from four signals: the binary through shutil.which, the known install locations, a loopback probe, and any OLLAMA_* variable in the environment, .env or the registry. Absent requires all four to say so; any doubt reports present, because ADR-017 gates the offer to install on absence and a false absent is what would install over somebody's Ollama. Nothing here starts, stops, pulls or configures anything.

Telling embedders from chat models is the point. On this machine eight models are pulled and only three can hold a conversation; the other five are embedders. The test is the positive one - the model carries the completion capability - because an embedder carries tools and thinking too, and only the absence of completion separates them. Rows that do not say fall back to a per-model ask, and a daemon that answers neither reads unknown rather than ready.

Two defects this task depended on, both fixed: OllamaProvider ignored the saved llm_model_ollama row, and the settings refresh stored every pulled name, embedders included.

One defect this task created and then closed, found by review rather than by the code reading wrong: once the provider read the saved row, an embedder saved as the chat model rendered a green ready row on the settings page. available() now validates against chat-capable models. Measured both ways before and after.

The same review caught six tests in test_doctor.py answering from this machine's live daemon, proven with a tripwire before it was fixed, and found twenty more that pre-date this task - they belong to TASK-090.

Criterion 2 stays unticked: the Linux and macOS install locations need WSL and a Mac. Criterion 8 is ticked on its own terms, with the fake-directory test standing alone, because showing a stopped Ollama would mean stopping Robert's.

Suite fenced at 2685 passed, 0 failed, 79 files.
<!-- SECTION:FINAL_SUMMARY:END -->
