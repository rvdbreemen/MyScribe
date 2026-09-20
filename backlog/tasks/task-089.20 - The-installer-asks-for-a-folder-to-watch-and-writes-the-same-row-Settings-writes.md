---
id: TASK-089.20
title: >-
  The installer asks for a folder to watch, and writes the same row Settings
  writes
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - ingest
  - settings
  - ux
dependencies:
  - TASK-089.11
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 157000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 as one of three extra questions (brief: R3, U4). 'How do recordings get in?' is the first thing a new user needs answered, and today the answer is a separate thing afterwards: find Settings, find the watch-folder form. His rule: rather a few extra questions during installation.

The counterpart in Settings exists already, which scribe/setup.py:21-23 requires before an installer may ask. Settings adds a `watch_folder` row through watching.add_folder (scribe/ingest/watching.py:284-304), and each row carries its own transcribe options, 'because nobody is at the dialog when the file lands' (scribe/web/settings.py:27-35). The question writes that same row through that same function, and nothing else.

The form refuses four things, each a mistake the user can fix from where they stand. Three of them, and a blank check, are in settings.parse_watch_path (scribe/web/settings.py:599-640): a path that is not an absolute, existing directory; a path outside the browse roots; a path inside the data directory. The fourth, a path already watched, is only in that function's docstring: the refusal itself is in the POST handler add_watch_folder, which turns the UNIQUE column's sqlite3.IntegrityError into a 409 '... is already being watched' (:664-667). parse_watch_path raises FastAPI's HTTPException, and the engine is not a web request. The installer applies the same four, and does not invent a fifth or skip one.

One of them bites on the reference machine. On Windows the default browse root is the drive the profile lives on, and nothing else (scribe/fsbrowse.py:44-53). A user whose recordings live on D: and whose profile is on C: is refused with 'outside the folders this app may read from; widen them under Settings'. That is the documented boundary of what the app may read, so the installer does not widen it by itself; it says the sentence and says where.

The watch folder's own options include diarize, which defaults to True (scribe/options.py:42). A machine with no token would therefore lose whole transcriptions from a watched folder today. TASK-089.08 is what makes this question safe to skip the token beside; it is built first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The question is skippable, default skip. Skipping writes nothing and is recorded as skipped (TASK-089.11). Its answer_later line names Settings > Watch folders.
- [ ] #2 A given folder is written as a `watch_folder` row through watching.add_folder, with the current transcribe defaults as its options. A test shows the row is identical to the one POST /settings/watch writes for the same input.
- [ ] #3 The same four refusals apply, with the same sentences: the three of settings.parse_watch_path (scribe/web/settings.py:599-640) - not an absolute existing directory, outside the browse roots, inside the data directory - and the duplicate refusal of add_watch_folder (:664-667), which the engine gets from the same sqlite3.IntegrityError out of watching.add_folder. The engine maps HTTPException.detail to its sentence, or the checks move into a plain function that both callers use; either way no FastAPI type reaches the asker. One test per refusal, and a refused folder writes nothing and reopens the question.
- [ ] #4 A folder outside the browse roots is refused and not written, and the sentence says how to widen the roots in Settings. The installer never writes fsbrowse_roots by itself. On Windows a test covers a folder on a second drive.
- [ ] #5 The question is only present when no watch_folder row exists yet, and a re-run shows the folders that are watched instead of asking.
- [ ] #6 Nothing is ingested during the sitting. The watcher thread picks the folder up when the app starts, as it does for a row added in Settings, and a test shows a file dropped in the folder becomes a recording after the start.
<!-- AC:END -->
