---
id: TASK-004.01
title: 'Web scaffold: templates, vendored htmx, .env loading, seed helpers'
status: Done
assignee: []
created_date: '2026-09-02 07:27'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 1. web.mount(app) with StaticFiles and Jinja2 (autoescape), base.html with nav, app.css/app.js, htmx.min.js copied from WHYcast (MIT), scribe.env.load_dotenv (no dependency, never overrides set keys), tests/seed.py helpers. Plan: docs/superpowers/plans/2026-09-02-phase3-webui.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 GET / renders the nav and loads /static/htmx.min.js
- [x] #2 load_dotenv sets missing keys only and ignores comments and quotes
- [x] #3 seed_run round-trips words, segments and labels through the schema
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Scaffold: web.mount with StaticFiles and autoescaping Jinja2, base.html with nav/flash/main, vendored htmx 2.0.10 byte-identical to WHYcast's, scribe/env.load_dotenv (setdefault semantics), tests/seed.py. Verified by an independent agent incl. a real server run on :4299. Commit cd780c8.
<!-- SECTION:FINAL_SUMMARY:END -->
