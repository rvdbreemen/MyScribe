---
id: TASK-070
title: >-
  Nothing in SQLite enforces one runner at a time; a second claimer starts a
  concurrent GPU child
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 16:39'
updated_date: '2026-09-16 16:45'
labels:
  - review-2026-09-16
  - concurrency
  - supervisor
dependencies: []
priority: medium
type: bug
ordinal: 115000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass, severity medium after correction). jobs.claim_next flips the next queued job to running under BEGIN IMMEDIATE, which makes each claim exclusive - but nothing makes the claim conditional on no other job running. ADR-001 promises 'the supervisor spawns at most one GPU runner at a time' and delivers it per supervisor only, by running its loop sequentially. Two routes break it without any bug elsewhere: (a) a second app instance against the same data directory (python -m scribe --port 4299 without --no-supervisor; the launcher and the start scripts refuse a second instance on the same port only) claims and spawns while the first runs; (b) Supervisor.stop() leaves a running child alone, the next startup's reconcile keeps that row because its pid is alive, and the new supervisor then claims the next queued job beside the orphan. Route (b) got more likely with TASK-069: the runner's own process group no longer receives the console's Ctrl-C, so stopping the app in a terminal leaves the runner alive. Two runners load Whisper and pyannote on one 16 GB card at once.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 claim_next returns None while any job is running, atomically in the same statement, and claims again once that job has left running
- [x] #2 A supervisor whose claim finds a running row left by a previous life flips it to interrupted when its pid is dead and goes on to claim, and leaves it alone - claiming nothing - while the pid is alive and old enough to be that job's runner
- [x] #3 The existing claim-race test still proves exactly one claimer wins a row, restated for one-at-a-time
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_jobs.py - claim_next returns None while a row is running, the race test restated as one winner per round; test_supervisor.py - a dead orphan row from a previous life is flipped by the loop and the queue moves, a live orphan holds the queue until it ends.
2. Green: claim_next's UPDATE takes 'AND NOT EXISTS (SELECT 1 FROM job WHERE status='running')' inside the same BEGIN IMMEDIATE statement (atomic across processes, ADR-009); Supervisor._loop calls reconcile() when a claim returns None, so a running row whose pid is dead does not hold the queue until the next restart.
3. Run test_jobs, test_queue_order, test_supervisor, test_app, test_web_jobs and the other files that call claim_next, one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_claim_next_refuses_while_a_job_is_running and the restated race test failed (a second claim succeeded beside a running row); test_a_dead_orphan_from_a_previous_life_does_not_hold_the_queue and test_a_live_orphan_from_a_previous_life_holds_the_queue_until_it_ends failed (the queue moved beside a live orphan; the dead orphan stayed running). Green after the NOT EXISTS predicate in claim_next and reconcile() on an empty claim: test_jobs 25, test_queue_order 13, test_supervisor 14, test_app 17, test_web_jobs 42, test_runner 13, test_stage_probe 25, test_stage_prepare 27, test_stage_transcribe 66, test_pipeline_e2e 54, test_web_settings 37, test_ingest_urls 86, test_web_url_dialog 90, test_llm_tasks 120, test_llm_chat 27, test_llm_selftest 14 - every file that calls claim_next, run one at a time. One existing test moved: test_the_board_refuses_to_move_a_running_job claimed a second job beside the board fixture's running one and now uses that running job. ADR-009's quoted claim SQL (lines 206-214) predates queue_seq and this predicate; that drift is the open ADR-009 low finding.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
claim_next's UPDATE carries AND NOT EXISTS (SELECT 1 FROM job WHERE status='running') inside the same BEGIN IMMEDIATE statement, so no claimer - a second instance or a new life beside an orphan - can start a runner while one runs; Supervisor._loop reconciles whenever a claim comes back empty, so a row whose runner died no longer holds the queue until a restart. Verified red-to-green by four new tests and 670 tests across the sixteen files that touch claim_next.
<!-- SECTION:FINAL_SUMMARY:END -->
