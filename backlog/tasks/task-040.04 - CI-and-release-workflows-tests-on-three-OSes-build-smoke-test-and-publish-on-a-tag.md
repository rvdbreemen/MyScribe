---
id: TASK-040.04
title: >-
  CI and release workflows: tests on three OSes, build, smoke-test and publish
  on a tag
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-26 18:23'
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
- [x] #2 release.yml on a v* tag checks the tag against scribe.__version__, builds the three artifacts, smoke-tests each installed artifact on its OS, and publishes a release with SHA256SUMS and attestations
- [x] #3 workflow_dispatch runs the same build and smoke jobs and uploads workflow artifacts without publishing a release
- [x] #4 Signing steps for macOS and Windows run only when their secrets are configured and are skipped with a notice otherwise
- [x] #5 One real run of the workflow on GitHub is green, with its run URL in the task notes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC5: run 35430468828 is green on ubuntu, macos and windows. That run also proved the workflows themselves - uv installed from its own script on all three, uv lock --check, uv sync --frozen, ffmpeg per platform, the doctor before the suite, and the suite in halves on Windows.

Three things the workflows caught that no local run would have: an ubuntu runner arrives with about 5 GB free against the doctor 10 GB floor; ffmpeg 6.1.1 reports a clip position the Mac reports differently; and a cold macOS runner needs longer than 30 s to bring the app up.

AC4 proven in the v0.5.0 release run: macos printed "MACOS_CERTIFICATE is not configured; shipping the ad-hoc signed app" and windows "WINDOWS_CERTIFICATE is not configured; the installer is unsigned". Neither failed the build, which is the point - an unsigned artifact is a real thing a user can run past Gatekeeper.

AC2 is met in every part but one, and the exception is not fixable here: attestations. actions/attest-build-provenance answered "Feature not available for user-owned private repositories" and failed the publish over three good builds. It is now conditional, the way signing already was, and SHA256SUMS is published either way. The criterion as written cannot be satisfied while this repository is a user-owned private one.

AC3 bewezen door run 35447454324 (event: workflow_dispatch): de jobs 'the tag is the version', windows-x64, macos-arm64 en linux-x64 zijn alle vier groen, elk inclusief 'Build the artifact and prove it starts'; de job 'publish' is overgeslagen. De run uploadde precies drie workflow-artifacts (myscribe-windows-x64, myscribe-macos-arm64, myscribe-linux-x64) en er is geen release aangemaakt. Dat is exact wat dit criterium vraagt: dezelfde build- en smoke-jobs, artifacts naar de run, geen publicatie.

2026-09-24 (orchestrator). #2 still waits on attestations, which GitHub refuses for a user-owned private repository. Everything else in it was re-proven on 2026-09-23 without a tag: tag-vs-version job, three builds, three smokes (run 35913685730), and a red probe (run 35923728188). If the repository goes public, the next tag's publish step shows whether attestations then work.

2026-09-26: criterion 2 met on the public repository: release run 36256827527 checked the tag against scribe.__version__, built and smoke-tested all three artifacts and published them with SHA256SUMS and attestations; a public repository gets the attestation GitHub refused for the private one.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
CI tests on three operating systems and the release workflow builds, smoke-tests and publishes on a tag with SHA256SUMS and attestations, proven by v0.6.0 on the public repository.
<!-- SECTION:FINAL_SUMMARY:END -->
