---
id: TASK-089.08
title: A job keeps its transcript when the speaker weights cannot load
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 01:35'
labels:
  - pipeline
  - speakers
  - bug
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 145000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
diarize defaults to True (scribe/options.py:42). WeightsUnavailable is raised at scribe/stages/diarize.py:392 and caught by nothing: a grep over scribe/ finds its definition, its docstring and that one raise. diarize is the fifth of eight stages, after transcribe, and finalize runs last (scribe/stages/__init__.py:29-38). A missing or refused token therefore costs the whole transcription, after it has run.

What a user hits: they skip the token, which is allowed and must stay allowed. They drop in a one-hour recording and wait for all of it. They get a failed job with 'No speaker diarization weights could be loaded' and no transcript. scribe/doctor.py:455-459 records this incident from 2026-09-18: 'the first transcribe job found that out at the diarize stage, an hour of audio later.'

The docs promise the opposite. .env.example:5-8 says the stage 'falls back to speaker-diarization-3.1 assembled from its two public checkpoints and says so on the run', and README.md:241 has the row 'Diarization falls back and says so'. A reader's curl on 2026-09-20 got 401 without a token for pyannote/segmentation-3.0, one of those two checkpoints. That request was not re-run when this task was written.

This task alone carries the behaviour that makes 'every question can be skipped' safe. The earlier plan added a guard in setup that switched the 'Recognise speakers' default off and recorded that it had. That guard is dropped, and setup never writes default_diarize by itself (brief: W6). The one other path by which it writes the row today is the typed `--diarize/--no-diarize` flag (scribe/setup.py:164-165 at d80360a, written at :125-135; TASK-089.03 shortens the file, so these lines are re-pointed after it lands). Robert decided on 2026-09-20 that the flag stays, as an explicit choice and nothing else (brief: G3): somebody who types `--no-diarize` is choosing, not guarding. TASK-089.09 criterion 17 keeps it as the one writer setup has, and fixes the tier-only write.

