---
id: TASK-102.06
title: The install guide says where the model weights live
status: To Do
assignee: []
created_date: '2026-10-04 05:27'
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
- [ ] #1 The guide states where the weights are kept, how HF_HOME / HF_HUB_CACHE moves them, and checked against scribe/models.py and scribe/doctor.py
- [ ] #2 The behaviour is not changed in this task
<!-- AC:END -->
