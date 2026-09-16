---
id: TASK-078
title: >-
  With scripting off, renaming or trashing from the transcript rail dumps the
  user back in the library
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:30'
updated_date: '2026-09-16 17:37'
labels:
  - review-2026-09-16
  - web
dependencies: []
priority: low
type: bug
ordinal: 123000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low). The transcript page's rail forms - rename, move to a folder, trash, restore - carry method=post and action= so they work without script, and post to the library's routes, whose plain-form answer (_after_change) is a 303 to the library view the request's origin state names. From /media/12 that is the library root: a person who renamed a recording from its own page is dropped into the library and has to find their way back. The htmx path is unaffected (data-refresh re-fetches the panel).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A plain post from a rail form on /media/{id} answers with a 303 back to /media/{id}; a plain post from the library keeps its 303 to the library view
- [x] #2 The back address is the Referer's path and nothing else - exactly /media/<int> is honoured, the way the library's own plain posts already read their view state from the Referer; any other path keeps the library redirect
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_web_library - a plain rename and a plain trash with Referer http://testserver/media/<id> answer 303 to /media/<id>; without that Referer the 303 to the library view stays.
2. Green: library._page_from_referer reads the Referer's path only and honours exactly /media/<int>; _after_change's plain-post branch redirects there when set. Same mechanism the library's own plain posts already use for their view state (_origin_state).
3. Run test_web_library and test_web_transcript one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: a plain rename and trash with Referer http://testserver/media/<id> answered 303 to /. Green after library._page_from_referer (path only, exactly /media/<int>) in _after_change's plain branch: test_web_library 81, test_web_transcript 98. A Referer to any other path keeps the library redirect (pinned).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A plain post from the transcript rail comes back to /media/<id>, read from the Referer's path the way the library already reads its view state; anything else keeps the library redirect. Verified red-to-green by two tests, 179 tests across two files.
<!-- SECTION:FINAL_SUMMARY:END -->
