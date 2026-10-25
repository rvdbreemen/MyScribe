---
id: "ADR-036"
title: "The library folder is asked apart from the engine home, as a request the launcher settles and the engine adopts"
status: "Proposed"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-019"
topics:
  - "installer"
  - "library"
aliases:
  - "library_request"
  - "library page"
  - "/LIBRARY="
components:
  - "packaging.launcher"
  - "packaging.windows"
symbols:
  - "locate_library"
  - "judge_library"
  - "WriteLibraryRequest"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-036 The library folder is asked apart from the engine home, as a request the launcher settles and the engine adopts

## Status

Proposed, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: ADR-036 amends ADR-019 for a new, empty library
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

TASK-097: the library (`myscribe.db`, media, models, work, the app's log) is
chosen at install, apart from the program. The first start asked where
*everything* goes; ADR-019 moves a library only by adopting one after the
download; Inno cannot read SQLite. Robert chose option A on 2026-10-24.

## Decision Drivers

* An update never swaps a library for an empty one; the default is today's layout.
* Only the engine adopts, numbers first (ADR-019); the launcher stays stdlib (ADR-011).

## Considered Options

* A request in the pointer file, settled by the launcher, adopted by the engine.
* No installer page: no Pascal, but no page on Windows either.
* The installer writes `"data"`: breaks ADR-019; Inno cannot show the numbers.

## Decision Outcome

Chosen option: **a request in the pointer file**, the only one that gives
Windows a page and keeps adoption in the engine. `MyScribe.location` gains
`"library_request"`, written by the installer's page (or `/LIBRARY=`), which
opens on `%LOCALAPPDATA%\MyScribe`, or by the first start's library question
where there is no page.

Before anything is downloaded, `locate_library` settles the request:

* **The default home, or its `data`.** Today's layout; no `"data"` is written.
* **An empty or new folder.** Made, written as `"data"`, request removed.
* **A folder with other files.** Its `MyScribe` subfolder is judged instead.
* **A library** (`myscribe.db` or `scribe.db` in it or in its `data\`, as in a
  former home). Handed to the first sitting with `--library`; the engine shows
  its numbers and adopts it only when chosen. A decision removes the request.
* **Anything else.** Refused with a sentence: asked again in a window, a stop
  without one.

While `<home>\data` holds a library, a request is dropped with a sentence.

This amends ADR-019's first Must for one case: the launcher writes `"data"` for
a new, empty library it made. Existing libraries stay the engine's.

### Confirmation

`tests/test_launcher_library_request.py` drives every case through `main`;
`tests/test_installer_library_page.py` reads the `.iss` (page, skip rule, key,
ASCII JSON, written before the launcher starts); ISCC compiles the Pascal.

## Decision Contract

### Must

* The installer writes `"library_request"` only with no `MyScribe.location` and
  no `{localappdata}\MyScribe\env`; uninstall deletes neither library nor pointer.
* The default home, or its `data`, as a request means `<home>/data`.
* A folder holding `myscribe.db` or `scribe.db` is adopted only by the engine.
* A request is never used while `<home>/data` holds a library.

### Must Not

* Let the launcher open, migrate or move a library.
* Ask at a first start where the engine home goes.

### Exceptions

* Under `--home` or `MYSCRIBE_HOME` a request is not read (as in ADR-019).

### Verification

* `tests/test_launcher_library_request.py` and `tests/test_installer_library_page.py`.

## Consequences

### Positive

* A first install can put the library on another disk; an update keeps it. A
  former home such as `D:\Data\MyScribe` is recognised by its `data\`.

### Negative

* An existing library chosen at install is used only after the engine download.
* Where the engine goes is no longer asked: a small C: needs `--home`.
* `launcher.log` and the stdout `app.log` stay in the home; `scribe.applog`'s log moves.

## Open Questions

- [ ] Should a door without a window stop on a request it cannot use, rather than start on `<home>/data`? Stopping matches ADR-019's rule for a missing library. Robert decides.

## Related Decisions

* ADR-019, amended for a new, empty library only. ADR-015 and ADR-011 hold.

## References

* `packaging/launcher/myscribe_launcher.py`, `packaging/windows/myscribe.iss`.
