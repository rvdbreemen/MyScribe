---
id: TASK-113
title: >-
  A first start on Windows survives uv failing to create its Python minor
  version link (issue 16)
status: To Do
assignee: []
created_date: 2026-10-19 18:14
updated_date: '2026-10-22T01:40:00Z'
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
<!-- SECTION:NOTES:END -->
