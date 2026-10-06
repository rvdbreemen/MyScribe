---
id: TASK-107
title: >-
  The second outside macOS walk (0.8.3): watch folder, Gatekeeper on macOS 26,
  the second window, silent downloads
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 06:13'
labels:
  - macos
  - installer
dependencies: []
priority: high
ordinal: 201000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Hermes (Jim's agent) walked 0.8.3 on macOS 26.0 on 2026-10-04/05 (mail of 2026-10-05, REPORT-2026-10-05-macos-0.8.3-acceptance.md). All five questions passed; these were found on the way.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PR #9 team review (security, performance, architecture; 2026-10-06): 10 findings, all fixed on the branch before merge, each its own commit - 1 High: one writer per model folder (OS lock in models.ensure, re-check after; red: two threads inside the fetch at once). Medium: 2 hub cache at another revision used, not fetched again (models.hub_any_revision); 3 CHOICE_WAITING set inside the on_tk work (race too narrow for the harness; real Tk run shows it); 4 gpu tests exempt from the fetch no-op (collected, not run); 5 a stage that fetched weights files no stage_perf (red then green). Low: 6 download fills the first 30% of the bar; 7 doctor gpu-smoke prints the fetch and names ModelError reasons (red then green); 8 setup.shown + parity test with the launcher; 9 models.for_alias; 10 browser/choice code moved near Launch. Touched files green: launcher 109+2 skipped, sitting 72, setup_plan+setup 198, transcribe 74, mlx 12, windows 28, second opinion 17, models 50, doctor 58, runner 14, build_release 6, e2e 54.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All five findings of the second outside macOS walk (0.8.3) handled: watch folders take new recordings only, audio by default (.01, released in 0.8.4); Gatekeeper steps for macOS 15+ (.02); the waiting choice said in the main window (.03); missing weights fetched with progress, not silently (.04, the doctor's default-tier limit kept and documented); browser address always said, console honours shown_if, proof needs 4242 free (.05).
<!-- SECTION:FINAL_SUMMARY:END -->
