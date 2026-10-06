---
id: TASK-108
title: >-
  A supported automation surface: JSON API, then a myscribe CLI, then an MCP
  server
status: To Do
assignee: []
created_date: '2026-10-06 09:30'
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
- [ ] #1 Robert has decided the Proposed ADR for phase 0
<!-- AC:END -->
