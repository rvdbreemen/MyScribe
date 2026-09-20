---
id: TASK-089.02
title: >-
  ADR-015 is decided before code depends on it, and the stdlib-only guard covers
  install.py
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - adr
  - architecture
  - packaging
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 139000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three durable decisions are being made here, and each belongs in an ADR before the code exists (.adr-kit/ADR-guide.md:40-43). One app-side setup engine sits behind a JSON contract that the frozen launcher depends on. MyScribe may install somebody else's software, under conditions. And the per-user home can be moved by a pointer file that home_dir() reads.

ADR-015 is written as Proposed in the recording step of 2026-09-20 (brief: R4). This task carries it to a decision. Acceptance is Robert's: the guide forbids accepting on one's own initiative.

Two things the earlier plan decided silently are argued in the ADR instead. First, whether install.py shares the launcher's CODE through a CloneLayout or only its SEQUENCE (brief: M11). The packaging reader advised sequence only, because the launcher must stay frozen-safe and ADR-011 keeps it small on purpose; the plan chose shared code in a files table. Second, the rules for the Ollama marker, which as first proposed let MyScribe install over an Ollama the user had put there (brief: M1).

A correction to the plan. It proposed adding a second forbid_pattern to ADR-011's Enforcement block. ADR-011 is Accepted, and the guide says never to rewrite an Accepted ADR (.adr-kit/ADR-guide.md:52). This repository's own practice agrees: ADR-013 and ADR-014 are restatements made for exactly that reason. The guard for install.py therefore lives in ADR-015's own Enforcement block, or ADR-011 is superseded. It is not edited in place.

ADR-011's pattern today is a deny-list of seven module names over packaging/launcher/** (its Enforcement block, lines 237-247). `import requests` would pass it. The real stdlib-only proof is the AST allow-list test in TASK-089.17, and the ADR text says so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ADR-015 is Accepted by Robert, or amended and then accepted. Nobody else runs the accept.
- [ ] #2 It records the engine contract, the rule for installing third-party software, and the home pointer file, each with the alternatives considered: questions in the Inno wizard, a /welcome page in the web UI, asking everything before the sync, and leaving 'only when missing' to Ollama's own scripts. It also says how `python -m scribe.doctor` from a terminal, and `--prove` when no app answers, sit with ADR-001: that record's Must keeps GPU work inside runner children and its Exceptions read 'None' (docs/adr/ADR-001-...md:116-118, :128-131), and no ADR records the doctor command as an exception today (TASK-089.13 criterion 9).
- [ ] #3 Sharing the launcher's code with install.py versus sharing only its sequence is argued as a considered option, with its consequences for the frozen binary, and one is chosen (brief: M11).
- [ ] #4 The marker rules are in the ADR as decided: written only after the installer finished successfully; the installer never offered again while any Ollama binary exists; cleared once Ollama has been seen ready (brief: M1).
- [ ] #5 Its context names the two recorded choices it revises knowingly: TASK-040.06's 'Four answers, and no more than four' (scribe/setup.py:9) and the spec's 'Defaults: commercial providers' (docs/superpowers/specs/2026-09-01-myscribe-design.md:219).
- [ ] #6 The stdlib-only guard covers install.py without editing ADR-011 in place. On a scratch COPY of the repo in which ADR-015 is marked Accepted, `adr-judge --dry-run-enforcement ADR-015` flags a probe diff that adds `import scribe` to install.py and `command += ["--llm-key", key]` to the launcher, and the output is shown: that is ADR-015's own Verification line. The judge skips a record that is not Accepted, which is why the copy is marked, and why this can be shown before criterion 1 is met without anybody running the accept in the repository. After acceptance the same diff is blocked by `/adr-kit:judge` - the kit's `adr_judge`, or `bin/adr-judge --json` from its CLI (.adr-kit/ADR-guide.md:22); the guide lists no `adr-kit judge` command.
- [ ] #7 ADR-INDEX.md and ADR-INDEX.json are updated by the kit, not by hand.
<!-- AC:END -->
