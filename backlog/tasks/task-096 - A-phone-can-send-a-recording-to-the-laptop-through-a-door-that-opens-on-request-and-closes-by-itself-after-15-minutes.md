---
id: TASK-096
title: >-
  A phone can send a recording to the laptop through a door that opens on
  request and closes by itself after 15 minutes
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-26 20:11'
updated_date: '2026-09-27 07:32'
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
- [x] #1 The main app is unchanged: still 127.0.0.1 only, the Host guard still refuses every other name, and a test proves no route of the main app is reachable through the new listener
- [x] #2 Nothing listens on the network until a person opens the door from the laptop; after a restart the door is closed; a test proves both
- [x] #3 The door closes by itself after 15 minutes and on request; after closing the port refuses connections. A test with a controllable clock proves the timeout without sleeping
- [x] #4 Without the exact secret in the path every request is a 404 and writes nothing; the secret is long and random (secrets module), new per opening, never logged; one test per refusal
- [x] #5 Only audio and video files are accepted, with a size limit; anything else is refused with a sentence; an accepted file goes through the same ingest path as an upload on the laptop and is transcribed like one
- [x] #6 The laptop shows the QR code, the URL, the IP fallback and a countdown, and a close button; the phone page is plain HTML that works in Safari on an iPhone without JavaScript frameworks or anything from the internet
- [x] #7 myscribe.local is published over mDNS while the door is open and withdrawn when it closes; if publishing fails the IP URL still works and the page says so
- [x] #8 Whatever Windows Firewall needs for a phone to connect is stated from a measurement on this machine, not assumed; MyScribe never adds a firewall rule that outlives the door without saying so
- [ ] #9 Red first for every behaviour, mutants on a copy, the per-file suite green, and a real run: a phone (or a second device on the LAN) sends a file, it becomes a recording, and the door closes on time. The phone half is Robert's
- [ ] #10 The phone page shows a progress bar from 0 to 100% while the file uploads, then the laptop's answer for 3 seconds, then a done screen (Safari does not let a page close a tab it did not open, so the page tries and otherwise says the tab can be closed); without JavaScript the plain form still works (Robert, 2026-09-26)
- [x] #11 The door closes by itself once the answer to an accepted file has been sent; a refused file leaves it open for another try
- [x] #12 When the door closed on a received file, the laptop panel says so for 3 seconds, naming the file and whether its transcription is running or queued, and then the library reloads and shows the recording
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

10. Robert's request 2026-09-26 after the first iPhone try: drop the accept filter (Safari greyed out his audio file); progress bar with inline vanilla JS on the phone page (XHR upload progress), answer 3 s, done screen; door closes after an accepted file; laptop panel shows received + job status 3 s, then HX-Refresh of the library. Tests red first, mutants, headless-browser run of the progress bar.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26/27, evidence (scratch copies in the session scratchpad; the numbers are here):

- Firewall (AC8), measured read-only on this laptop: Wi-Fi "Koekie 3" is filed Public; all three profiles on; C:\Python312\python.exe (the process that owns the socket, not the venv launcher) already has inbound Allow rules for Private and Public, TCP and UDP, so a listener on 192.168.1.234:4243 raised no prompt and answered. No rule added, nothing elevated. The panel's firewall sentence says this.
- Real run on 4299, fenced data dir, door shortened to 60 s: 4243 refused before opening; wrong/missing secret and /health through the door 404; notes.txt 415; a .wav became a media row with a queued job on the stored defaults; myscribe.local resolved to 192.168.1.234; the secret was in neither app.log nor the console. The first run found a race (the countdown said closed while the port still accepted for up to a second); close_if_due fixed it, red first.
- Graceful stop (Ctrl+Break, real zeroconf): with the door open the app exited in 0.62 s, 4243 and 4299 refused, app.log phone.close reason "app stopped", no mdns_withdraw_failed. With the door already timed out: exit in 0.17 s, same checks. Exit code 3 is uvicorn re-raising SIGBREAK after "Application shutdown complete".
- Robert's first iPhone try: Safari greyed out his audio file under accept="audio/*,video/*". The filter is gone; the door's extension check (probe.MEDIA_EXTENSIONS) stays the guard.
- Robert's request after that (AC10-12): progress bar, answer 3 s, done screen; the door closes after an accepted file; the laptop panel names the file and its job status for 3 s, then HX-Refresh reloads the library. Measured in headless Chrome (390 px phone tab, upload throttled to 2 MB/s, 10.1 MB wav): 0% 1% 3% ... 98% 100%, answer 5.36 s after Send, done screen 3.01 s after the answer; laptop panel "Received ... Transcription is queued" (no supervisor in that run), /phone/done, GET /, dialog closed, the row listed. Safari itself was not measured: a page cannot close a tab it did not open, so the done screen says the tab can be closed.
- Found while testing AC11: close() returned at once when another thread was already closing, so phone.close landed in the next test's log. close() now waits until the door is closed; red first.
- Suite: per-file run on the rebased branch before AC10-12, 94 files green, 3531 passed, 0 failed (test_llm_live: 7 deselected, exit 5). After AC10-12: tests/test_phone_door.py 61 passed (three runs), tests/test_web_phone.py 33 passed. The whole suite after AC10-12 runs in CI, not here: the machine had 3.2 of 28 GB free.
- Mutants on a copy. Round 1 (before AC10-12), 23 mutants: 22 killed; content-length-not-checked survived, and a test that sends only headers with Content-Length over the limit and wants 413 within 5 s now kills it. Round 2: content-length-not-checked, door-stays-open-after-file, refused-file-closes-too, close-does-not-wait, reload-on-every-panel killed; no-hx-refresh, running-said-as-queued, timed-out-door-reloads, accept-filter-back not run (the harness stopped the run for low memory).
- Not done: the phone half of AC9 and Safari for AC6/AC10 (Robert's iPhone). ADR-022 stays Proposed until Robert accepts it.

2026-09-27, Robert's iPhone run (read from MyScribe-wt-096/data/logs/app.log; code of commit f115637 with the accept filter removed, before the progress bar): phone.open 23:34:47 on 192.168.1.234:4243 with mDNS; ingest.phone 23:35:33 "Audio Terrein sessie ..m4a", 252,638,391 bytes; closed on the laptop 23:35:55; job 1 transcribed, runner.exited status done after 1146 s. So Safari on an iPhone sends a real recording through the door and it is transcribed like a laptop upload (AC6; the phone half of AC9). The progress bar (AC10) has not been seen in Safari yet.
One line in that log came from a pytest process (pid 23224, no proc, 23:53:39, phone.close reason received, 0 files): the close race fixed in 5a19140 let a test's closing thread log after the test had restored paths.LOGS_DIR, whose default is the checkout's data dir. Five phone-test runs after the fix wrote nothing there. Tests that do not fence LOGS_DIR themselves still default to the checkout's data dir; that is wider than this task.
<!-- SECTION:NOTES:END -->
