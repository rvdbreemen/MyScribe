---
id: TASK-007.02
title: 'web: URL import dialog with a preview'
status: Done
assignee: []
created_date: '2026-09-02 16:34'
updated_date: '2026-09-03 09:15'
labels: []
dependencies: []
parent_task_id: TASK-007
ordinal: 45000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6 Task 2. POST /transcribe/url validating scheme, GET /transcribe/url/preview probing in a threadpool with a 10s timeout showing title/uploader/duration or entry count, cookies-file field, URL tab in the transcribe dialog.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 file: and javascript: URLs refused with 400
- [x] #2 Preview shows a probed title and playlist entry count
- [x] #3 A probe timeout renders a readable message, not a traceback
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_web_url_dialog.py, run with the recording suite: 52 passed in 13.74s (commit a1c3618). AC1 by test_a_url_that_is_not_http_is_refused_before_yt_dlp_sees_it - read in full rather than taken from its name: parametrised over file:///etc/passwd, javascript:alert(1), ftp://, a bare 'example.test/watch' (which yt-dlp would turn into a web search), the empty string and whitespace, it asserts 400 and that no job and no media row were created, with the probe monkeypatched to refuse so yt-dlp is provably never reached. AC2 by test_the_preview_says_what_a_single_link_is and test_the_preview_of_a_playlist_counts_the_entries_and_says_all_of_them, with test_the_preview_escapes_a_title_that_is_markup pinning that a hostile video title cannot inject markup into the preview. AC3 by test_a_preview_that_hangs_gives_up_without_holding_the_page, and test_the_preview_refuses_a_url_that_is_not_a_web_address_without_probing keeps the guard in front of the network on this path too.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
URL import dialog with a preview that says what will be fetched (commit a1c3618). Verified by tests/test_web_url_dialog.py: a non-http URL is refused with 400 before yt-dlp sees it and queues nothing, the preview names a single link and counts a playlist's entries while escaping a title that is markup, and a hanging probe gives up with a readable message rather than holding the page.
<!-- SECTION:FINAL_SUMMARY:END -->
