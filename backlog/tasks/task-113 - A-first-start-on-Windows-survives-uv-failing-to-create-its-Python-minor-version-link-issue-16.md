---
id: TASK-113
title: >-
  A first start on Windows survives uv failing to create its Python minor
  version link (issue 16)
status: Done
assignee: []
created_date: 2026-10-19 18:14
updated_date: '2026-10-25T07:56:00Z'
labels:
  - packaging
  - windows
  - bug
dependencies: []
references:
  - https://github.com/rvdbreemen/MyScribe/issues/16
  - https://github.com/astral-sh/uv/issues/19616
  - >-
    docs/adr/ADR-027-myscribe-keeps-uv-managed-python-and-retries-a-failed-minor-version-link-instead-of-shipping-its-own-python.md
priority: high
ordinal: 222000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
GitHub issue 16 (2026-10-16): a default install on Windows stops at the first start with `error: Failed to create Python minor version link directory`, caused by `os error 448` (the path cannot be traversed because it contains an untrusted mount point). The bundled uv 0.12.13 always makes a junction `cpython-3.12-windows-x86_64-none` after downloading Python (crates/uv-python/src/installation.rs:368, managed.rs:796, uv-fs replace_with_junction) and has no switch to skip it. The same upstream error is astral-sh/uv#19616, still open. The cause on the reporters machine is not known; 448 is not reproduced here. A scratch simulation with the same uv and the same flags gives the same top-level error (os error 267 instead of 448), leaves a complete Python on disk, and a second sync then succeeds. Decision context in ADR-027.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-22: GitHub #16 closed as fixed in 0.9.0 (the retry of TASK-113.01 and the messages of TASK-113.02 ship there). TASK-113.03 (install Python without uv's link, ADR-027 option B) stays To Do: it is picked up if the reporter reopens #16 because 0.9.0 still fails.

2026-10-24 Robert's decision (asked with options and consequences in the session): close as solved: the cause of GitHub #16 / os error 448 was Inno Setup's RedirectionGuard, fixed in 0.9.1 (TASK-116, RedirectionGuard=no); ADR-027 gets a dated note that the retry stays as a cheap guard for other link failures.

2026-10-25: all subtasks Done (113.01 retry, 113.02 honest messages, 113.03 closed as not needed). The cause of GitHub #16 is fixed in 0.9.1 (TASK-116); ADR-027 note in PR #94 (merged); #16 comment posted asking the reporter to confirm.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A first Windows start no longer fails with os error 448: the cause was the installer's RedirectionGuard, fixed in 0.9.1 (TASK-116). The minor-version-link retry and honest messages (0.9.0) stay as guards. ADR-027 records the real cause; the reporter on #16 has been asked to confirm.
<!-- SECTION:FINAL_SUMMARY:END -->
