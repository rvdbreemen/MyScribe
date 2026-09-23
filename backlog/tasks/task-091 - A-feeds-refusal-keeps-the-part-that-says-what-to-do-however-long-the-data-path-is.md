---
id: TASK-091
title: >-
  A feed's refusal keeps the part that says what to do, however long the data
  path is
status: Done
assignee: []
created_date: '2026-09-20 21:41'
updated_date: '2026-09-23 20:42'
labels:
  - feeds
  - ui
dependencies: []
references:
  - scribe/ingest/feeds.py
  - scribe/doctor.py
  - tests/test_feed_first_episode.py
priority: low
type: bug
ordinal: 163000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found on 2026-09-20 by running the suite with the data directory pointed at a long scratch path (TASK-090's fence).

`scribe/ingest/feeds.py:458` and `:464` store a poll's outcome as `result[:200]`. The disk-floor refusal reads "only 1.0 GB free at <path>, and this app keeps 10 GB clear on the drive your recordings land on. Free up space and retry; nothing was downloaded." (scribe/doctor.py:99-104). The path sits in the middle, so a long one pushes the actionable half over the 200-character edge.

Measured: from a data directory of 142 characters the amount the app needs is gone, and the Feeds page shows only "not checked: only 1.0 GB free at <path>". Robert's own paths are far shorter - 51 characters for the clone, 42 for a release install - so this is not his machine today. A user who keeps recordings in a deeply nested or cloud-synced folder can reach it, and Windows allows 260.

It surfaced as a failing test rather than as a report: tests/test_feed_first_episode.py:174 asserts the floor is in `last_result`, and it fails when the data directory path is long. The test is right; the message is what gives.

Not urgent, and not caused by the work it was found during.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first: tests/test_feed_first_episode.py:150 is run with a data directory whose path is over 141 characters and fails on its `last_result` assertion, and that output is kept.
- [x] #2 A refusal that is too long keeps what a person acts on - how much is free, how much is needed, and that nothing was downloaded - and gives up the middle of the path instead, or the whole path.
- [x] #3 The 200-character store is still respected, and a test pins what a refusal looks like at the longest path Windows allows.
- [x] #4 The same test passes with a short path and with a long one, and both runs are shown.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red first: parametrize tests/test_feed_first_episode.py::test_a_poll_below_the_disk_floor_queues_nothing_and_says_so over a short and a long (150-character) data directory, and assert that last_result keeps the free amount, the 10 GB floor and "nothing was downloaded". Keep the red output of the long case on today's code.
2. doctor.py: NotEnoughDisk carries free_gb, path and floor_gb; one builder writes the message, and fitted(limit) shortens only the path, in its middle, until the message fits. str(exc) stays the full message for the runner, the ingest page and the 507 on the Feeds page.
3. feeds.py: the disk-floor refusal stores "not checked: " plus the fitted message. The 200-character store stays as a final cut. The probe-failure site keeps its blind cut (no known structure).
4. A test pins the stored refusal exactly for a 260-character path (disk_probe_path monkeypatched, nothing created), and asserts it is 200 characters or fewer.
5. Mutants on a copy; run every test file that imports doctor or feeds.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Build agent (gate lane, worktree MyScribe-wt-gate), 2026-09-23. Not committed, no criterion ticked.

What changed
- scribe/doctor.py: NotEnoughDisk now carries free_gb and path, and has fitted(limit). A new block "TASK-091: the refusal keeps what a person acts on" holds disk_refusal(free_gb, path, limit=None), the one builder of the message. Without a limit, or when the message fits, the text is exactly what it was. When it does not fit, the path gives up its middle (a Unicode ellipsis between its start and its end). When not even three characters of path fit, the path and its "at" go entirely. The words around the path are never cut. require_disk_headroom raises with that builder, so str(exc) is unchanged for the runner (DISK_LOW), the ingest page and the Feeds page's 507.
- scribe/ingest/feeds.py, poll(): the disk-floor refusal stores "not checked: " + exc.fitted(187), still passed through [:200]. The returned "error" keeps the whole path. The probe-failure site right below keeps its blind [:200]: that text is an arbitrary exception message with no known structure, and its head (type and error) is the part that says what happened. The task names that line, but none of its criteria are about it.

Finding that changes the task's picture
- The fixed words of the stored refusal take 152 of the 200 characters at "1.0 GB". So a path over 48 characters already lost "nothing was downloaded", and one over 141 lost the amount needed. The task says Robert's paths (51 for the clone, 42 for a release install) are far shorter. The clone's 51 characters were already over the first edge: its stored line ended mid "downloaded". The short case of the test (the conftest library, about 96 characters here) failed red on that too.

Criteria and proof (EVIDENCE = the 091 folder next to this plan)
- #1: red-test_feed_first_episode.txt, run on the old doctor.py and feeds.py before any change. test_a_poll_below_the_disk_floor_queues_nothing_and_says_so[long] (data directory of 150 characters, under tmp_path) fails on the existing last_result assertion: "assert '10' in 'not checked: only 1.0 GB free at C:\...ddd, and this app ke'".
- #2: disk_refusal and fitted. Proof: the [short] and [long] cases now assert the stored line holds "only 1.0 GB free", "keeps 10 GB clear" and ends with "nothing was downloaded.", and is at most 200 characters. test_a_refusal_with_no_room_for_the_path_drops_the_whole_path pins the whole-path fallback. tests/test_web_feeds.py::test_the_feeds_page_shows_a_disk_refusal_that_kept_what_to_do shows it on the Feeds page through TestClient.
- #3: test_a_disk_floor_refusal_at_the_longest_windows_path_is_pinned. A 260-character PureWindowsPath (not created, disk_probe_path replaced) gives exactly: "not checked: only 1.0 GB free at C:\" + 20 a's + ellipsis + 24 b's + ", and this app keeps 10 GB clear on the drive your recordings land on. Free up space and retry; nothing was downloaded." That is 200 characters. The expected shape was computed before the code ran (48 characters of path room: 23 in front, 24 behind).
- #4: green-test_feed_first_episode.txt shows [short] and [long] PASSED in one run (16 passed). red-old-code.txt shows both failing on the old code with the new tests in place.

Mutants (copy in EVIDENCE/mut, `grep -rn MUTANT scribe tests packaging` in the worktree finds nothing)
- red-old-code.txt: old doctor.py and feeds.py from HEAD. 4 failed in the feed file, 1 in test_web_feeds.
- mut-blind-cut.txt: feeds stores the old blind cut. short, long, the 260 pin and the page test fail.
- mut-head-only.txt: the path keeps only its start. The 260 pin fails.
- mut-keep-path-when-no-room.txt: the fallback keeps the path. The whole-path test fails.
- mut-room-off-by-one.txt: one character less path room. The 260 pin fails.

Other files importing doctor or feeds, one process each, all green (green-<file>.txt): test_credentials 35, test_disk_floor 9, test_doctor 64, test_dotenv_commands 9 (4 runpy warnings, unrelated), test_feed_backfill 13, test_feed_follow 13, test_feeds 18, test_ingest_urls 112, test_install 56, test_launcher 102 + 1 skipped, test_launcher_sitting 55, test_llm_ollama 55, test_llm_privacy 19, test_llm_selftest 14, test_models 49, test_ollama_setup 80, test_params_door 26, test_setup 15, test_setup_plan 144, test_setup_prove 43, test_stage_transcribe 69, test_stage_transcribe_mlx 12, test_web_feeds 19, test_web_jobs 43, test_web_settings 54, test_web_url_dialog 90.

Changed tests
- test_a_poll_below_the_disk_floor_queues_nothing_and_says_so is parametrized over short and long, and gains assertions. Nothing was loosened; its original assertions stay. It now takes tmp_path.

Not done
- No run on a real machine with a nearly full disk. The page test renders the stored line through TestClient; the app was not started.

Addendum. The evidence folder named above is C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/d0ea7837-cd8e-4a70-958e-8536dd2e4652/scratchpad/build/gate/091 (scratch, not in the repo).
- The [short] case is the conftest library, about 96 characters on this machine. That is already past the old 48-character edge, so it proves shortening too, not a path that fits.
- The case that fits has its own test: test_a_refusal_that_fits_is_stored_whole. A 42-character path (the release-install length the task quotes) is stored whole, with no ellipsis, identical to the answer's error.
- Mutant mut-no-fits-return.txt removes the early return for a message that fits. That test fails; nothing else did.
- Final run: green-test_feed_first_episode.txt, 17 passed.

Verified 2026-09-23 by the orchestrator in MyScribe-wt-gate, fenced, one file per process: test_llm_cleaning_gate 31, test_feed_first_episode 17, test_web_feeds 19, test_web_transcript 107, test_doctor 64, test_llm_tasks 159, test_ingest_urls 112, test_stage_prepare 27, all passed; grep MUTANT over scribe tests packaging install.py finds nothing. Recorded for Robert: the task's premise was partly wrong - the fixed words take 152 of the 200 characters, so from a 49-character path the old store already lost 'nothing was downloaded', and Robert's 51-character clone path was already past that edge. The probe-failure line at feeds.py keeps its plain cut on purpose: its text has no known structure and no criterion covers it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A disk-floor refusal stored in a feed's last result keeps what a person acts on - free, needed, and that nothing was downloaded - by shortening the middle of the path, or dropping the path when there is no room; the 200-character store holds, pinned at a 260-character Windows path. Verified red first with a 150-character data path, five mutants, 26 dependent files green, and a Feeds page TestClient test.
<!-- SECTION:FINAL_SUMMARY:END -->
