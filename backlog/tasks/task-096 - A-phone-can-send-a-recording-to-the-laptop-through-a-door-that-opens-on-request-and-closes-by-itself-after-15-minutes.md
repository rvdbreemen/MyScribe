---
id: TASK-096
title: >-
  A phone can send a recording to the laptop through a door that opens on
  request and closes by itself after 15 minutes
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-26 20:11'
updated_date: '2026-09-26 20:18'
labels:
  - web
  - ingest
  - security
  - network
dependencies: []
priority: medium
ordinal: 170000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert wants to move a recording from his iPhone to the laptop without any cloud service (2026-09-26). MyScribe binds 127.0.0.1, has no login and refuses any Host but localhost (scribe/guard.py, README 'One user, one machine'), so a phone cannot reach it, and it must stay that way. Decided with Robert: a separate upload-only listener that a person opens from the laptop ('Receive from phone'), reachable on the local network through a one-time secret URL shown as a QR code (myscribe.local via mDNS, with the IP as fallback), accepting audio and video files only, feeding the normal ingest path, and closing by itself after 15 minutes or when closed by hand. The phone sees an upload page and a 'received' answer, nothing of the library. Recording in the phone's browser is out of scope: Safari refuses the microphone without https. This deliberately makes an exception to 'one user, one machine', so a Proposed ADR (ADR-022) records it and Robert accepts it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The main app is unchanged: still 127.0.0.1 only, the Host guard still refuses every other name, and a test proves no route of the main app is reachable through the new listener
- [ ] #2 Nothing listens on the network until a person opens the door from the laptop; after a restart the door is closed; a test proves both
- [ ] #3 The door closes by itself after 15 minutes and on request; after closing the port refuses connections. A test with a controllable clock proves the timeout without sleeping
- [ ] #4 Without the exact secret in the path every request is a 404 and writes nothing; the secret is long and random (secrets module), new per opening, never logged; one test per refusal
- [ ] #5 Only audio and video files are accepted, with a size limit; anything else is refused with a sentence; an accepted file goes through the same ingest path as an upload on the laptop and is transcribed like one
- [ ] #6 The laptop shows the QR code, the URL, the IP fallback and a countdown, and a close button; the phone page is plain HTML that works in Safari on an iPhone without JavaScript frameworks or anything from the internet
- [ ] #7 myscribe.local is published over mDNS while the door is open and withdrawn when it closes; if publishing fails the IP URL still works and the page says so
- [ ] #8 Whatever Windows Firewall needs for a phone to connect is stated from a measurement on this machine, not assumed; MyScribe never adds a firewall rule that outlives the door without saying so
- [ ] #9 Red first for every behaviour, mutants on a copy, the per-file suite green, and a real run: a phone (or a second device on the LAN) sends a file, it becomes a recording, and the door closes on time. The phone half is Robert's
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Dependencies: segno and zeroconf via the pinned uv (uv add --no-sync, == pins, uv lock, uv lock --check); install only the new wheels into the shared venv with the pinned uv's pip. Show the uv.lock diff stat.
2. scribe/phone_door.py: the door. Own tiny Starlette app served by uvicorn on a thread, on a socket bound here (LAN address, port 4243; tests bind 127.0.0.1 port 0). Secret secrets.token_urlsafe(32) per opening, compared with hmac.compare_digest; anything else 404 before the body is read. Deadline from an injectable clock; a watchdog thread closes the door at 15 minutes; requests after the deadline 404. Access log off. Size limit on bytes received as well as Content-Length; audio/video by probe.MEDIA_EXTENSIONS. Seams: LAN-address picker (UDP connect to the default route, nothing sent), mDNS publisher (zeroconf), listener clock.
3. scribe/web/transcribe_dialog.py: factor the per-file ingest+enqueue of the laptop upload into one helper; both doors call it. The phone door passes the stored defaults, Uncategorized, and does not save defaults.
4. scribe/web/phone_ui.py + templates: GET /phone (dialog or page), POST /phone/open, POST /phone/close, GET /phone/countdown; all behind the existing guard. Panel: QR (segno inline SVG), myscribe.local URL, IP URL, countdown, Close, mDNS status, firewall sentence, received files. Button in the library toolbar next to Record.
5. scribe/app.py lifespan closes the door; nothing persists it open.
6. Tests red first per behaviour (tests/test_phone_door.py, tests/test_web_phone.py), real HTTP client against 127.0.0.1; mutants on a copy.
7. Firewall measurement on this machine (read-only checks, then one listener on the LAN address), no rule added, nothing elevated.
8. Real run on port 4299 with a fenced data dir and a short door timeout; upload from a second process with and without the secret; see the media row; see the door close.
9. ADR-022 Proposed; README one paragraph; CHANGELOG [Unreleased]; implementation notes.
<!-- SECTION:PLAN:END -->
