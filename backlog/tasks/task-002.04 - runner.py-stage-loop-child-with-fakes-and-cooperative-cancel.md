---
id: TASK-002.04
title: 'runner.py: stage-loop child with fakes and cooperative cancel'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-02 02:27'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 4. runner.main(argv) exit codes 0/1/2, STAGES registry per job type, RunnerContext(conn, job, params, report, cancelled), report() throttled to 0.4s, cancel checked between stages, exception to error_code map plus traceback event. stages_fake.py scenarios ok/boom/slow.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Scenario ok ends done with ordered stages and stage_perf rows
- [x] #2 Scenario boom ends failed with error_code RUNTIME and traceback event
- [x] #3 Scenario slow with cancel flag ends cancelled, exit code 2
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
runner.py stage loop with throttled progress reporting, cooperative cancel between stages, exception-to-error_code mapping; stages_fake.py ok/boom/slow scenarios. Verified: all three scenarios reach the right terminal status and exit code. Commit f215c0f.
<!-- SECTION:FINAL_SUMMARY:END -->
