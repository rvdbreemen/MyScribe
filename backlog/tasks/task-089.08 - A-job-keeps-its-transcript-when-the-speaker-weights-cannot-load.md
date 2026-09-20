---
id: TASK-089.08
title: A job keeps its transcript when the speaker weights cannot load
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-20 21:48'
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
- [ ] #1 Red first, in a scratch data directory with an empty Hugging Face cache, no token, diarize on and one short clip: the job ends 'failed' today. After the change it ends 'done', with a transcript and no speakers. Both outputs are shown.
- [ ] #2 The run carries a note in words - speakers skipped, and the three routes to fix it - and the media page shows it with a link to the Hugging Face token field under Settings > Transcription. There is no section called 'Defaults': the nav label is 'Transcription' (scribe/web/settings.py:436) and the panel's heading is 'Defaults for the next transcription' (scribe/templates/_settings_defaults.html:23). An event is emitted; nothing is silent.
- [ ] #3 Any other exception in the diarize stage still fails the job, and a test covers it.
- [ ] #4 The same holds for a job that did not come through the dialog: one test enqueues through a watch folder's own options.
- [ ] #5 `.venv/Scripts/python -m pytest tests/test_exports_text.py -q`: the goldens are unchanged, or the diff is shown and explained.
- [ ] #6 README.md:241 and .env.example:5-8 no longer claim a fallback that works without a token. Whether pyannote/segmentation-3.0 is gated is checked with one request whose output is shown, not taken from the reader's note.
- [ ] #7 Nothing in this task writes default_diarize, and setup never writes it by itself: there is no guard. The typed `--diarize/--no-diarize` flag stays, as an explicit choice and the one writer setup keeps - decided by Robert on 2026-09-20 (brief: G3). TASK-089.09 criterion 17 carries it, with the fix for the tier-only write.
<!-- AC:END -->
