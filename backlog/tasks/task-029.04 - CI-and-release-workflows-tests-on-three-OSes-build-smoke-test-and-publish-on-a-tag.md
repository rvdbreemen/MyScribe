---
id: TASK-029.04
title: >-
  CI and release workflows: tests on three OSes, build, smoke-test and publish
  on a tag
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-11 21:18'
labels:
  - ci
  - release
dependencies:
  - TASK-029.03
parent_task_id: TASK-029
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The repo has no CI at all. The user wants to trust the automation to produce the release artifacts, which means every artifact is built from a tag, installed and exercised on its own OS before it is published, and carries checksums and provenance. Actions minutes are billed on this private repo (macOS counts 10x), so the three-OS matrix runs on pull requests and tags, not every push.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ci.yml runs uv lock --check and the suite on windows, macos and ubuntu for pull requests
- [ ] #2 release.yml on a v* tag checks the tag against scribe.__version__, builds the three artifacts, smoke-tests each installed artifact on its OS, and publishes a release with SHA256SUMS and attestations
- [ ] #3 workflow_dispatch runs the same build and smoke jobs and uploads workflow artifacts without publishing a release
- [ ] #4 Signing steps for macOS and Windows run only when their secrets are configured and are skipped with a notice otherwise
- [ ] #5 One real run of the workflow on GitHub is green, with its run URL in the task notes
<!-- AC:END -->
