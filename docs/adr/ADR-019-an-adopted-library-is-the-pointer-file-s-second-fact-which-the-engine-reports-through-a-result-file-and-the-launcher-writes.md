---
id: "ADR-019"
title: "An adopted library is the pointer file's second fact, which the engine reports through a result file and the launcher writes"
status: "Accepted"
date: "2026-09-24"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "setup"
  - "installer"
  - "library"
aliases:
  - "adopt a library"
  - "pointer file"
  - "MyScribe.location"
  - "MYSCRIBE_SETUP_RESULT"
components:
  - "scribe.library"
  - "packaging.launcher"
symbols:
  - "adopted_data"
  - "write_pointer_data"
  - "take_setup_result"
  - "_library_door"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-019 An adopted library is the pointer file's second fact, which the engine reports through a result file and the launcher writes

## Status

Accepted, 2026-09-24.

## Status History

```yaml
status_history:
  - date: 2026-09-23
    status: Proposed
    changed_by: Claude (agent, session 2026-09-23)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-24
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: Accepted by Robert on 2026-09-24, asked for in the session as 'Accepteer beide ADR', after answering its open question in the same session.
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

TASK-089.19 lets the first-run sitting adopt a MyScribe library that already
exists, in place. A release cannot be pointed at it the usual way: the
launcher forces `SCRIBE_DATA_DIR=<home>/data` into the app's environment
(`app_environment`), so a line in `.env` is overwritten. ADR-015 fixed the
principle and left the mechanism to this task (its Open Questions): where
things live is kept in a small pointer file the launcher reads with the
standard library before anything else exists; the launcher writes it only on
the engine's instruction and never decides its content; the app is never asked
to move a library.

Two facts shaped the answer. The pointer file of TASK-089.14 already exists
(`MyScribe.location`, beside the default home) and its reader keeps unknown
keys. And one of the launcher's two doors never reads the setup child's
stdout: the console door hands the terminal to the engine, which asks for
itself.

## Decision Drivers

* Inside ADR-015's principle, so that record needs no amendment.
* The launcher stays stdlib-only and decides nothing (ADR-011, ADR-015).
* One channel for both launcher doors, the window and the console.
* No silent fallback: a library that is gone must not become a new, empty one.

## Considered Options

* The pointer's second fact, `"data"`, reported through a result file.
* The same fact, reported as a JSON line on the setup child's stdout.
* The engine writes the pointer file itself.
* Move the adopted library into `<home>/data`.

## Decision Outcome

Chosen option: **the pointer's second fact, reported through a result file**,
because it is the only one that reaches both doors and keeps the launcher a
writer of facts it was handed.

The launcher names a file for every setup child in `MYSCRIBE_SETUP_RESULT`.
After an adoption the engine writes `{"data_dir": ...}` there; for a folder
somebody typed, which it has only looked at, `{"library": ...}`. The launcher
reads and removes the file when the child has gone. On `data_dir` it merges
`"data"` into the pointer file beside `"home"` and plans again against that
library, whose own stamp decides what is still open. On `library` it plans
again with `--library <folder>`, so the folder's numbers are shown before
anybody says yes. `Layout.data_dir` prefers `"data"`, and `SCRIBE_DATA_DIR`
follows it. A `"data"` that names a folder that is not there stops the launcher
with a sentence. With no variable set - a clone, `install.py`, a bare run - the
engine writes `SCRIBE_DATA_DIR` into `.env`, the documented manual route.

### Confirmation

`tests/test_launcher_library.py` starts the launcher's `main` on a release
layout whose pointer names an adopted library and asserts the
`SCRIBE_DATA_DIR` the app process gets; with no such fact it gets
`<home>/data`; with a folder that is gone nothing starts. The door tests assert
that only the engine's result, never an answer, moves the library.

## Decision Contract

### Must

* The engine alone decides an adoption; the launcher writes `"data"` only from
  a result file the engine wrote.
* A pointer `"data"` that names a missing folder stops the launcher with a
  sentence, and never falls back to `<home>/data`.
* Writing `"data"` keeps `"home"`, and writing `"home"` keeps `"data"`.
* `--home` and `MYSCRIBE_HOME` keep everything under that home: the pointer's
  `"data"` is not read under them, and an adoption there says it holds for
  this start only.

### Must Not

* Copy or move a library to adopt it.
* Let a front-end read a question id to decide that a library was adopted.

### Exceptions

* None.

### Verification

* `tests/test_launcher_library.py`, all of it.
* `tests/test_setup_library.py::test_the_launcher_hears_the_adoption_and_dotenv_is_left_alone`.

## Consequences

### Positive

* A release uses a clone's library in place, and a second start finds it.
* The console door and the window door hear the same result.

### Negative

* The pointer file holds two facts, and one file beside the home now names a
  folder somewhere else on the disk. An uninstall that removes the home leaves
  that library alone, which is right, and the pointer names it.
* A third channel beside `--plan` and `--apply-stdin`. It carries a path and
  never a secret.

## Pros and Cons of the Options

### The pointer's second fact through a result file (chosen)

* Good, because both doors hear it, and the launcher writes what it was handed.
* Bad, because a file is one more thing that can be left behind; it is
  removed before and after every child.

### The same fact as a stdout line

* Good, because the installer already speaks in JSON lines.
* Bad, because the console door never reads the child's stdout.

### The engine writes the pointer itself

* Good, because nothing crosses a process boundary.
* Bad, because the engine would have to know the launcher's file and layout,
  which ADR-015 gives to the launcher.

### Move the library into the home

* Good, because nothing new is needed in the launcher.
* Bad, because moving gigabytes of somebody's recordings is what criterion 4
  forbids silently and ADR-015 forbids at all.

## Open Questions

- [x] Is "asked first, in the full list" right for the library question, where the design spec had "asked alone"? A first step that stamps leaves the rest unasked at every later start, so a document that adopts writes nothing else and the door plans again against the adopted library. The cost: somebody who adopts answered the other questions once for nothing. Robert decides. — **Answered 2026-09-24 by User: Robert van den Breemen:** Asked first, in the full list: Robert chose this on 2026-09-24, accepting that somebody who adopts answers the other questions once more against the adopted library.

## Related Decisions

* ADR-015 fixes the principle this record stays inside.
* ADR-011 keeps the launcher stdlib-only; the pointer is read with `json`.

## References

* `packaging/launcher/myscribe_launcher.py`: `adopted_data`, `write_pointer_data`, `take_setup_result`.
* `scribe/setup.py`: `_library_door`, `RESULT_VARIABLE`; `scribe/library.py`.
* `docs/superpowers/specs/2026-09-20-installer-design.md`, section 2.
