---
id: TASK-089.19
title: >-
  An existing MyScribe library is found and offered, and adopting it never sends
  its back catalogue to a provider nobody chose
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-20 21:29'
labels:
  - library
  - packaging
  - ux
dependencies:
  - TASK-089.17
  - TASK-089.14
  - TASK-089.11
  - TASK-089.07
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 156000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 as one of three extra questions (brief: R3, M5). Today this is a 'separate thing afterwards', and a quiet one. Robert's own machine is the example: a clone with a live library in <repo>/data (scribe/paths.py:4), and a release install that would start on an empty %LOCALAPPDATA%\MyScribe\data, because the launcher forces SCRIBE_DATA_DIR to <home>/data (packaging/launcher/myscribe_launcher.py:200-207). Nothing says 'a MyScribe library already exists at X - use it, or start fresh?'. The user sees an empty app and concludes their recordings are gone. A reader noted that %LOCALAPPDATA%\MyScribe did not exist on this machine on 2026-09-20, so the released installer has never had a first run here.

A release cannot discover that clone by itself. It has no <repo>: its default data directory resolves inside the payload (scribe/paths.py:4, with app_dir = payload/app at myscribe_launcher.py:95), the launcher forces SCRIBE_DATA_DIR (:200-207), and a clone that uses the default ./data names its library in no environment layer. From a release, a library inside a clone is therefore found only when the user names the folder.

The app already treats an older installation as something to adopt, not abandon: adopt_legacy_db renames a `scribe.db` from before the rename (scribe/paths.py:12-32). This question is the same manners, one level up.

It interacts with R1, which is why TASK-089.07 lands first. The lifespan runs sweep_speaker_passes at every start (scribe/app.py:261), with no ceiling. Adopt a library with diarized recordings, skip the provider question, and today the first start queues one OpenRouter job per recording.

Two facts make adoption less innocent than it looks. db.migrate is a forward-only ladder: `range(version + 1, SCHEMA_VERSION + 1)` (scribe/db.py:551-558). A library NEWER than the app gives an empty range, and the app then runs on a schema it does not know, without a word. And merely looking is a write if done carelessly: db.connect switches the file to WAL (scribe/db.py:544) and, by default, renames a legacy database (:540).

An open point this task settles, with a test: how a release points at an adopted library, given that the launcher forces SCRIBE_DATA_DIR. The pointer file of TASK-089.14 moves the whole home, which is not the same thing as using a library that lives inside a clone. ADR-015 fixes the principle and leaves the rest to this task, as below the level of a decision record. That is the agent's proposal in the grill of 2026-09-20 and not Robert's decision; he sees it in ADR-015's acceptance packet and can overturn it there. The principle: where things live is kept in a small pointer file that the launcher reads with the standard library before anything else exists, the launcher writes it only on the engine's instruction and never decides its content, and the app is never asked to move a library. Whether that file holds a second fact for a library adopted in place is the design spec's proposal until this task has built and measured it (criterion 8). 'Is a MyScribe serving that library right now?' is answered by the two fields `/health` gains in TASK-089.17: decided by Robert on 2026-09-20 (brief: G5, which settles W2). Adopting migrates, so this task depends on TASK-089.17 and carries criterion 11: a library is not migrated under a MyScribe that is serving it.

Skip means: start fresh, leave the old library untouched, and say where it is.