The reason. default_diarize is not a preference with an owner; it is 'the last options submitted'. save_defaults writes language, tier and diarize together (scribe/web/transcribe_dialog.py:97-108) and is called from seven sites, on every dialog, upload or URL submit: scribe/web/ingest_ui.py:375, :438, :509 and :888; transcribe_dialog.py:247 and :280; scribe/web/settings.py:492. After the first upload a marker saying 'setup switched this off' no longer says who set the row, and restoring it when a token is saved could flip a choice the user made on purpose. Watch folders carry their own options (settings.py:27-31) and never read the row; neither do feeds, a URL, the recorder or a retry. An install-time guard could not cover them anyway. This is the orchestrator's choice, stated with its reason so that Robert can overturn it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first, in a scratch data directory with an empty Hugging Face cache, no token, diarize on and one short clip: the job ends 'failed' today. After the change it ends 'done', with a transcript and no speakers. Both outputs are shown.
- [x] #2 The run carries a note in words - speakers skipped, and the three routes to fix it - and the media page shows it with a link to the Hugging Face token field under Settings > Transcription. There is no section called 'Defaults': the nav label is 'Transcription' (scribe/web/settings.py:436) and the panel's heading is 'Defaults for the next transcription' (scribe/templates/_settings_defaults.html:23). An event is emitted; nothing is silent.
- [x] #3 Any other exception in the diarize stage still fails the job, and a test covers it.
- [x] #4 The same holds for a job that did not come through the dialog: one test enqueues through a watch folder's own options.
- [x] #5 `.venv/Scripts/python -m pytest tests/test_exports_text.py -q`: the goldens are unchanged, or the diff is shown and explained.
- [x] #6 README.md:241 and .env.example:5-8 no longer claim a fallback that works without a token. Whether pyannote/segmentation-3.0 is gated is checked with one request whose output is shown, not taken from the reader's note.
- [x] #7 Nothing in this task writes default_diarize, and setup never writes it by itself: there is no guard. The typed `--diarize/--no-diarize` flag stays, as an explicit choice and the one writer setup keeps - decided by Robert on 2026-09-20 (brief: G3). TASK-089.09 criterion 17 carries it, with the fix for the tier-only write.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Facts re-checked on 2026-09-22: WeightsUnavailable is defined at scribe/stages/diarize.py:127 and raised at :389 (task says :126/:392); hf_token (:259-273) now delegates to credentials.resolve(HUGGINGFACE); diarize defaults True at scribe/options.py:42; the stage list is scribe/stages/__init__.py:29-38. diarization_note is written by _note_run (:654-669) and rendered by NOTHING today.
2. Red first: add stage tests to tests/test_stage_diarize.py - load_pipeline_with_source raising WeightsUnavailable leaves the job 'done' with turns [], a run note and an event - and run them against unmodified code. Keep that failing output as the red evidence.
3. The change in diarize.run: catch WeightsUnavailable and only that. Set ctx.state['turns'] = [] (the same state the 'diarize: false' path already produces, which attribute and finalize are covered for), write the note with _note_run(run_id, '', note), emit a diarize event with structured keys (skipped, reason, token present yes/no, n_turns=0 - prose would be cut at 80 chars by jobs_ui._short), log a warn line like proxy.py does, report 1.0, return. Any other exception still leaves the stage and fails the job (criterion 3).
4. The note's words come from one builder that takes whether a token resolved: no token at all -> 'no Hugging Face token was found'; a token present -> 'the token from <source> does not open the model; its conditions are not accepted'. That is the 401/403 distinction as far as the stage can honestly know it - no HTTP call is added in a failure path. Resolve once through credentials.resolve so the source can be named; never name a local 'params' for a literal read in this module (tests/test_params_door.py:459 scans the stage modules' AST).
5. Both sentences end with the three routes: the token field under Settings > Transcription, HF_TOKEN in the environment or .env, and a pipeline directory at MODELS_DIR/pyannote.
6. The media page: transcript.page_context parses the current run's params_json and passes diarization_note; _transcript_panel.html renders it as a <p class="hint" data-diarization-note> beside the data-cleanup-status precedent, with a link to /settings?section=defaults#hf-token. Add id="hf-token" to the token form in _settings_defaults.html - criterion 2 asks for the field, and the existing #hf-hint is the paragraph above it. Rider to state in the evidence: this also puts the existing FALLBACK_NOTE on screen for the first time.
7. Tests to add: the media page shows the note and the link (tests/test_web_transcript.py); a ValueError from the stage still fails the job (tests/test_stage_diarize.py); a job enqueued through a watch folder's own options keeps its transcript when the weights cannot load (criterion 4, tests/test_ingest_watching.py, using watching.add_folder/options_of and the real runner).
8. Docs (criterion 6): README.md:241 and .env.example:5-8 stop promising a fallback that works without a token; they say the assembled 3.1 fallback needs a token too and that a job without one keeps its transcript and says so. Evidence: one unauthenticated HEAD on https://huggingface.co/pyannote/segmentation-3.0/resolve/main/config.yaml, output kept, no Authorization header and no token in that process.
9. Criterion 1's real run: a scratch SCRIBE_DATA_DIR and a fresh HF_HOME, one short clip, tier tiny on CPU, job status before and after the change. Probe first that the process really has no token (credentials.find_all, booleans and source names only - the registry and the Hugging Face login file are outside the test fence). If it cannot be made tokenless without touching the registry, or the run is killed for memory, hand that half to the orchestrator and say so rather than passing the mocked test off as it.
10. pytest: one file per process, output to a file, SCRIBE_DATA_DIR/SCRIBE_ENV_FILE fence exported. Files: test_stage_diarize.py, test_web_transcript.py, test_ingest_watching.py, test_params_door.py, test_exports_text.py (criterion 5) and test_exports_rich.py (tests/golden/exports/seed.json carries run params, so it is the other file a run note could move).
11. Criterion 7 is a negative: proven with grep for default_diarize over the diff and scribe/, not with a new test. Nothing here writes that row.
12. Out of scope, reported and not fixed: scribe/doctor.py:555 answers 401 and 403 with the same sentence ('the conditions are not accepted for this token'), which design 3.8 gives to TASK-089.12 and 3.7 gives to TASK-089.16 for scribe/models.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation, 2026-09-22 (builder)

Evidence directory: C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/96fe3055-12a8-4348-a17a-5699a082adf4/scratchpad/build/TASK-089.08/

