---
id: TASK-102.07
title: SHA256SUMS lists only the artifacts
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:56'
labels:
  - release
dependencies: []
parent_task_id: TASK-102
ordinal: 183000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
release.yml's publish step runs sha256sum * over the artifacts, the .sha256 files included, so a plain shasum -c SHA256SUMS fails on files that were not downloaded alongside. Report section 7.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 SHA256SUMS lists the three artifacts and not their .sha256 files, with a test on the workflow step
- [x] #2 sha256sum -c SHA256SUMS still passes when all three artifacts are present
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: a test runs release.yml's SHA256SUMS step with bash in a temp dir holding an artifact and its .sha256, and expects only the artifact listed. Green: hash every file but the .sha256 ones.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_the_sums_list_the_artifacts_and_not_their_sha256_files listed the .sha256 files. First fix (find ! -name *.sha256) went red on a second bug the old code had too: the redirect creates SHA256SUMS before find, so it hashed itself and sha256sum -c reported 'SHA256SUMS: FAILED'. Now excluded by name. Green: tests/test_release_sums.py + test_release_kind.py 13 passed. The tests run the step's own script read from release.yml with bash. docs/RELEASING.md's manual publish line changed to the same command.

CI on macos-latest (run 37180919925) failed test_the_sums_list_the_artifacts_and_not_their_sha256_files with [] - BSD find has no -printf, so the listing was empty there. The publish step itself runs on ubuntu, but the test runs the step's script on every runner, which is how it surfaced. Now ls | grep -v -e '\.sha256$' -e '^SHA256SUMS$' | xargs sha256sum; 13 passed locally, checked in WSL bash (a.dmg, b.exe listed).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
SHA256SUMS lists only the artifacts: release.yml's publish step hashes every file but the .sha256 ones and SHA256SUMS itself. Tested by running the step's own script from the workflow (red, then green); RELEASING.md matches.
<!-- SECTION:FINAL_SUMMARY:END -->
