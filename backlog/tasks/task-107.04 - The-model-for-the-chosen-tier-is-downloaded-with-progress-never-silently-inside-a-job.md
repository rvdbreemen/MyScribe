---
id: TASK-107.04
title: >-
  The model for the chosen tier is downloaded with progress, never silently
  inside a job
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 21:44'
labels:
  - installer
  - models
dependencies: []
parent_task_id: TASK-107
ordinal: 205000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Tier max needs mlx-community/whisper-large-v3-mlx (2.9 GB); with the sitting's weights question left open it arrived inside the first job with no progress and no log line (the TASK-089.16 class, one tier over).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A job that must fetch weights reports it with progress and a log line, or the sitting fetches the chosen tier's model; tested
- [ ] #2 The doctor names the missing model for the chosen tier
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Where: load_model, the seam every test already replaces, so no test or CI run fetches real weights. fetch_missing_weights(name, backend, on_weights): the catalogue model with that alias that loads on this backend; nothing when it is present (data/models or the hub cache) or unknown; otherwise models.ensure([repo]) with on_progress forwarded. The stage passes a callback that turns bytes into the job's progress, emits one 'weights' event and one applog line when a fetch starts. Red first: unit tests on the helper, load_model calling it before constructing a model, and the stage's callback. The doctor names the chosen tier's missing model: checked against its existing models check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: no fetch_missing_weights; the stage passed no on_weights. Green: load_model calls fetch_missing_weights(name, device, on_weights) before building a model - the catalogue entry with that alias that loads on this backend, fetched with models.ensure when models.present says it is not here (library or hub cache), nothing for an unknown name. The stage's _WeightsReport turns bytes into job progress and emits one 'weights' event and one applog line 'transcribe.weights' per repo. Found on the way: tests/test_stage_transcribe_mlx.py::test_load_model_builds_the_mlx_backend_on_a_mac calls the real load_model and now reached the Hugging Face Hub (stack: fetch_file <- ensure <- fetch_missing_weights); tests/conftest.py now makes the fetch a no-op in every test except those marked real_weights_fetch (pytest.ini), which fake models.ensure themselves. test_a_folder_that_is_not_whole_is_not_used changes on purpose: it now also asserts the fetch was asked (ensure is a recorder). Green: test_stage_transcribe 73, _mlx 12, _second_opinion 17, _windows 28, test_doctor 56, test_models 49, test_pipeline_e2e 54. AC2 not met, on purpose: doctor.check_models deliberately uses the default tier, not the chosen one, because it renders in the web process and may not import scribe.setup (its docstring records the trade-off). With the job now showing the fetch, that remains a known limit rather than a hang.
<!-- SECTION:NOTES:END -->
