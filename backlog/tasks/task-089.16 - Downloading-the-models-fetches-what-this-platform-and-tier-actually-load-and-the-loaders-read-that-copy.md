---
id: TASK-089.16
title: >-
  Downloading the models fetches what this platform and tier actually load, and
  the loaders read that copy
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 13:58'
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

Needs a real machine: Robert's RTX 3080 for the offline transcription on Windows, and his WSL for Linux. A real Apple Silicon Mac for the MLX half: the Mac is somebody else's (brief: G9, decided by Robert on 2026-09-20); its points are bundled for the Mac's owner in TASK-089 criterion 10, and until that sitting reported as not run. Nobody has verified that mlx-whisper loads from a local folder.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Catalogue entries carry platforms or backends, and a tier. Pins (revision, per-file sha256, size) exist for the CT2 turbo repo under the id faster-whisper requests, for Systran/faster-whisper-large-v3 and for mlx-community/whisper-large-v3-mlx. The pins were not collected in the design run; the notes say where each came from and when. The diff of `python -m scribe.models` output on Windows is shown, with the reason it moved.
- [x] #2 status(), present() and ensure() agree: a hub-cache snapshot at the pinned revision counts as present. On Robert's machine `--fetch` downloads nothing. Red first: today ensure() would download pyannote again.
- [x] #3 ensure() fetches gated entries first. With no token, the first and only request is for the gated repo, and the probe output is shown.
- [x] #4 Red first, with the truncating local-server probe: a short read raises reason 'offline', not 'mismatch'. A .part file resumes with Range, with up to 3 retries.
- [x] #5 Free space is checked on the volume the files land on, before the first byte. When it is too small, one sentence gives both numbers and no download is attempted. ENOSPC surfaces as disk-full, not as 'offline'.
- [x] #6 transcribe.load_model and the MLX backend load from the pinned local folder when it is complete, and behave as today otherwise. tests/test_stage_transcribe.py:43-54 (ADR-004) stays green.
- [ ] #7 Needs a real GPU: with a fresh HF_HOME and a scratch data directory on Windows, fetch turbo, then transcribe tests/fixtures/clip30.wav with HF_HUB_OFFLINE=1. The word count and the seconds are printed. The same run is done in WSL. Robert runs both, on his RTX 3080.
- [ ] #8 Needs a real Mac: the same offline run on Apple Silicon answers whether mlx-whisper loads from a local folder. The Mac is somebody else's, decided by Robert on 2026-09-20 (brief: G9), so this point is not asked on its own: it goes into the bundled macOS list of TASK-089 criterion 10 with its command and its expected output, and reads 'not run' until that sitting. If it is not run, the box stays unticked and the parent's final summary lists it. Until it is run the MLX half is not called done.
- [x] #9 401 and 403 produce different sentences in models.fetch_file and in doctor._gated_repo_reachable.
- [x] #10 The Authorization header is sent only for gated repos and is dropped on a cross-host redirect. The two-server probe is red, then green.
- [x] #11 Choosing tier max downloads large-v3 and nothing for turbo. This makes TASK-040.06 AC5 true.
- [x] #12 `--plan`'s downloads now gives real bytes for this platform and tier, and the 'not pinned yet' line that TASK-089.09 carried is gone. The diff of its output is shown.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Collect the pins (AC1). Hub API (HfApi.model_info files_metadata=True, anonymous) gives the revision sha and the LFS sha256+size of every big file; small files carry no LFS digest, so they are hashed from a scratch HF_HOME download (a few MB, never near Robert's cache) or from his cache copies whose size+revision the API already confirmed - the notes record which, per file, with the date. No --fetch, no 1.6 GB. Three repos: mobiuslabsgmbh/faster-whisper-large-v3-turbo (rev 0a363e91..., the id faster_whisper.utils._MODELS maps 'large-v3-turbo' and 'turbo' to, v1.2.1 - also the repo in this machine's hub cache), Systran/faster-whisper-large-v3 (rev edaa852e...), mlx-community/whisper-large-v3-mlx (rev 49e6aa28...). File set for the CT2 repos = faster_whisper's own allow_patterns (config.json, preprocessor_config.json, model.bin, tokenizer.json, vocabulary.json).
2. models.json gains per entry: backends (mlx, or cuda+cpu, or every backend for pyannote), tier (turbo/max, absent for pyannote) and the loader alias (the faster-whisper name / the mlx repo id). The alias stays data, never a Python literal: ADR-004's grep (tests/test_stage_transcribe.py:43-54) allows large-v3-turbo in stages/transcribe.py only, and only scans .py/.html/.js/.css - models.json is data the way tools.json is.
3. models.py owns the platform answer once. Move setup.transcriber() (docstring and its measured 4.9 s included) down here as models.backend_here(): mlx_available() first, NOT_MLX otherwise, so no torch import - check_models is in WEB_SAFE_CHECKS and ADR-001 forbids it. Add models.loads_here(model, backend) and models.wanted_here(backend=..., tier=...). status() keeps returning EVERY catalogue row (setup.downloads names the entries that do not load here, and tests/test_setup_plan.py:636 pins that) and each row gains loads_here and tier; missing() and ensure() default to wanted_here.
4. AC2: ensure() passes base=where or root() into present(), so hub_snapshot is never consulted (models.py:254,260). Red first with a hub-cache fixture, then present() consults the cache for the default destination while --dest keeps meaning the folder only.
5. AC3 gated first (sort the walk by gated), AC4 short read = offline + .part resume with Range and 3 retries, AC5 shutil.disk_usage on the volume the files land on before the first byte and an errno.ENOSPC branch BEFORE the generic OSError->offline, AC9 401 and 403 split in fetch_file and in doctor._gated_repo_reachable, AC10 an opener whose redirect handler drops Authorization when the netloc changes and sends it only for gated repos (models.py:205). ModelError's docstring names three reasons today; a fourth (disk) is a deliberate edit there and in main()'s exit-code map.
6. AC6 loaders: transcribe.load_model and mlx_backend prefer the pinned local folder when models says it is complete, and fall back to today's behaviour otherwise (WhisperModel takes a directory, faster_whisper/transcribe.py:678; mlx-whisper's local-folder support is verified by nobody and stays AC8). Keep tests/test_stage_transcribe.py:43-54 (the ADR-004 grep) and :207-238 (turbo substitutes large-v3 for translate) green and say so.
7. AC11 tier: ensure()/downloads() take the chosen tier (answers.tier, else the stored default_tier row), so max fetches large-v3 and nothing for turbo. setup.py's question text at :606 already prints models.human(total_bytes) and becomes true for free.
8. AC12 setup.py reads loads_here and the backend from models instead of its own copies (:430, :449), UNPINNED_HERE (:139-142) and its use at :488 go, and tests/test_setup_plan.py:636 moves with the line it pins. Show the diff of --plan's downloads block.
9. Red first for AC2, AC3, AC4 and AC10, output kept. The AC4 and AC10 probes run against loopback http.server of my own, in the house pattern of tests/test_launcher.py and tests/test_proxy.py; the AC10 green is the second host recording no Authorization header. A new test pins models.json's CT2 aliases against faster_whisper.utils._MODELS and the MLX ones against mlx_backend.repo_for, so AC1's claim stays true.
10. Tests that move: test_models.py:168/182, test_setup_plan.py:132/636/658, test_doctor.py:709, and any count of "2 model(s) present". paths.MODELS_DIR is an import-time constant, so a scratch models dir needs monkeypatch, not SCRIBE_DATA_DIR alone.
11. Evidence in the scratchpad: the before/after diff of python -m scribe.models on Windows (fenced, so it reads this machine's hub cache and a scratch MODELS_DIR), the diff of setup --plan's downloads, the red and green of every red-first criterion, per-file pin provenance, and the mutation run on a copy outside the repository. One test file per pytest process with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE exported.
12. Not closed by an agent: AC7 (fresh HF_HOME, fetch, HF_HUB_OFFLINE=1 transcribe on the RTX 3080 and in WSL) is Robert's - the exact commands and expected output go in the notes, box unticked. AC8 needs a Mac nobody here has: commands and expected output into the notes, reported as not run, and it belongs to TASK-089 criterion 10. The launcher's Tk checkbox at packaging/launcher/myscribe_launcher.py:886 still says "about 1.6 GB" whatever was picked (it moved from :574); no criterion names it and it needs a person at the screen - reported, not fixed.

13. The doctor is the headline fix and is explicit: doctor.check_models() counts only the status rows that load here at the active tier, so on Windows the MLX rows never reach 'absent' and the card stops asking for 1.6 GB this machine cannot load. Evidence is the before/after of its detail and fix_hint (the before is the orchestrator's fenced run on 2026-09-22). doctor.py:725 shortens the repo name with split('/')[-1]; once the filter lands that name is honest here (faster-whisper-large-v3-turbo), so it is kept deliberately and the line says why rather than being left to chance.

14. Before writing code: /adr-kit:context (read-only) over models.py, doctor.py, setup.py and the two loaders, because the catalogue gaining platform and tier keys sits next to ADR-004 and ADR-001. No ADR is authored - none is in the criteria - and if context says a decision is owed, it is reported to the orchestrator.

15. AC2's second half cannot be produced in this build: running python -m scribe.models --fetch is forbidden here. What an agent shows is missing() returning empty for this platform and tier on this machine, over the same predicate --fetch walks, plus the read-only cache check that says why - pyannote at 3533c8cf with all five pinned files at their pinned sizes, the CT2 turbo at 0a363e91 with all five. The literal --fetch run on Robert's machine is left to the orchestrator.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (agent, 2026-09-22)

Evidence directory: `C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/96fe3055-12a8-4348-a17a-5699a082adf4/scratchpad/build/TASK-089.16/`.
Every pytest process ran one test file at a time with `SCRIBE_DATA_DIR` and `SCRIBE_ENV_FILE` pointing at a scratch fence (TASK-090). No `--fetch` was run and nothing was written near `C:/Users/rvdbr/.cache/huggingface`.

### The shape of the fix

`scribe/models.py` now owns the platform-and-tier answer, and the doctor and the plan both read it instead of keeping copies. `models.backend_here()` is `setup.transcriber()` moved down with its docstring; `setup.NOT_MLX` is a re-export, not a second constant. New in `models.py`: `loads_here(model, backend)`, `wanted_here(backend=, tier=)`, `local_dir(alias, backend)`, `DEFAULT_TIER`, `EXIT_CODES`, `room_for()`, `DropAuthAcrossHosts`, `_one_request()`. `status()` still returns every catalogue row; each row gained `tier`, `loads_here` (platform) and `wanted` (platform AND tier). `missing()` and `ensure()` default to `wanted_here`.

`setup.transcriber()`, `setup.loads_here()` and `setup.UNPINNED_HERE` are gone; `setup.NOT_THIS_TIER` is new. `packaging/fetch_models.py` now names every repository explicitly, so a payload build still assembles the whole catalogue although `ensure(None)` no longer means that.

### AC1 - the pins, and where each came from

Collected 2026-09-22 with `huggingface_hub.HfApi(token=False).model_info(repo, files_metadata=True)` - a listing call, no download. Raw output: `pins-raw.json`, `pins-small.json`; the scripts that made them are `collect_pins.py` and `hash_small.py` beside them.

| repo | revision | backends | tier | alias |
|---|---|---|---|---|
| mobiuslabsgmbh/faster-whisper-large-v3-turbo | 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf | cuda, cpu | turbo | large-v3-turbo |
| Systran/faster-whisper-large-v3 | edaa852ec7e145841d8ffdb056a99866b5f0a478 | cuda, cpu | max | large-v3 |
| mlx-community/whisper-large-v3-turbo | a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb | mlx | turbo | large-v3-turbo |
| mlx-community/whisper-large-v3-mlx | 49e6aa286ad60c14352c404340ded53710378a11 | mlx | max | large-v3 |
| pyannote/speaker-diarization-community-1 | 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee | every | every | (loaded by the diarize stage) |

Provenance per file, kept honest the way `models.json` already distinguished them:

* **The Hub's own LFS sha256** (never computed here): `model.bin` of both CT2 repos, `weights.npz` of the MLX large-v3, `weights.safetensors` of the MLX turbo.
* **Hashed by hand from this machine's hub cache, read-only** (2026-09-22): `config.json`, `preprocessor_config.json`, `tokenizer.json`, `vocabulary.json` of both CT2 repos. The Hub API reported the same byte count for every one of them at the pinned revision, which is what says the cache copy is that revision's copy.
* **Hashed by hand from a 269-byte GET** of `mlx-community/whisper-large-v3-mlx/resolve/49e6aa28.../config.json` (2026-09-22).
* **Unchanged, older**: both files of `mlx-community/whisper-large-v3-turbo` and all five of pyannote. Cross-check: the method above reproduces the MLX turbo pin exactly - revision a4aaeec0, `weights.safetensors` 951ed3fc..., 1613977612 bytes - which is the evidence that the new entries were built the way the old ones were.

Licences read from the API's cardData: `mit` for all four transcription repos.

**The id faster-whisper requests** was established from the installed library, not from a blog post: `.venv/Lib/site-packages/faster_whisper/utils.py:29-30` maps both `large-v3-turbo` and `turbo` to `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, and `large-v3` to `Systran/faster-whisper-large-v3`. Same repo, same revision, in this machine's hub cache. `tests/test_models.py::test_the_catalogue_pins_the_ids_the_loaders_actually_request` asks `faster_whisper.utils._MODELS` and `mlx_backend.repo_for` live, so a library upgrade that re-points a name is a red test rather than a silent second download. The file set is faster-whisper's own `allow_patterns` (`utils.py:91-97`).

**The diff of `python -m scribe.models` on Windows** (`diff-models.txt`, fenced):

```
-[MISSING] mlx-community/whisper-large-v3-turbo            1.6 GB  mit
+[   have] mobiuslabsgmbh/faster-whisper-large-v3-turbo    1.6 GB  mit
+[   have] Systran/faster-whisper-large-v3                 3.1 GB  mit (not loaded at the turbo quality setting)
+[      -] mlx-community/whisper-large-v3-turbo            1.6 GB  mit (not loaded on this platform)
+[      -] mlx-community/whisper-large-v3-mlx              3.1 GB  mit (not loaded on this platform)
 [   have] pyannote/speaker-diarization-community-1         33 MB  cc-by-4.0
-1.6 GB to download
+Everything is here.
```

Why each line moved: the MLX turbo row stopped being MISSING because nothing here loads it - it is marked `-` and says so; the two CT2 rows are new pins and read `have` because they are already in this machine's hub cache at the pinned revisions; the Systran row is pinned but belongs to the other tier; and the total went to zero because `missing()` now walks what this platform and tier want.

**The doctor, before and after** (`before-doctor-models.txt`, `after-doctor-models.txt`), the headline of the task:

```
- detail  : 1.6 GB still to download: whisper-large-v3-turbo (1.6 GB)
- fix_hint: Run `python -m scribe.models --fetch`.
+ detail  : 2 model(s) present
+ fix_hint:
```

`doctor.py:725`'s `split('/')[-1]` was kept deliberately: with the rows filtered, what is left is a repository this machine loads, so the short name is honest. The comment there says why.

### AC2 - status, present and ensure agree

Red first. `ensure()` passed `base = where or root()` into `present()`, so `hub_snapshot` was never consulted and it re-downloaded what the status line called present.

```
tests/test_models.py::test_a_copy_the_hub_cache_holds_is_not_downloaded_again
>       assert models.ensure() == []
E       AssertionError: assert ['demo/model'] == []
```

Fixed by passing `where` (possibly None) through. `--dest` keeps meaning that folder only - `test_a_named_destination_still_means_that_folder_only` pins it.

The machine half: `models.missing()` returns `[]` on Robert's machine (see `after-doctor-models.txt`), over the same predicate `--fetch` walks. The literal `python -m scribe.models --fetch` run is forbidden in this build; the orchestrator or Robert runs it to show it downloads nothing.

### AC3 - gated first

Red first, with the recorded request list empty being the point:

```
tests/test_models.py::test_the_gated_entry_refuses_before_any_public_byte_is_requested
>       assert seen == [], "and not after the public weights had already been fetched"
E       assert ['config.yaml', 'weights.bin'] == []
```

`ensure()` now sorts the walk gated-first and does every free refusal - no token, no room - before the first byte.

### AC4 - a short read is the network, not tampering

Red first, against a loopback `http.server` of my own that announces Content-Length and then closes short (`tests/test_models.py::hub_server`, never a real host):

```
>       assert exc.value.reason == "offline", str(exc.value)
E       AssertionError: demo/model/weights.bin does not match its pin at revision aaaa...; it was deleted rather than used
E       assert 'mismatch' == 'offline'
```

`fetch_file` now counts what arrived against Content-Length, keeps the `.part` and re-requests with `Range: bytes=N-`, up to `RETRIES = 3`. The server records the Range headers; the green assertion is `[None, "bytes=64-"]`. A server that ignores Range and answers 200 makes the write truncate rather than append, so a resumed file is never the right length out of the wrong bytes.

### AC5 - room before the first byte

`models.room_for()` runs `shutil.disk_usage` on the volume the files land on, summed over only the entries about to be fetched, and refuses with both numbers and no request. `errno.ENOSPC` during the write is a fourth `ModelError` reason, `disk`, ahead of the generic OSError branch. `ModelError`'s docstring now names four reasons; `models.EXIT_CODES` is the one exit-code table and `scribe/setup.py` reads it instead of keeping a copy (`disk` is 4).

### AC6 - the loaders read the copy that was downloaded

`transcribe.load_model` and `MlxWhisperModel` ask `models.local_dir(name, backend)` and use the folder when every pinned file is there; otherwise they behave exactly as before. `WhisperModel` taking a directory is verified in the installed library (`faster_whisper/transcribe.py:678`, `os.path.isdir` short-circuits `download_model`).

Both ADR-004 contracts stay green and were run by name:
`tests/test_stage_transcribe.py::test_the_default_model_name_is_spelled_once_in_the_package` (the grep over `scribe/` for .py/.html/.js/.css - which is why every alias and tier lives in `models.json`, a .json the grep does not scan) and `::test_turbo_cannot_translate_so_large_v3_is_substituted` plus the four `resolve_model` cases around it. `6 passed, 63 deselected`.

### AC9 - 401 and 403

Split in `models._one_request` and in `doctor._gated_repo_reachable`. The sentences, printed in the tests:

* 401: `<repo>: this Hugging Face token was not accepted - it is missing, expired or belongs to another account` / `HTTP 401 - this token was not accepted; it is missing, expired or another account's`
* 403: `<repo> is gated: this account has not accepted the conditions at https://hf.co/<repo>` / `HTTP 403 - this account has not accepted the conditions at hf.co/<repo>`

Both keep `credentials.proxy_note()` on the models side, because a proxy also answers 403.

### AC10 - the token goes to the gated repo and stays on its host

Red first, two loopback servers on different netlocs (`127.0.0.1:A` redirecting to `localhost:B`), the header's presence recorded and never its value:

```
tests/test_models.py::test_the_token_is_dropped_when_a_redirect_changes_host
>       assert [row["auth"] for row in elsewhere.log] == [False], "and nowhere else"
E       assert [True] == [False]

tests/test_models.py::test_a_public_repo_is_never_sent_the_token
>       assert [row["auth"] for row in hub.log] == [False]
E       assert [True] == [False]
```

Green: the Authorization header is built only when `model.gated`, and `models.DropAuthAcrossHosts` (a `HTTPRedirectHandler`) pops it whenever `urlsplit(newurl).netloc` differs from the old one. The first server still records `True`, the second `False`.

### AC11 - the tier

The catalogue carries a `tier` per entry, so no model name entered a .py. `models.wanted_here(backend=, tier=)` is what `missing()` and `ensure()` default to; `setup.plan()` passes the stored `default_tier` row into the offer and `setup.apply()` passes `answers.tier or` the stored row into the fetch. `setup.needed()['to_download']` was summing every absent row, MLX included, on Windows; it now sums the `wanted` ones. That is one line outside the listed criteria and is called out here rather than left silent.

### AC12 - the plan's downloads block

`setup.downloads()` reads the backend and the row filter from `models` and `UNPINNED_HERE` is gone. Diff of `python -m scribe.setup --plan`'s downloads block (`diff-plan-downloads.txt`, fenced):

```
-  "note": "the Whisper weights this platform loads are not pinned yet: ... (TASK-089.16)",
+  "note": "",
+  mobiuslabsgmbh/faster-whisper-large-v3-turbo  here true   bytes 1621665983  loads_here true
+  Systran/faster-whisper-large-v3               here true   bytes null        "not loaded at the chosen quality setting"
   mlx-community/whisper-large-v3-turbo          here false  bytes null        "not loaded on this platform"
+  mlx-community/whisper-large-v3-mlx            here false  bytes null        "not loaded on this platform"
```

`tests/test_setup_plan.py:636` moved with the line it pinned.

### Tests

New or moved: `tests/test_models.py` (38 passed, 21 of them red before the change), `tests/test_doctor.py` (56), `tests/test_setup_plan.py` (106), `tests/test_stage_transcribe.py` (69), `tests/test_stage_transcribe_mlx.py` (12). Every other test file that imports a changed module was run one file per process and passed: test_disk_floor 9, test_dotenv_commands 9, test_feed_backfill 13, test_feed_follow 13, test_glossary 69, test_ingest_urls 112, test_ingest_watching 84, test_params_door 26, test_proxy 28, test_setup 15, test_stage_transcribe_second_opinion 17, test_stage_transcribe_windows 28, test_web_recorder 24, test_web_settings 54, test_web_transcribe_dialog 54, test_web_url_dialog 90, test_launcher 53 + 1 skipped, test_credentials 35. Output per file in `green-<file>.txt`.

`test_web_settings.py::test_the_web_process_never_imports_a_model_runtime` passes, so `models.py` importing `scribe.accel` keeps the web process torch-free (ADR-001). `accel` imports only `importlib.util`, `platform` and `sys`; `cuda_available()` imports torch lazily and `backend_here()` never reaches it off Apple Silicon.

### Mutation proof (a copy outside the repository, one change per run)

Each mutation asserted the old line was present before replacing it. `grep -rn MUTANT scribe tests packaging` in the repository finds nothing.

| mutation | file | what failed |
|---|---|---|
| count every catalogue row again | doctor.py | test_a_windows_machine_is_never_asked_to_download_apple_weights, test_weights_the_hub_cache_already_holds_are_present (2 failed, 54 passed) |
| hand faster-whisper the name, as before | stages/transcribe.py | test_the_weights_setup_downloaded_are_the_weights_that_load (1 failed, 68 passed) |
| always the default tier | setup.py | test_choosing_the_largest_model_offers_the_largest_model (1 failed, 105 passed) |
| send the token to every repo | models.py | test_a_public_repo_is_never_sent_the_token (1 failed, 37 passed) |

### AC7 - not run; it needs the RTX 3080 and WSL, so it is Robert's

The code makes it possible: `ensure()` writes into `MODELS_DIR/<org>--<name>` and `load_model` opens that folder. The exact commands, Windows first. `HF_HOME` **and** `SCRIBE_DATA_DIR` are both fresh, because with an empty hub cache the offline run only works if the pinned folder is the one that loads, and `paths.MODELS_DIR` is computed at import - so the fetch and the transcription must be separate processes.

```
cd D:/Users/Robert/Documents/GitHub/RvdB/MyScribe
export HF_HOME=/c/temp/ac7-hf SCRIBE_DATA_DIR='C:\temp\ac7-data'
.venv/Scripts/python -m scribe.models --fetch
HF_HUB_OFFLINE=1 .venv/Scripts/python -c "
import time
from scribe.stages import transcribe
started = time.time()
model, device, compute = transcribe.load_model(transcribe.DEFAULT_MODEL)
segments, info = model.transcribe('tests/fixtures/clip30.wav', word_timestamps=True)
words = sum(len(s.words or []) for s in segments)
print(device, compute, words, 'words in', round(time.time() - started, 1), 's')
"
```

Expected: the fetch prints a progress line per repository and ends with `downloaded: pyannote/speaker-diarization-community-1, mobiuslabsgmbh/faster-whisper-large-v3-turbo` (the gated one first), about 1.65 GB in total; the second command prints `cuda float16 <n> words in <t> s` with n around 70 for the 30-second clip, and does not touch the network. A `HF_HUB_OFFLINE=1` run that reaches the Hub raises `LocalEntryNotFoundError`, which is the failure this proves is not happening.

WSL is the same two commands with `.venv/bin/python`, `HF_HOME=~/ac7-hf` and `SCRIBE_DATA_DIR=~/ac7-data`; the expected device is `cuda` there too, or `cpu` if the WSL venv has no CUDA torch. Box left unticked.

### AC8 - not run; it needs a Mac nobody here has (brief G9)

Whether `mlx-whisper` loads from a local folder is still verified by nobody. The command for the Mac's owner, to go into TASK-089 criterion 10's bundled list:

```
export HF_HOME=~/ac8-hf SCRIBE_DATA_DIR=~/ac8-data
.venv/bin/python -m scribe.models --fetch
HF_HUB_OFFLINE=1 .venv/bin/python -c "
from scribe.stages import transcribe
model, device, compute = transcribe.load_model(transcribe.DEFAULT_MODEL)
print(device, compute, model.repo)
segments, info = model.transcribe('tests/fixtures/clip30.wav')
print(sum(len(s.words or []) for s in segments), 'words')
"
```

Expected: `mlx float16 /Users/<name>/ac8-data/models/mlx-community--whisper-large-v3-turbo` - a path, not a hub id - followed by a word count. If mlx-whisper refuses a folder it raises there, and the fallback at `mlx_backend.py:83` is what has to change. Reported as **not run**; the MLX half is not called done. Box left unticked.

### Out of scope, found and not fixed

1. `packaging/launcher/myscribe_launcher.py:886` still reads "Download the model weights now (about 1.6 GB; ...)" whatever tier was picked. No criterion names it and confirming a Tk dialog needs a person at the screen.
2. `tests/test_feed_first_episode.py::test_a_poll_below_the_disk_floor_queues_nothing_and_says_so` fails with the mandated fence and passes with a short `SCRIBE_DATA_DIR`. The cause is `scribe/ingest/feeds.py:458`, which cuts the recorded result at 200 characters; the fence path is 159 of them, so "10 GB clear" falls off the end. Pre-existing, environment-dependent, and nothing this task touched - proved by re-running the same test with a short scratch data directory, where it passes.
3. The doctor's `check_models` uses the default tier rather than the library's stored one, because reading that row means `db.connect` - a journal-mode switch, i.e. a write - in a check that sits in WEB_SAFE_CHECKS and in `--read-only` (TASK-089.13, W1). A machine set to the largest model is therefore told about the default one. Named in the function's docstring; worth a small follow-up if it matters.

### One edge closed after the notes above

A `.part` left by an earlier run can be longer than the file the pin now names (a changed revision), and `Range: bytes=N-` past the end is answered 416. That surfaced as "the hub answered HTTP 416" at somebody who did nothing wrong. `models._one_request` now throws that leftover away and returns "short", so the retry loop starts the file over. Test: `tests/test_models.py::test_a_part_file_from_an_older_pin_is_thrown_away_and_the_file_starts_over`, which asks the loopback server (it answers 416 for a range past the end, as a real one does) and asserts the recorded ranges are `["bytes=204800-", None]`. tests/test_models.py: 39 passed.

### Four corrections after review, and what they changed

**1. `backend_here()` would have imported torch into the web process on Apple Silicon (ADR-001, Must Not).** The body moved down from `setup.transcriber()` was `accel.transcription_backend() if accel.mlx_available() else NOT_MLX`, and `transcription_backend()` asks CUDA first, which is the torch import. That was harmless while only `setup.downloads()` called it; it stopped being harmless the moment `check_models` - a WEB_SAFE_CHECK - started reading `status()`. No test on this machine could catch it, because `mlx_available()` short-circuits on `is_apple_silicon()`.

It is now `return "mlx" if accel.mlx_available() else NOT_MLX`, which answers the only question the catalogue asks and is identical on every machine that can exist: Apple Silicon has no CUDA, and everywhere else cuda and cpu load the same files. `load_model` still asks the real probe - it is about to load a model anyway. New test, which fails the old body: `tests/test_models.py::test_asking_which_backend_loads_here_never_imports_torch` replaces both `accel.transcription_backend` and `accel.cuda_available` with a function that raises.

**2. The credit file quietly stopped being written on Robert's machine.** `write_credit()` sat inside the download loop, so once a hub-cache copy counted as present (AC2) nothing was downloaded and no `LICENCE-AND-CREDIT.txt` appeared - including pyannote's, which is CC BY 4.0. The existing test could not see it because it uses `where=tmp_path` with nothing cached.

Decided rather than left to chance: the credit goes beside every copy that is in this folder, downloaded here or put there by hand, and a copy that exists only in the huggingface_hub cache gets none - this app placed nothing there, so there is nothing of its doing to attribute, and a licence beside an empty folder would claim there were weights in it. Two tests: `test_the_credit_is_written_beside_a_copy_somebody_put_there_by_hand` and `test_a_copy_only_the_hub_cache_holds_gets_no_credit_file_here`.

**3. `python -m scribe.models --fetch --dest <payload>` had narrowed too.** `packaging/fetch_models.py` was fixed, but the CLI still passed `args.only` (None) and would have assembled a Windows payload without the MLX weights. `--dest` now implies the whole catalogue, which is what that flag's own comment already says it means; without it the question stays "what does this machine load".

**4. A `.part` longer than the file now pinned.** Covered in the note above this one.

Mutation proof of 1 and 2, on the copy: restoring the old backend probe and removing the credit loop failed `test_asking_which_backend_loads_here_never_imports_torch`, `test_the_credit_is_written_beside_a_copy_somebody_put_there_by_hand` and the existing `test_the_credit_lands_beside_the_weights` (3 failed, 39 passed). `grep -rn MUTANT scribe tests packaging` in the repository: clean.

Re-run after these four: test_models 42, test_doctor 56, test_setup_plan 106, test_setup 15, test_stage_transcribe 69, test_stage_transcribe_mlx 12, test_dotenv_commands 9, test_web_settings 54 (which contains `test_the_web_process_never_imports_a_model_runtime`). All passed.

### AC7, corrected commands

The earlier block mixed a git-bash path with a Windows one; `HF_HOME=/c/temp/ac7-hf` is read by Windows Python as `C:\c\temp\ac7-hf`. Both are Windows paths here, and the expected word count is dropped - I have never run this clip, so a number would be an invention Robert would read as a pin.

Windows, from the repository root, in two processes because `paths.MODELS_DIR` is computed at import:

    set HF_HOME=C:\temp\ac7-hf
    set SCRIBE_DATA_DIR=C:\temp\ac7-data
    .venv\Scripts\python -m scribe.models --fetch
    set HF_HUB_OFFLINE=1
    .venv\Scripts\python -c "import time; from scribe.stages import transcribe; t=time.time(); m,d,c=transcribe.load_model(transcribe.DEFAULT_MODEL); s,i=m.transcribe('tests/fixtures/clip30.wav', word_timestamps=True); w=sum(len(x.words or []) for x in s); print(d, c, w, 'words in', round(time.time()-t,1), 's')"

Expected: the fetch prints one progress line per repository, gated first, and ends with `downloaded: pyannote/speaker-diarization-community-1, mobiuslabsgmbh/faster-whisper-large-v3-turbo`, about 1.65 GB in total. The second command prints `cuda float16 <words> <seconds>` for the 30-second clip and reaches no network at all; a `HF_HUB_OFFLINE=1` run that still tries raises `LocalEntryNotFoundError`, which is the failure this proves is not happening. Whatever the word count is, it is the number to record - I have not run it.

WSL is the same two steps with `.venv/bin/python`, `export HF_HOME=~/ac7-hf` and `export SCRIBE_DATA_DIR=~/ac7-data`; expect `cuda` there too, or `cpu` if that venv has no CUDA torch. Box left unticked. The Mac command in the note above has the same correction: both variables are set in the same shell and the fetch and the transcription are separate processes.

## What the review changed (agent, 2026-09-22)

Three verifiers reviewed the implementation. Ten findings were acted on, three
rejected. Evidence in the same directory; every pytest process one test file,
fenced with `SCRIBE_DATA_DIR` and `SCRIBE_ENV_FILE`.

### The one behaviour fault (major)

`plan()` built the download offer from the tier **stored in the database**,
while `default_tier` is answered in the same sitting. A first sitting on a
machine whose turbo weights were already here therefore asked nothing at all
about downloading, and somebody who picked Maximum got the larger model inside
their first transcription with no progress shown - the fault this task exists
to remove, one tier over. Where both tiers needed bytes, the question named the
turbo number while `apply()` fetched the other repository.

Red first (`red-tier-in-sitting.txt`):

```
test_a_tier_answered_in_this_sitting_is_still_offered_its_download
>       assert asked is not None
E       AssertionError: the tier this sitting can still choose has a download
E       assert None is not None

test_the_offer_names_a_number_per_choice_while_the_quality_is_open
E       AssertionError: assert '5 MB' in 'Download the speech weights now (2 MB)?'
```

Fixed in `_questions`: while the quality question is open, question 11 is
offered against every tier that can still be chosen (`setup.TIER_CHOICES`, now
written once because two questions need it). One number while it is the same
number whatever is chosen, one per choice when the choice changes it. What
`plan()["downloads"]` returns is untouched, so AC12's diff still reads. Three
tests, two of them red first.

### The two coverage gaps the mutation verifier found (major)

Both were real: the code was right and nothing held it there.

* The gated-first sort in `ensure()`. The existing test refuses for free with
  no token, which happens before any fetch whatever the order is. The case
  where the order matters is a token the hub **refuses**: that arrives at
  download time, and unsorted the public weights are spent first. New test
  `test_a_token_the_hub_refuses_costs_no_public_download_either`.
* The `206`-vs-`200` guard on resume. New probe mode on the loopback server
  (`ignore_range`): it reads the Range header and answers 200 with the whole
  body, which appended to the `.part` builds a file of the right length out of
  too many bytes. Test
  `test_a_server_that_ignores_the_range_header_does_not_make_one_file_of_two`.
* `needed()['to_download']` now asserts the byte count, not just that there is
  one, and that it is smaller than the whole catalogue (tests/test_setup.py).

### The minors that were real

* **The token could go out in cleartext.** `DropAuthAcrossHosts` compared the
  netloc only, so `https://huggingface.co` to `http://huggingface.co` read as
  the same place and the Bearer header followed. Now `models.origin_of()`
  compares scheme and host together.
* **A `.part` is now named after the revision it was begun for**, and any other
  one beside it is swept up. Named per file alone it outlived a re-pin and the
  next run appended one revision's bytes to another's: the right length, the
  wrong content, rejected with "does not match its pin ... it was deleted
  rather than used" - the tampering sentence AC4 exists to delete. The sweep
  also clears the name this app used before, which is the leftover an upgrade
  really meets.
* **A dropped connection keeps what arrived.** Every `OSError` used to unlink
  the `.part` on the way out, so the resume this function promises worked only
  for a body that ended early without raising. `ENOSPC` still deletes it (the
  `.part` is why there is no room); a reset, a timeout, a 401 or a 500 keeps
  it.
* **The doctor's docstring gave a wrong reason.** It said reading the
  `default_tier` row means a journal-mode switch, which `setup.read_only()`
  disproves in this same repository (`mode=ro&immutable=1`, measured, no `-wal`
  and no `-shm`). The real constraint is that `doctor` may not import `setup`:
  setup pulls `diarize`, `ai_ui` and `transcribe_dialog` into the web process
  (ADR-001), and a second spelling of the immutable open is the duplication
  this task removes. Restated; the limitation itself stands.
* **The plan's `loads_here` key tells the truth again.** It had been made to
  carry platform AND tier, so a CUDA machine at tier max read `loads_here`
  false about `Systran/faster-whisper-large-v3`, the repository it loads. The
  design spec (3.1) defines that key as the platform question, so it keeps that
  meaning and `wanted` is beside it. Both are in `after-plan-downloads.json`
  and `diff-plan-downloads.txt`, regenerated.
* **`room_for` is now held to the volume the files land on**: the test records
  the path `shutil.disk_usage` was asked about.
* **Three deliberate changes that had no test.** `models.main --fetch --dest`,
  a `--fetch` without a destination, and `packaging/fetch_models.ensure` - all
  three now pinned, the payload one being the failure that costs a build.

### Rejected, with the evidence

* *"`present(verify=True)` no longer verifies every byte for a hub-cache
  copy."* The docstring says `ensure` verifies every byte **it writes**, and a
  hub-cache copy is not something ensure wrote. The text is already true.
  `local_dir` asking the cheap question is on purpose: a load failure is
  recoverable and hashing 1.6 GB on every job is not.
* *"`local_dir(name, device)` should split a `cuda:1` device string."* No
  caller passes one, and the degradation is a fall back to today's behaviour.
  A one-line fix with no test only moves the untested line. Reported, not done.
* *"`python -m scribe.models` needs a `--tier`."* A new command-line surface no
  criterion names. The limitation is disclosed below instead, beside the
  doctor's.

### Disclosures

* `python -m scribe.models` (both the listing and `--fetch`) answers for
  `models.DEFAULT_TIER` and never reads the stored `default_tier` row - the
  same limitation already recorded for `doctor.check_models()`, and this is the
  command the doctor's own `fix_hint` names. On a machine set to Maximum the
  listing says everything is here and `--fetch` downloads nothing. A follow-up
  if it matters.
* The "same number whatever is chosen" branch of question 11 is reachable in
  reality (only the gated pipeline missing, both Whisper repositories present)
  and is pinned only through the settled-tier test.

### AC7 and AC8: the precondition the command blocks were missing

A fresh `SCRIBE_DATA_DIR` has no settings row, so `models.default_token()`
finds nothing there. Since AC3 landed, `ensure()` refuses gated-first **before
any byte**, so with no token reachable the run downloads *nothing at all* -
that is AC3 working, not AC7 failing. The token must therefore be reachable
from the environment or from the repository's `.env` (`scribe/env.py` loads the
repository's file, not the data directory's) before step 1 of either block. On
a machine whose token lives only in Settings, step 1 exits 3.

### The mutation round

`mut2-summary.txt` and `mut2-*.txt`: ten mutations, one per process, in a fresh
copy at `.../TASK-089.16/mutate2/` with the script beside it. All ten die,
including the three the verifier recorded as surviving (the gated sort, the 206
guard, the `to_download` filter). One of them, `E-part-name`, survived its first
run and that run found two real faults - the sweep missed the old `.part` name
and the test had planted a leftover only the new scheme produces. Both fixed;
`mut2-E-part-name.txt` is the run after.

The earlier round's fifth mutation, recorded in `mutant-backend-credit.txt` and
missing from `evidence/mutate.py`, reverted `backend_here()` to the
`transcription_backend()` body and moved `write_credit` back inside the
download loop - two changes in one run, which is why it failed three tests.

### Tests after the review, one file per process, fenced

```
final-test_models.txt                49 passed in 16.06s
final-test_setup.txt                 15 passed in  5.15s
final-test_setup_plan.txt           109 passed in 11.28s
final-test_doctor.txt                56 passed in 14.24s
final-test_stage_transcribe.txt      69 passed in 22.82s
final-test_stage_transcribe_mlx.txt  12 passed in  3.50s
final-test_launcher.txt              53 passed, 1 skipped in 21.85s
final-test_web_settings.txt          54 passed in 35.23s
final-adr004.txt                      6 passed, 63 deselected in 3.90s
```

ADR-004 holds: the six contracts around `resolve_model`, the grep over the
package and `test_turbo_cannot_translate_so_large_v3_is_substituted` among
them. `grep -rn MUTANT scribe tests packaging` in the repository is empty.

### Three things the note above owes the next reader

**`CONTRACT` stays 2.** Its own docstring says to raise it whenever a question
is added to `_questions`, and none was: question 11 changed its predicate and
its text, and the `downloads` entries gained a `wanted` key beside the
`loads_here` they already carried. A front-end renders the questions it is
given and reads the keys it knows, so neither is a document an older renderer
would choke on - and launcher and app ship in one payload anyway (ADR-015), so
a renderer that disagreed would be a mismatched install rather than an old
client. The original implementation reshaped the same entries without a bump
for the same reason.

**Thirteen findings, not ten.** Ten acted on with code or a test (A, B, C, D,
E, F, I, J, L, M, N - counting the two `.part` halves as one), three by writing
something down (O the AC7/AC8 precondition, P the fifth mutation of the earlier
round, and the two disclosures). Three rejected with evidence (the `verify`
claim about `ensure`, the `cuda:1` device string, and a `--tier` for the
models CLI). The prose above says "ten and three"; this is the exact count.

**Half of one rejection was accepted.** The finding about `present()` was
rejected for what it said about `ensure` - "verifies every byte **it writes**"
is already true of a copy ensure did not write. But the same function takes
`verify` and returned True from `hub_snapshot()` before consulting it, which
the docstring did not say. That sentence is now there: a hub-cache copy is
accepted on its revision, names and sizes whatever `verify` asks, because this
app did not put it there and huggingface_hub does its own integrity work. No
behaviour changed.

Two more test files were run afterwards, both importing a module this review
touched: `final-test_proxy.txt` 28 passed, `final-test_dotenv_commands.txt`
9 passed. `tests/test_disk_floor.py`, `test_feed_backfill.py`,
`test_feed_follow.py`, `test_feed_first_episode.py` and `test_ingest_urls.py`
import `scribe.doctor`, where only a docstring moved.

## Verification (orchestrator, 2026-09-22)

Run here, fenced, not taken from the build report. Robert asked for this task
by name after seeing the symptom in a real doctor run, so the evidence below
is the same command he saw.

### The symptom is gone

Before, on this machine:

    detail: 1.6 GB still to download: whisper-large-v3-turbo (1.6 GB)
    hint  : Run `python -m scribe.models --fetch`.

After:

    ok    : True
    detail: 2 model(s) present
    hint  : (empty)

`python -m scribe.models` now reads:

    [   have] mobiuslabsgmbh/faster-whisper-large-v3-turbo    1.6 GB  mit
    [   have] Systran/faster-whisper-large-v3                 3.1 GB  mit (not loaded at the turbo quality setting)
    [      -] mlx-community/whisper-large-v3-turbo            1.6 GB  mit (not loaded on this platform)
    [      -] mlx-community/whisper-large-v3-mlx              3.1 GB  mit (not loaded on this platform)
    [   have] pyannote/speaker-diarization-community-1         33 MB  cc-by-4.0

    Everything is here.

Two reasons are now distinguishable in one line - wrong platform and wrong
tier - where before the platform reason was hidden by
`row['repo'].split('/')[-1]`.

### Criterion 2's machine half, which the build was forbidden to run

Checked in the safe order: the listing first, then `missing()`, and only then
the command.

    missing: []
    .venv/Scripts/python -m scribe.models --fetch
    nothing to do; everything was already here
    exit=0
    bytes written into the fenced data dir: 0

### The pins, verified against real bytes rather than against the Hub

The build collected them from `HfApi.model_info(files_metadata=True)`. That is
the same source the file records, so it is not an independent check. I hashed
the files in this machine's hub cache instead, at the pinned revision:

    pyannote/speaker-diarization-community-1  (pin PRE-EXISTING)
      5 of 5 files: sha256 MATCH, size match
    mobiuslabsgmbh/faster-whisper-large-v3-turbo  (pin NEW)
      5 of 5 files: sha256 MATCH, size match  (model.bin is the 1.6 GB one)
    Systran/faster-whisper-large-v3  (pin NEW)
      5 of 5 files: sha256 MATCH, size match  (model.bin is the 3.1 GB one)

The pre-existing pyannote pin coming out right is what validates the method.
Fifteen files, every digest and every size.

**Not verified here, and it should not be claimed:** the two `mlx-community`
pins. Neither repository is in this machine's cache and nothing on Windows can
open them, so they rest on the Hub API alone. They are settled by criterion 8,
on a Mac nobody here has.

### Criterion 12

`--plan`'s downloads block now carries `here`, `loads_here`, `wanted` and
`bytes` per entry, and the `note` that used to read "the Whisper weights this
platform loads are not pinned yet ... (TASK-089.16)" is empty. The entry this
machine loads reports 1,621,665,983 real bytes where it used to report none.

### Where the knowledge ended up

`models.wanted_here` is the one answer, and both doors read it: `check_models`
no longer calls `models.status()` raw, and `setup.py` dropped its own
`loads_here` body. That was the orchestrator's design instruction and it was
followed rather than patched around.

One limitation is disclosed rather than hidden, in `check_models`'s own
docstring: the tier it uses is `models.DEFAULT_TIER` and not the stored
`default_tier` row, because this check renders on a settings page
(`WEB_SAFE_CHECKS`, ADR-001) and importing `scribe.setup` for the immutable
open would pull `diarize`, `ai_ui` and `transcribe_dialog` into the web
process. A machine set to Maximum is therefore told about the default model.
That is a smaller error than the one this task removed, and it is written down.

### Whole suite

One file per process, fenced: **2944 passed, 10 skipped, 0 failed, 0 errors** over 81 files, reconciling exactly with "2954/2964 tests collected (10 deselected)" since 2944 + 10 = 2954. That is +48 on the run after TASK-089.12 (2906 collected): `tests/test_models.py` 42 -> 49, `tests/test_setup_plan.py` 106 -> 109, `tests/test_doctor.py` 53 -> 56, and the rest spread over the transcribe and setup files the loaders touch.

### Criteria 7 and 8 stay open

Criterion 7 needs a fresh `HF_HOME`, a fetch of several gigabytes and then a
transcription with `HF_HUB_OFFLINE=1`, on the RTX 3080 and again in WSL. An
agent must not start that here. Criterion 8 needs an Apple Silicon Mac. Both
recipes are in the notes above, including the token precondition the fix round
added. Until criterion 7 runs, criterion 6's local-folder load is carried by
tests and by reading `faster_whisper/transcribe.py:678-680`, not by a run.

### Carried over, not this task's

`tests/test_feed_first_episode.py::test_a_poll_below_the_disk_floor_queues_nothing_and_says_so`
fails when the fence path is long: `scribe/ingest/feeds.py:458` cuts the
recorded result at 200 characters and a 159-character path eats the number the
assertion looks for. That is TASK-091, already filed. My own fence is
`C:\ms-f`, which is short, so the suite below is unaffected by it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The catalogue now knows which weights this platform and quality setting actually load, and both doors read the same answer. scribe/models.json gains pins for the CT2 turbo repo under the id faster-whisper really requests (mobiuslabsgmbh/faster-whisper-large-v3-turbo), for Systran/faster-whisper-large-v3 and for mlx-community/whisper-large-v3-mlx, each with revision, per-file sha256 and size; models.wanted_here is the one place that answers "would this machine load these", and doctor.check_models and setup.py's plan both read it instead of each carrying their own copy. Robert saw the symptom in a real doctor run and asked for this task by name: the card said "1.6 GB still to download: whisper-large-v3-turbo" two lines above a gpu-smoke that transcribed with weights already in the cache, because the missing entry was the Apple conversion and the shortened name had dropped the mlx-community that said so. It now reads "2 model(s) present" with no hint, and `python -m scribe.models` distinguishes wrong-platform from wrong-tier in words. ensure() was hardened alongside: gated entries first so no token means no public download, free space checked on the landing volume before the first byte, a short read reported as offline instead of as a pin mismatch, .part files resumed with Range (and dropped when a server ignores it, or when they came from an older pin), 401 and 403 given different sentences, and the Authorization header dropped on a redirect that changes scheme or host. Verified by the orchestrator rather than taken on report: the listing and the doctor line on this machine; `--fetch` run in the safe order after missing() answered empty, giving "nothing to do; everything was already here", exit 0, zero bytes written; and the pins hashed against the real files in the hub cache - pyannote 5 of 5, CT2 turbo 5 of 5, Systran 5 of 5, every sha256 and every size, with the pre-existing pyannote pin validating the method. The two mlx-community pins are NOT verified here: neither is in this machine's cache and nothing on Windows opens them, so they rest on the Hub API alone until criterion 8. Whole suite over 81 files: 2944 passed, 10 skipped, 0 failed, reconciling with 2954 collected. Criteria 7 and 8 stay unticked - a fresh HF_HOME, a multi-gigabyte fetch and an HF_HUB_OFFLINE=1 transcription on the RTX 3080 and in WSL, and the same on a Mac nobody here has; until criterion 7 runs, criterion 6's local-folder load is carried by tests and by reading faster_whisper, not by a run. Disclosed and not fixed: check_models and `python -m scribe.models` answer for the default tier and not the stored row, because this check renders on a settings page and importing scribe.setup would pull diarize and ai_ui into the web process (ADR-001).
<!-- SECTION:FINAL_SUMMARY:END -->