### What changed
* `scribe/stages/diarize.py` - `run()` resolves the token through `credentials.resolve` (the source is needed for the note, which `hf_token`'s bare value cannot give) and wraps the `diarize()` call in `try/except WeightsUnavailable`. `_skip_speakers()` keeps the transcript: `ctx.state['turns'] = []` (the state `"diarize": false` already leaves), a note on the run, a warn line in app.log, a `diarize` event with structured keys, `report(1.0)`, return. `skipped_note()` builds the two sentences. `_note_run()` no longer writes an empty `diarization_pipeline`: nothing loaded, so the key is absent rather than `""` (it would read as "diarized with the pipeline called ''" in the JSON export).
* `scribe/web/transcript.py` - `diarization_note(run)` parses the current run's `params_json`; `page_context` passes it.
* `scribe/templates/_transcript_panel.html` - renders it as `<p class="hint" data-diarization-note>` beside the `data-cleanup-status` precedent, with `<a href="/settings?section=defaults#hf-token">Settings &gt; Transcription</a>`.
* `scribe/templates/_settings_defaults.html` - `id="hf-token"` on the token form (the label comes into view with the field; `#hf-hint` is the paragraph above it).
* `README.md:241` and `.env.example:4-8` - the fallback no longer promises to work without a token.

### Rider, stated rather than slipped in
Rendering `diarization_note` generically puts the existing `FALLBACK_NOTE` (the assembled 3.1 substitution) on screen for the first time. It has been written to `run.params_json` since that fallback shipped and was read by no template and no route. Intended, and a real behaviour change riding along with this task.

### Red first
* `red-stage-diarize.txt` - 5 failed, 69 passed. Every failure is `scribe.stages.diarize.WeightsUnavailable` escaping `diarize.run` at diarize.py:615.
* `red-web-transcript.txt` - 2 failed, 99 passed (no note paragraph, no link).
* `red-ingest-watching.txt` - 1 failed, 83 passed: `runner._run` returned 1 where the test wants 0, through the real runner.
* `red-web-settings.txt` - 1 failed, 46 passed (`id="hf-token"` absent).

### Green (one file per process, fence exported, output kept)
test_stage_diarize 74 passed / test_web_transcript 101 / test_ingest_watching 84 / test_web_settings 47 / test_exports_text 74 / test_exports_rich 45 / test_params_door 26 / test_credentials 35 / test_doctor 23 / test_glossary 69 / test_models 15 / test_setup 9 / test_web_jobs 43 / test_web_recorder 24 / test_web_transcribe_dialog 54 / test_web_url_dialog 90 / test_library_row_meta 24 / test_env 43+8 skipped / test_dotenv_commands 9 / test_launcher 27+1 skipped / test_pipeline_e2e 54. All green; no golden moved.

### Mutation proof for the three tests that were green before the change
Copy at .../mut/, mutated there (`mutate.py` asserts the old text first): `except WeightsUnavailable` widened to `except Exception`, and `{% if diarization_note %}` made unconditional. Exactly three tests failed and nothing else - `test_any_other_failure_in_the_stage_still_fails_the_job`, `test_a_watched_folders_job_still_fails_on_anything_else`, `test_a_run_that_has_its_speakers_says_nothing_about_missing_ones` (mutant-*.txt). `grep -rn MUTANT scribe tests` in the repository: nothing.

### Criterion 6, measured not quoted
`head-probe.txt`, 2026-09-22, urllib HEAD with no Authorization header (printed as such): `pyannote/segmentation-3.0` HTTP 401, `pyannote/speaker-diarization-community-1` HTTP 401, `pyannote/wespeaker-voxceleb-resnet34-LM` HTTP 200. So the assembled 3.1 fallback is gated too - one of its two checkpoints is - and a machine with no token has no route at all.

### Criterion 1's real run: handed to the orchestrator
`token-probe.txt`: with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE fenced, HF_HOME redirected to an empty directory and HF_TOKEN/HUGGINGFACE_TOKEN/HUGGING_FACE_HUB_TOKEN unset, `credentials.find_all` still answers `huggingface: found=True source='HF_TOKEN (Windows registry, HKEY_LOCAL_MACHINE)'`. Making this process tokenless would mean changing the Windows registry, which is outside this task. The mocked tests are not offered as that run. What is held instead: `runner._run` over the real runner returns 1 (failed) before the change and 0 (done, transcript committed, note on the run) after - tests/test_ingest_watching.py, with the four model/ffmpeg stages stubbed and diarize, attribute and finalize real.

### What a person actually reads (render-probe.txt)
Both notes in full, and the jobs board line through `jobs_ui.event_summary`: `run_id=42, skipped=true, reason=weights-unavailable, token_found=false, token_source=, n_turns=0`. Nothing is cut at 80 characters.

### Honest limit on the 401/403 distinction
The stage never sees a status code: `Pipeline.from_pretrained` returns None for a missing, a private and a gated repository alike. What it distinguishes is "no token resolved at all" from "a token resolved and opened nothing", and the second sentence says "most often the model's conditions have not been accepted for it" rather than asserting 403 as fact.

### Out of scope, found and not done
* `scribe/doctor.py:556` still answers 401 and 403 with one sentence. Not touched (design 3.8 -> TASK-089.12, 3.7 -> TASK-089.16 for scribe/models.py).
* The opt-out path (`"diarize": false`, diarize.py:600) emits `skipped=True` with no `reason`. The two are now told apart by `reason` being present or absent; giving the opt-out its own reason would change an existing event shape and is not in these criteria.

### Criterion 7, the negative
`default_diarize` appears in the diff only twice, both in prose saying nothing writes it. `scribe/setup.py` is not in the diff; the typed `--diarize/--no-diarize` flag is untouched.

### Addendum after review (same session)
* The app.log line now carries `detail=str(exc).splitlines()[0]` - the fixed sentence only. The per-source hints under it are whatever pyannote and huggingface_hub said, which can embed a request URL, and `detail` is not a name applog redacts. They remain on the run note and on the job row.
* Two tests added to tests/test_web_transcript.py (103 passed): the existing `FALLBACK_NOTE` renders sensibly too - that is the rider, now pinned rather than assumed - and the htmx panel on its own carries the note, which is what a rail action re-fetches. `panel-probe.txt` holds both rendered paragraphs verbatim.
* Mutation proof re-run on a fresh copy of the shipped code: same three guards fail, nothing else, no MUTANT in the repository.
* Wording observation for the orchestrator, not changed: the note prose says "save the token under Settings > Transcription" and the link right after it repeats that phrase. The plan fixed the link text; the repo's precedent (`_ai_region.html:73`) puts a verb in it ("Choose a provider in Settings > AI providers"). A one-word change if you want it.

## After review, 2026-09-22 (fixer)

Fourteen findings from three verifiers. What the review changed, what it only
proved, and what it hands on.

### Changed in the code
* `diarize.py` - the comment over the app.log line claimed the per-route
  "Tried:" lines survive "on the run note and on the job row". Neither carries
  them: `skipped_note` never sees the exception and the event has no detail
  key. The comment now says plainly that they are dropped here, and names what
  a person gets instead - the note on the run, and `python -m scribe.doctor`,
  which asks the Hub with the token and reports the status code. A false
  comment justifying a deliberate loss was the worse half of that finding.
* `detail=str(exc).split("\n", 1)[0]` instead of `splitlines()[0]`: an
  exception with no text raised IndexError *inside* the rescue, turning it back
  into the failed job this task exists to remove. No route raises an empty one
  today, which is exactly why nothing would have caught it. Pinned by
  `test_a_failure_with_no_message_at_all_is_still_a_kept_transcript`.
* `skipped_note` names `MODELS_DIR/pyannote`, not the resolved absolute path.
  The note goes into `run.params_json`, which `exports/jsonw._params` ships
  verbatim, so a shared transcript carried the directory layout of the machine
  that made it. The doctor keeps the resolved path - that is a report about one
  machine and never leaves it.
* Two guards that no test held now have one: the app.log truncation
  (`test_only_the_first_line_of_the_failure_reaches_the_application_log`) and
  `token_source` on the event (one assertion added to the test that already
  sets up a resolvable token).
* README.md:241 said "There is no route without a token", which contradicts the
  app's own third route. It now reads "no route that downloads weights without
  a token", and the Fix column names the local pipeline directory - so the
  table, the note on the page and the doctor offer the same three routes.
* The link text is the action, not the place: "Open the token field". The prose
  keeps "Settings > Transcription", because the same sentence is stored on the
  run and exported as JSON where there is no link to follow. That also stops
  the link dangling after the substitution note, which names no place at all.
* The `ctx.state["turns"] = []` comment no longer implies the next stage needs
  it - `attribute` reads `ctx.state.get("turns")`, and a run with the line
  removed still ends done. It says what is true: it keeps the two skip paths
  identical.

### Proven rather than changed (copy at .../mut-fix, repository untouched)
* Deleting the paragraph from the template, guard left alone, fails all four
  note tests - including the two added after the review pass, which until now
  were covered by inference only: `fix-mut-f5-paragraph-deleted.txt`, 4 failed
  / 100 passed.
* Replacing the `except (TypeError, ValueError)` in `transcript.diarization_note`
  takes the media page down with a JSONDecodeError:
  `fix-mut-f6-note-parse-unguarded.txt`, 1 failed / 103 passed.
* `fix-mut-f1..f4` pin the four code changes above, each failing exactly the one
  test that names it and nothing else.

### Accepted out loud
* The rider stands: rendering `diarization_note` generically puts the
  pre-existing `FALLBACK_NOTE` on the media page for the first time. It is
  arguably the bug criterion 2 describes - "says so on the run" said it only to
  the database - and it is pinned by
  `test_the_substitution_note_reaches_the_page_too`.
* In the record rather than only in a deviation list: the builder ran one bare
  `python` for a text edit of tests/test_web_settings.py, against the rule that
  only `.venv/Scripts/python` is used here. No residue, and that file is green
  at 47 passed.

### Criterion 1's real run - still the orchestrator's, with a sharper reason
Two things block it from this side. The "ends failed today" half needs the
pre-change tree, which needs a checkout or a stash, and no git write is allowed
here. And `cache-probe.txt` (read-only, names only): this machine's hub cache
already holds `models--pyannote--segmentation-3.0`, `--speaker-diarization-3.1`
and `--speaker-diarization-community-1`, so a run against the default cache
would diarize successfully - the criterion's empty cache is load-bearing, not
decoration. The same cache holds `models--Systran--faster-whisper-tiny`, so the
cheapest honest route is a scratch `HF_HOME` seeded with that one snapshot plus
a deliberately invalid `HF_TOKEN` exported. That proves "a token was found and
it opened nothing" and not "no token at all": `credentials._sources` returns at
the first place a name is found, and an empty environment value falls through
to the registry, so a literally tokenless run still means editing HKLM.

### Final runs (fence exported, one test file per process)
test_stage_diarize 76 passed, 2 deselected / test_web_transcript 104 /
test_ingest_watching 84 / test_web_settings 47 / test_params_door 26 /
test_exports_text 74 / test_exports_rich 45 / test_pipeline_e2e 54, 1
deselected / test_doctor 23. No golden moved.
`grep -rn MUTANT scribe tests`: nothing.

Criterion 1's real-run half, run by the orchestrator on 2026-09-22. It was the one blocker the review left, and it is the half no mocked stage can answer.

Two trees, identical but for scribe/stages/diarize.py - "before" carries HEAD's file (aa7e44f), "after" the change - given the same clip, the same scratch data directory, the same empty pyannote cache and the same deliberately invalid token:

    before   runner exit 1   job status failed   76 words   no note    44.0 s
    after    runner exit 0   job status done     76 words   the note   27.0 s

Everything in it is real: ffprobe, ffmpeg, faster-whisper on the tiny model, and runner.main walking all eight stages. Only the token is arranged, and only so the weights genuinely cannot be fetched. An invalid token was chosen over no token at all because this machine keeps a real one in the HKLM registry, which no environment fence reaches; an invalid one forces a real 401 from Hugging Face, which is the harder path. pyannote printed its own refusal in both runs ("Could not download Pipeline ... visit https://hf.co/... to accept user conditions").

The note a user now gets: "Speakers were not worked out: the Hugging Face token from HF_TOKEN opened none of the diarization pipelines - most often the model's conditions have not been accepted for it. The transcript is complete; only the speakers [...]" - it names where the token came from and tells the two cases apart.

What the mocked tests could not have shown: in the before run those 76 words were already in the database when diarize failed. The data was there and the job was failed, which is what a person sees. This change is not about whether the words exist but about whether the job admits it.

The script and its output are kept beside the other installer evidence as real_run_diarize.py and real_run_diarize.output-2026-09-22.txt.

Also verified by me: the suite, one file per process, fenced at a short path - 2702 passed, 0 failed, 10 skipped over 79 files, against 2685 before this task. The live library carries the same modification time before and after.

Worth recording about the run that produced this: the first fix pass died on an API error after writing its code changes but before verifying them, and the resumed pass said so plainly rather than presenting that work as its own.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A job that asked for speakers keeps its transcript when the weights will not load.

WeightsUnavailable was raised in the fifth of eight stages and caught by nothing, so an hour of audio ended as a failed job. Measured on a real run: before the change the runner exits 1 and the job reads failed; after, it exits 0 and reads done. Both runs put the same 76 words in the database - the difference is not whether the words exist but whether the job admits it, which is what a person sees.

The stage now catches that one exception and nothing else, keeps the transcript with no speakers, and says what happened in three places: a note on the run, a line on the media page, and a job event with structured keys, because the jobs board truncates prose at 80 characters. The note distinguishes no token at all from a token that opened nothing, and names where the token came from.

One thing found on the way that nobody had asked about: diarization_note has been written to run.params_json since the fallback shipped and was rendered by nothing - no template and no route read it. The README's promise that diarization "falls back and says so" was only ever half true. This puts it on a screen for the first time.

The README and .env.example stop promising a fallback that works without a token, and that was measured rather than quoted: an unauthenticated HEAD on segmentation-3.0 answers 401, so the assembled 3.1 route is gated too.

Nothing here writes default_diarize. That is the point of W6 - the honest fallback lives in the stage, not in a setting somebody flips.

Verified: red first through the real runner, mutations on copies, the golden export files unmoved, and the suite fenced at 2702 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
