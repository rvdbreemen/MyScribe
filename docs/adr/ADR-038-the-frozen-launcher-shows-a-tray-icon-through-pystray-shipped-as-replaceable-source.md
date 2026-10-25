---
id: "ADR-038"
title: "The frozen launcher shows a tray icon through pystray, shipped as replaceable source"
status: "Proposed"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-011"
topics:
  - "packaging"
  - "launcher"
  - "background"
  - "licensing"
aliases:
  - "tray icon"
  - "system tray"
  - "pystray"
  - "status item"
components:
  - "packaging.launcher"
  - "packaging/launcher/hooks"
symbols:
  - "Tray"
  - "tray_for"
  - "xembed_tray_present"
  - "replaceable_tray"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-038 The frozen launcher shows a tray icon through pystray, shipped as replaceable source

## Status

Proposed, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Initial proposal (TASK-112.11), on Robert's choice of 2026-10-24 in TASK-112.08 (tray option B)
    changed_via: adr-kit
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: ADR-038 amends ADR-011's standard-library-only rule for the frozen launcher's tray (TASK-112.11)
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

A login entry starts the launcher headless on all three OSes (ADR-026, `scribe/autostart.py`). Nothing on screen then says MyScribe runs, and stopping it takes Settings or `--stop`. Robert chose a tray icon with Open MyScribe and Stop, through pystray, in the frozen launcher only (TASK-112.08, 2026-10-24). ADR-011 keeps the launcher standard-library only, because it runs before the environment exists. pystray needs Pillow and, per OS, a backend: ctypes on Windows, pyobjc on macOS, python-xlib (or PyGObject for AppIndicator) on Linux. It is LGPL-3.0, and its last release is 0.19.5 of 2023-09-17.

## Decision Drivers

* The icon must not cost the launcher its start: no dependency may be needed by the doors that run before or without it.
* What ships frozen must meet the LGPL, and must have no open advisory on the day it is pinned.

## Considered Options

Checked on 2026-10-25 against PyPI's JSON API and OSV (osv.dev).

* **A.** pystray 0.19.5 (LGPL-3.0): one API on all three OSes, no advisories, repository last pushed 2025-03-27. It needs six 1.17.0 and Pillow, pinned at 13.0.0 (MIT-CMU, the first release with the eight advisories of 2026-10-22/23 fixed); python-xlib 0.33 (LGPL-2.1-or-later) on Linux; pyobjc 12.2.2 (MIT, universal2 wheels) on macOS.
* **B.** A hand-written `Shell_NotifyIconW` over ctypes. It stays standard library, but covers Windows only.
* **C.** infi.systray 0.1.12.1 (BSD). Windows only; its code is from 2019.
* **D.** Qt's QSystemTrayIcon (PySide6-Essentials 6.12.0, LGPL-3.0, 59 to 110 MB wheels), or **E.** wxPython 4.3.2 TaskBarIcon (16 to 19 MB wheels, none for manylinux on PyPI).
* **F.** No tray. The Stop button in Settings stays the only visible stop.

## Decision Outcome

Chosen option: **A**. It is the only candidate that covers all three OSes at a few MB. B and C cover Windows alone. D and E would multiply the launcher's size, and E has no Linux wheel. F is what Robert chose against.

A measured cost: the Windows onedir build went from 33 MB to 47 MB, of which Pillow is 13 MB.

### Confirmation

On Windows, from source and frozen (port 4540, a scratch home): the menu shows, Open MyScribe asks the browser for the URL, and Stop MyScribe logs `app.stop_requested`, exits 0 and removes the icon. `--stop` exits 0; a killed app exits 70. macOS and Linux get their own walks.

## Decision Contract

### Must

* Only the headless door shows the icon, after the app answers `/health`. The window keeps its own Open and Quit.
* pystray, Pillow, python-xlib and pyobjc are imported inside functions only. When any of them fails, for any exception, the door serves without a tray and writes one `No tray icon (...)` line to launcher.log.
* Stop in the menu only sets a flag. The headless loop then calls `Launch.stop`: the stop route first (ADR-028, ADR-032), the hard stop after. A stop asked from the tray exits 0, and a dead app still exits `APP_DIED`.
* The icon runs on a thread of its own on Windows and Linux, so Python's signal handlers keep the main thread.
* On macOS the icon takes the main thread. It takes SIGINT and SIGTERM back through `PyObjCTools.MachSignals` as a stop, because pystray's own SIGINT handler calls `NSApp.terminate_`, which exits without stopping the app. It sets the accessory activation policy, so the Dock shows no icon.
* On Linux, the xorg backend runs only when an X11 system tray owns `_NET_SYSTEM_TRAY_S<n>`; otherwise the door serves with no tray and logs that line.
* The dependencies live in `packaging/build-requirements.in` and its hashed `.txt` only, never in the app's `uv.lock`.
* The hooks in `packaging/launcher/hooks` collect pystray and python-xlib as loose `.py` files (`module_collection_mode = "py"`), with their dist-info and licence texts. `build_release.replaceable_tray` fails the build when pystray's source or COPYING.LGPL is missing.

### Must Not

* Import a tray module at the top of `myscribe_launcher.py`.
* Stop the app from inside a menu callback.

### Exceptions

* The frozen Linux AppImage carries no PyGObject, so it gets the xorg backend only. On GNOME without an extension that hosts legacy tray icons it has no tray (unverified on a real desktop).

### Verification

* `tests/test_launcher.py`, the tray tests from `test_the_tray_menu_offers_open_myscribe_and_stop` on; `tests/test_build_release.py`, the `replaceable_tray`, freeze and hook tests.

## Consequences

### Positive

* A MyScribe started at login is visible, and can be opened and stopped from the tray without a terminal.

### Negative

* ADR-011's standard-library-only rule now has one exception: an LGPL dependency from 2023 and its backends, in the frozen builds only.
* Fourteen MB on Windows, and Pillow to watch for advisories. The macOS path is unit-tested but has not run.
* Licence, as read by the agent (not legal advice): LGPL-3.0 section 4 asks for a notice that pystray is used, the GPL and LGPL texts, and its source or a replaceable copy. Loose `.py` files in `_internal` are both; on macOS a swapped file breaks the ad-hoc signature until it is re-signed.

## Open Questions

- [ ] Is the notice in README's License section and the shipped dist-info enough as the prominent notice LGPL-3.0 4(a) asks, or does the installer need a third-party notices page?

## Related Decisions

* ADR-011: amended. Its standard-library-only rule gains this one exception for the frozen builds; its enforcement pattern is unchanged. ADR-026 makes the login entry this icon is for; Stop uses ADR-028's and ADR-032's stop.

## References

* Backlog TASK-112.11 and TASK-112.08. `packaging/launcher/myscribe_launcher.py`, `packaging/build_release.py`, `packaging/launcher/hooks/`, `packaging/build-requirements.in`.
* https://pypi.org/project/pystray/0.19.5/, https://github.com/moses-palmer/pystray, https://www.gnu.org/licenses/lgpl-3.0.html, https://osv.dev/list?ecosystem=PyPI&q=pillow

## Enforcement

```json
{
  "forbid_pattern": [
    {"pattern": "^(import|from)\\s+(pystray|PIL|Xlib|AppKit|PyObjCTools)\\b", "path_glob": "packaging/launcher/*.py", "message": "Tray modules are imported inside a function: the launcher must start without them (ADR-038)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