Needs a real machine: A COPY of Robert's library, on his machine. Never the live one: that is his own rule.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The sitting looks for a myscribe.db, and for a legacy scribe.db from before the rename (scribe/paths.py:8-9), in <repo>/data when it runs from a clone, in the per-user home, and in a SCRIBE_DATA_DIR found in any environment layer other than the target. It also accepts a folder the user names, typed or browsed, because a release cannot discover a clone. A scribe.db is shown as a library from before the rename; looking does not rename it (criterion 2), and adopting does, through adopt_legacy_db. For each one found it shows the path, the number of recordings and the size on disk.
- [ ] #2 Looking changes nothing. The found database is opened read-only and not through db.connect. A test shows that no file next to it is created, renamed or modified: no -wal, no -shm, no migration.
- [ ] #3 Skip starts fresh, leaves the found library untouched, and says where it is. It is recorded as skipped (TASK-089.11) and does not return at every start.
- [ ] #4 Adopting never copies or moves a library silently. Before it adopts, the sitting says that this version will migrate the library, and that an older MyScribe must not open it afterwards.
- [ ] #5 Red first: a found library whose user_version is newer than this app's SCHEMA_VERSION is refused with one sentence. Today migrate() does nothing for that case and the app would run on it (scribe/db.py:551-558).
- [ ] #6 Red first, on a COPY of a library with N diarized, never-asked recordings and an OpenRouter key in the environment: adopting it and skipping the provider question queues N llm jobs on the first start today. After TASK-089.07 it queues none, and the test counts the job rows.
- [ ] #7 Before adopting, the sitting says how many diarized recordings have never been asked who is speaking, and that choosing a cloud provider later will send each of them once. Nothing else about the library is sent or changed.
- [ ] #8 How a release points at an adopted library is settled here, with a test, before the adoption is built; the decision and its reason are in the notes. It stays inside the principle ADR-015 fixes: a small pointer file that the launcher reads with the standard library before anything else exists; the launcher writes it only on the engine's instruction and never decides its content; the app is never asked to move a library. The design spec's proposal is the candidate until this task has built and measured it: the engine's result names the data directory, the launcher writes it into the pointer file as a second fact, and Layout.data_dir then prefers it. The test: a release layout whose pointer names an adopted library starts the app on that library and on no other, through the SCRIBE_DATA_DIR the launcher forces (packaging/launcher/myscribe_launcher.py:203); with no such fact it starts on <home>/data, as today. That this is this task's to settle is the agent's proposal of 2026-09-20 and not Robert's decision (ADR-015, Open Questions). ADR-015 needs no amendment while the result stays inside that principle; if it cannot, the notes say why and the record is amended before the code lands.
- [ ] #9 Every run in this task is on a COPY of a library, never the live one, and names the copy it used.
- [ ] #10 Where the answer can be given later: `--setup` and the Setup button, and in a clone also SCRIBE_DATA_DIR in `.env` or `--data-dir`, which is the documented manual route (.env.example:30). There is no Settings counterpart. That is a stated exception to scribe/setup.py:21-23, with its reason: the settings rows live in the library's own database, so switching libraries from inside the running app would change the database under the process that serves it. Settings > This machine already shows the store's path (scribe/templates/settings.html:100-104) and gains one line saying how to change it, and the question's answer_later line says the same. Robert can overturn this by asking for a Settings counterpart, which is then built first, as TASK-089.21 is for TASK-089.22. TASK-089.11 criterion 4 allows this exception by name.
- [ ] #11 A library that a running MyScribe is serving is not migrated under it: the lifespan connects and migrates at start (scribe/app.py:247-248), and the ladder only goes forward (scribe/db.py:551-558). The mechanism is the one Robert decided on 2026-09-20 (brief: G5) and TASK-089.17 builds (its criteria 8 and 9): `/health` names the source tree the app runs from and the data directory it serves, and this task reads the data directory. It invents no mechanism. When it says the found library is being served, adoption is refused with 'stop that MyScribe first', and nothing is migrated. A MyScribe that answers /health without saying what it serves - an older one - is doubt, and doubt refuses the same way, as it does for the sync in TASK-089.17. What the sitting cannot see at all - an app on a port nobody named, this repository's own `--port 4299` among them - is said in the sentence that asks for the explicit yes before adopting. A job row in state `running` in the found database is read without writing and refuses in any case; it needs no port. One test per case, and each asserts that the found database's user_version did not move.
<!-- AC:END -->
