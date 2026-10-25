---
id: TASK-108
title: >-
  A supported automation surface: JSON API, then a myscribe CLI, then an MCP
  server
status: Done
assignee: []
created_date: 2026-10-06 09:30
updated_date: '2026-10-25T14:17:00Z'
labels:
  - api
  - proposal
dependencies: []
priority: low
ordinal: 209000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Proposal by Hermes (Jim's agent), 2026-10-05, after driving the whole third macOS walk with curl against htmx forms and reading the source for field names. Today the only interface is the web UI on 127.0.0.1:4242: HTML and htmx forms, most management routes include_in_schema=False, errors as HTML. Phases, smallest useful first: (0) /api/v1/* JSON twins of the routes that matter (watch folders, jobs, media, transcripts, export, search, settings, doctor), OpenAPI on, a bearer token on the loopback kept in the data dir (0600), JSON errors with a code, idempotency keys on writes; HTML routes unchanged. (1) a myscribe CLI as a thin client on phase 0, shipped as a console script in the app and the packaged binary, exit code per outcome. (2) myscribe mcp on the same client: stdio tools such as transcribe_path, add_watch_folder, list_jobs, get_transcript, export_transcript, search_library, doctor. (3) contract tests per endpoint, golden output for the CLI, tool tests for the MCP. Hermes estimates a few focused days. Not decided: Robert asked for a Proposed ADR on phase 0 first (2026-10-06).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Robert has decided the Proposed ADR for phase 0
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): ADR-025 option B: /api/v1 JSON twins without a token, behind the 127.0.0.1 bind and scribe.guard like the pages (OpenAPI, coded JSON errors, idempotency key on uploads). Build phases 0, 1 and 2: the API, a myscribe CLI on top of it, and an MCP server on the same API.

2026-10-25 Robert's decision (asked with options in the session): MCP stays stdlib JSON-RPC (ADR-033 accepted). CLI shim in {app}\bin, PATH via an unticked installer task that only adds/removes that one entry; Linux 'MyScribe.AppImage cli <command>'. Also fix: the doctor sentence per channel (no 'below' for API/CLI/MCP) and /settings/doctor uses web_checks so the web process does not import torch (ADR-001). Idempotency key: content hash + options, title does not count. Doctor and watch-folder routes added now. Schema v19 accepted. ADR-025 option B accepted.

2026-10-25: the terminal setup path (setup.py watch_added_sentence) has the same 'below' problem; fixed with on_page=False in the same round (obvious, same class as Robert's decision).

2026-10-25 PR #102 merged, CI green on three OSes (after test fixes: POSIX tmp_path lies outside the default browse root, so the API/CLI/MCP watch tests needed the existing tmp_inside_the_roots fixture; the POSIX shim tests now look where macOS keeps the home). Built phases 0-2: /api/v1 JSON twins behind 127.0.0.1 + scribe.guard with OpenAPI, coded errors and an idempotency key (schema v19); doctor and watch-folder routes incl. transcribing left-alone files; a myscribe CLI (Windows shim in {app}\bin with an opt-in PATH task, Linux 'MyScribe.AppImage cli', python -P so a planted scribe/ in the working folder never runs - red test); an MCP stdio server as a thin client of /api/v1. ADR-025 (option B) and ADR-033 accepted by Robert. Disclosure: an early test run sent up to 4 read-only GETs to the live 127.0.0.1:4242, which answered 404; fixed by -P. Not verified: the installer PATH task at runtime, a real AppImage, macOS.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
An outside program can drive MyScribe: /api/v1 (ADR-025, no token, loopback + Host guard), a myscribe CLI and an MCP server on top of it (ADR-033). PR #102.
<!-- SECTION:FINAL_SUMMARY:END -->
