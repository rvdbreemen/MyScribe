---
id: TASK-034
title: >-
  A params object at the ingest door is not sieved, so a local client can make
  pyannote open any pipeline
status: In Progress
assignee: []
created_date: '2026-09-11 06:03'
updated_date: '2026-09-11 07:54'
labels:
  - security
  - bug
dependencies: []
ordinal: 75000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-11 by the TASK-028 reachability workflow. POST /api/media takes transcribe options as fields (validated by options.TranscribeOptions) or as a params object, and _ingest_options (scribe/app.py:88-109) passes a params object into the job unfiltered. options.PARAM_KEYS already names every key a transcribe job may carry, but is not applied there. The diarize stage reads params["diarization_model"] (scribe/stages/diarize.py:613) and hands it to pyannote Pipeline.from_pretrained (diarize.py:304), trying MODELS_DIR first; pyannote then resolves the pipeline class named in that config (get_class_by_name) and loads checkpoints with weights_only=False. So whoever can post to 127.0.0.1:4242 chooses code the runner executes. The browser route is closed by scribe/guard.py (TrustedHost for loopback names, SameOriginPosts on Sec-Fetch-Site/Origin); what remains is any local process, including another Windows account, since loopback is shared. POST /api/jobs/{id}/retry (app.py:315-334) replays a stored job's params as they are.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A params object carrying a key outside options.PARAM_KEYS is refused with a 400 that names the key, at every door that accepts one (POST /api/media upload, local path and URL forms)
- [ ] #2 diarization_model cannot be set through any HTTP door, and the diarize stage still opens its default pipeline
- [ ] #3 Retry of a transcribe job carries only PARAM_KEYS forward, so a job stored before the fix cannot be replayed with a foreign key
- [ ] #4 Every legitimate producer (dialog fields, URL import with extra_hotwords, feeds, bulk retranscribe, retry) still works: tests show it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Find every door that accepts client job params. 2. Red tests per door, retry, stage and producers. 3. Sieve a params object to options.PARAM_KEYS at _ingest_options (400 naming the key); retry sieves transcribe params and an ingest_url job's nested options through options.replayable (PARAM_KEYS, and a model that is not a speech model dropped); the diarize stage stops reading diarization_model. 4. Adversarial review (bypass, producer regressions), fix its three minor findings red/green. 5. Suite halves, real runs on port 4299 with a scratch data dir.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built in an isolated worktree (branch fix/params-door), cherry-picked onto feat/feed-episode-import as 889bc22 and a28295f.
Doors (every route that takes client-supplied job params, found by the implementer): only _ingest_options (app.py) takes a raw params object - it serves both forms of POST /api/media (multipart upload, JSON local path); retry_job replays stored params; every other door builds params from validated fields (parse_options().to_params(): /transcribe/upload, /transcribe/path, /transcribe/url and ticked episodes, /record/{session}/finish, watch folders) or server-side (bulk retranscribe via _last_params, feed polls, url_stage, llm jobs).
Decisions: the diarize stage no longer reads diarization_model (nothing in scribe/ wrote it; only a GPU test did; a job stored before the fix is never re-validated, so only the stage change covers it; choosing a pipeline stays possible by placing it at MODELS_DIR/pyannote, which needs write access to the data dir). PARAM_KEYS gains device and compute_type, which the stages read and the CPU e2e test posts; torch.device and CTranslate2 reject an unknown device before opening anything. Consequence: bulk retranscribe now carries device/compute_type forward (own test). Retry sieves type transcribe, and an ingest_url job's nested options; llm and other jobs replay verbatim.
Red on the unfixed code: 11 failed (diarization_model/feed_id/prompt accepted with 201 at both /api/media forms; retry carried diarization_model and feed_id; the stage opened attacker/pipeline; the stage-reads guard). Follow-ups red: 4 failed (retry kept openai/gpt-5.6-luna for transcribe and ingest_url options; ingest_url options kept diarization_model and feed_id; the guard scan could not see extra_hotwords - a mutation removing it from PARAM_KEYS passed the old guard and fails the new one).
Green: tests/test_params_door.py 26 passed; worktree halves 1162 + 811; after cherry-pick on the feature branch test_params_door + test_app + test_web_transcribe_dialog 96 passed. Real runs (python -m scribe --port 4299, scratch SCRIBE_DATA_DIR, curl as the local client): diarization_model in params -> 400 naming the key and the admitted keys, JSON and multipart; clean params -> 201; retry of poisoned transcribe/ingest_url rows -> sieved; a clean retry byte-identical. Reviews: no bypass found; three minor findings fixed (040ad9e/a28295f). Not yet run: the gpu-marked end-to-end test, edited to post params={}.
<!-- SECTION:NOTES:END -->
