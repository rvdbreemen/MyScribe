---
id: TASK-040.04
title: >-
  CI and release workflows: tests on three OSes, build, smoke-test and publish
  on a tag
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-19 08:56'
labels:
  - ci
  - release
dependencies:
  - TASK-029.03
parent_task_id: TASK-040
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The repo has no CI at all. The user wants to trust the automation to produce the release artifacts, which means every artifact is built from a tag, installed and exercised on its own OS before it is published, and carries checksums and provenance. Actions minutes are billed on this private repo (macOS counts 10x), so the three-OS matrix runs on pull requests and tags, not every push.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ci.yml runs uv lock --check and the suite on windows, macos and ubuntu for pull requests
- [ ] #2 release.yml on a v* tag checks the tag against scribe.__version__, builds the three artifacts, smoke-tests each installed artifact on its OS, and publishes a release with SHA256SUMS and attestations
- [ ] #3 workflow_dispatch runs the same build and smoke jobs and uploads workflow artifacts without publishing a release
- [ ] #4 Signing steps for macOS and Windows run only when their secrets are configured and are skipped with a notice otherwise
- [x] #5 One real run of the workflow on GitHub is green, with its run URL in the task notes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC5: run 35430468828 is green on ubuntu, macos and windows. That run also proved the workflows themselves - uv installed from its own script on all three, uv lock --check, uv sync --frozen, ffmpeg per platform, the doctor before the suite, and the suite in halves on Windows.

Three things the workflows caught that no local run would have: an ubuntu runner arrives with about 5 GB free against the doctor 10 GB floor; ffmpeg 6.1.1 reports a clip position the Mac reports differently; and a cold macOS runner needs longer than 30 s to bring the app up.
<!-- SECTION:NOTES:END -->
