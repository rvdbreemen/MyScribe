---
id: TASK-002.05
title: 'supervisor.py: claim loop, spawn, watch, kill grace, reconcile'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-02 02:27'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 5. Supervisor daemon thread claims and spawns python -m scribe.runner children (CREATE_NO_WINDOW), stores pid, enforces 10s cancel grace then terminate/kill, safety-net verdict RUNNER_DIED, reconcile() flips orphaned running rows to interrupted. Windows pid_alive via ctypes OpenProcess. runner_cmd override for scripted fake runner in tests.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Fake runner script: queued to running to done observed
- [x] #2 Runner exiting 1 without verdict yields failed RUNNER_DIED
- [x] #3 Cancel kills a sleeping child within grace+2s, job cancelled
- [x] #4 reconcile() marks running row with dead pid as interrupted
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
supervisor.py claim-spawn-watch loop with 10s cancel grace then terminate/kill, RUNNER_DIED safety net, startup reconcile; Windows pid liveness via ctypes OpenProcess. Verified with scripted fake runners. Commit 8d19131.
<!-- SECTION:FINAL_SUMMARY:END -->
