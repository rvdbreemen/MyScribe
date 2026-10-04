---
id: TASK-102.06
title: The install guide says where the model weights live
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:30'
labels:
  - docs
dependencies: []
parent_task_id: TASK-102
ordinal: 182000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The guide says the home holds the speech engine and its caches, but the weights go to the Hugging Face cache (~/.cache/huggingface) by design; scribe/models.py accepts that copy. A home on another drive therefore does not hold 1.5 GB of weights. Report section 2.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The guide states where the weights are kept, how HF_HOME / HF_HUB_CACHE moves them, and checked against scribe/models.py and scribe/doctor.py
- [x] #2 The behaviour is not changed in this task
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
docs/installation.md now says the speech models are the one large thing that can live outside the home: setup's downloads go to data/models (paths.MODELS_DIR; setup.py calls models.ensure), a model a loader fetches itself goes to the Hugging Face cache (doctor.hf_cache_dir's precedence: HF_HUB_CACHE, HF_HOME/hub, ~/.cache/huggingface/hub), either copy counts (models.hub_snapshot; tests/test_models.py lines 230 and 659) and HF_HOME moves it. Behaviour unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
