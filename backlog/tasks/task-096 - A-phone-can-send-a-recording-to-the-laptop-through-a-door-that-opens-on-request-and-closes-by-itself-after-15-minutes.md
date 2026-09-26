---
id: TASK-096
title: >-
  A phone can send a recording to the laptop through a door that opens on
  request and closes by itself after 15 minutes
status: To Do
assignee: []
created_date: '2026-09-26 20:11'
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
