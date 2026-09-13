---
id: TASK-041
title: >-
  Episodes imported from a podcast feed are named after their episode title -
  the job, the media and the file
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 21:45'
updated_date: '2026-09-11 22:02'
labels:
  - ingest
  - podcast
dependencies: []
ordinal: 75000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The user asked (2026-09-11) that RSS feed imports use the podcast name from the feed metadata for the job and the file. Pasting a feed URL fans out into one ingest_url job per episode, but only the enclosure URL travels to the child job, so the title in the feed is lost: probing NPR Planet Money showed episode 1 as "The loan at the heart of a new foreclosure crisis" in the feed, while the child download of its enclosure (.../default.mp3) is titled "default.mp3_ywr3ahjkcgo_d69e0cb5864c0e62da44eafa2ce7b645_31070322" - and until it registers, the jobs board lists every episode as "ingest_url job".
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each job fanned out from a playlist or feed carries its entry title, and the jobs board shows that title for it before any media exists
- [x] #2 The downloaded file is named after the entry title (scrubbed as today) and the media row gets that title and original name
- [x] #3 An entry without a title keeps what yt-dlp says, as today
- [x] #4 Tests cover the fan-out params, the download naming and the board title; a real feed episode imported through the app shows its feed title
- [x] #5 Feed episodes are named from their metadata including the episode number: "Ep 179 - The Courthouse - Revisited" (itunes episode number, the clean itunes title, no doubled number, zero-padded to the feed's widest, season only when the feed has several); an episode without a number gets its release date instead ("2026-09-11 - Title"); a number is never invented from feed position
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. urls._entries keeps the itunes fields (episode, episode_number, season_number, timestamp); urls.episode_names names feed entries (Generic extractor): 'Ep 179 - title' with the itunes title, no doubled number, zero-pad to the widest, season only when the feed has several; else the release date; else the title. probe sets entry['name']. 2. url_stage._fan_out carries it as params['entry_title']; fetch passes it to urls.download(title=...), which names the file and the media row. 3. jobs_ui titles a job without media by entry_title. Tests first.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11 user added: the name must be a real name from the metadata that includes the episode #. Real feeds: Darknet Diaries carries itunes episode_number 179 / season 1 with title '179: The Courthouse - Revisited' and itunes title 'The Courthouse - Revisited'; its trailer has no number. Planet Money (355 entries, a truncated window of a longer show) and The Changelog (1014, over MAX_FAN_OUT) carry no numbers at all, so position cannot stand in for one; the release date (pubDate) is the metadata fallback. Feeds are recognised by yt-dlp's Generic extractor (YouTube playlists come from YoutubeTab and keep their titles).

Red then green: 9 new tests failed before the change (the 10th, a plain URL keeping yt-dlp's title, is the baseline). Live feeds 2026-09-11: Darknet Diaries -> 'Ep 179 - The Courthouse - Revisited', '2026-08-06 - LOW - Trailer', 'Ep 001 - The Phreaky World of PBX Hacking'; Planet Money (no numbers) -> '2026-09-11 - The loan at the heart of a new foreclosure crisis'. Real run in the dev app on 4299: one ingest_url child queued exactly as _fan_out does for episode 179; the jobs board showed 'Ep 179 - The Courthouse - Revisited' (no 'ingest_url job' labels); media row title 'Ep 179 - The Courthouse - Revisited', orig_name 'Ep 179 - The Courthouse - Revisited.mp3', 107.7 MB; transcribe job 5 queued for it. Full suite 1684 passed, 18 skipped.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Feed episodes keep the name the feed gives them: fan-out carries each entry's name to its job, the download names the file and recording with it, and the jobs board shows it before the media exists. For podcast feeds the name includes the itunes episode number ('Ep 179 - The Courthouse - Revisited'), or the release date when the feed has no numbers; YouTube playlists are unchanged. Verified with 10 new tests (red first), live probes of Darknet Diaries and Planet Money, and a real episode imported through the app.
<!-- SECTION:FINAL_SUMMARY:END -->
