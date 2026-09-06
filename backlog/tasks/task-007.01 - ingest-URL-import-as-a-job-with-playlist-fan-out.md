---
id: TASK-007.01
title: 'ingest: URL import as a job with playlist fan-out'
status: Done
assignee: []
created_date: '2026-09-02 16:34'
updated_date: '2026-09-03 07:36'
labels: []
dependencies: []
parent_task_id: TASK-007
ordinal: 44000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6 Task 1. urls.probe/download via yt-dlp (bestaudio, no re-encode, info-json kept), progress hooks mapped to 0..1, hotword_terms from metadata; ingest_url job type with fetch+register stages, playlist fans out one job per entry; error codes DOWNLOAD_FAILED / UNSUPPORTED_URL / UNAVAILABLE; Windows cookies note. Plan: docs/superpowers/plans/2026-09-02-phase6-ingest.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A playlist enqueues one job per entry instead of one download
- [x] #2 DownloadError becomes DOWNLOAD_FAILED with the reason preserved
- [x] #3 A malicious title never reaches a path
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_ingest_urls.py: 39 passed in 6.42s. AC1 by test_a_playlist_fans_out_one_job_per_entry_and_downloads_nothing and test_probe_reads_a_playlist_as_its_entries, with test_a_fanned_out_job_refuses_to_fan_out_again pinning that a child job cannot fan out recursively. AC2 by test_a_download_error_becomes_download_failed_and_keeps_the_reason, which keeps yt-dlp's own text ('HTTP Error 403') rather than replacing it with a generic message. AC3 by test_a_traversing_title_cannot_escape_the_work_dir and test_a_title_that_scrubs_to_nothing_still_gets_a_name - a title is scrubbed to a name, and a title that scrubs away entirely still lands somewhere rather than at the work dir itself. Separately verified today: ensure_http_url (urls.py:328) rejects file://, javascript:, data://, uppercase FILE://, whitespace-padded variants, UNC paths, bare Windows paths and bare search strings - the last matters because yt-dlp turns a bare string into a web search. Fourteen hostile inputs refused, three http/https accepted, none wrongly allowed; test_a_non_http_url_never_reaches_yt_dlp turns red when the scheme check is disabled in a scratch copy.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
URL import as a job with playlist fan-out and honest failures (commit c5305da). Verified by tests/test_ingest_urls.py (39 passed): a playlist enqueues one job per entry and downloads nothing itself, a yt-dlp DownloadError becomes DOWNLOAD_FAILED with its reason intact, and a traversing title cannot escape the work directory. The scheme guard was verified by attempting the bypass rather than by reading it.
<!-- SECTION:FINAL_SUMMARY:END -->
