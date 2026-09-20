---
id: TASK-089.03
title: >-
  The setup, models and doctor commands read .env the way the app does, and a
  blank variable never hides a real value
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - bug
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 140000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Any installer that writes to `.env` breaks on two facts. scribe/setup.py:34, scribe/models.py:37 and scribe/doctor.py:36 import scribe.paths at module load, and scribe/paths.py:4 fixes DATA_DIR at that moment; each calls env.load_dotenv() only later (setup.py:170, doctor.py:814). scribe/__main__.py:82-86 does it the other way round and says why. So a SCRIBE_DATA_DIR set only in `.env` - the documented way to move the library (.env.example:30) - is honoured by the app and ignored by all three commands.

What the user hits: they move the data directory through `.env` and run setup. The token row, the provider row, the stamp and the downloaded weights land in ./data, and the app, reading the other directory, behaves as if setup never happened. A reader showed it with a scratch env file on 2026-09-20: the file said 'elsewhere', setup wrote to ./data, the app read 'elsewhere'.

Separately, load_dotenv applies the file with os.environ.setdefault (scribe/env.py:65-66). An empty variable exported by a shell, a compose file or a service unit therefore keeps a real token in `.env` from ever arriving.

write_token has the smaller fault its own docstring warns about. It reads utf-8 where env.py reads utf-8-sig, and matches only the exact prefix `HF_TOKEN=` (scribe/setup.py:95, :98). A file Notepad saved with a BOM, or one written as `HF_TOKEN = old`, ends up with two lines for one name.

This comes first and depends on nothing (brief: M9).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: with SCRIBE_DATA_DIR only in a scratch env file (SCRIBE_ENV_FILE), `python -m scribe.setup --status`, `python -m scribe.models` and `python -m scribe.doctor --no-gpu` all use that directory. The scratch-run output is shown before and after.
- [ ] #2 Red first: a blank or whitespace-only process variable no longer shadows a non-blank `.env` value. A non-blank process value still wins, and tests/test_web_scaffold.py:218-244 stays green.
- [ ] #3 load_dotenv records which names it applied from the file, and a caller can ask which names those were.
- [ ] #4 A write to `.env` reads utf-8-sig, matches `NAME = value` with spaces, and never leaves two lines for one name; the BOM case and the spaced case are red first. It goes through a temp file and os.replace, and creates the file 0600 on POSIX, shown in WSL. macOS is not measured, and the notes say so. The writer takes any NAME, not only HF_TOKEN. After TASK-089.09 no secret is written to `.env` any more (its criterion 7), so write_token (scribe/setup.py:84-106) loses its caller. What still writes `.env` is a line that is not a secret: SCRIBE_DATA_DIR, when a clone adopts a library (TASK-089.19), which is the documented way to move one (.env.example:30). If TASK-089.19 decides otherwise, the writer goes with write_token, and the notes say so.
- [ ] #5 `<repo>/.tools/bin` is first on PATH for `python -m scribe`, scribe.setup, scribe.models and scribe.doctor when it exists. `.tools/` is in .gitignore.
<!-- AC:END -->
