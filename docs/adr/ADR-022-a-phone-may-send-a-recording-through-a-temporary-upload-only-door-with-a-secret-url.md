---
id: "ADR-022"
title: "A phone may send a recording through a temporary upload-only door with a secret URL"
status: "Proposed"
date: "2026-09-26"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-001"
topics:
  - "network"
  - "security"
  - "ingest"
aliases:
  - "receive from phone"
  - "phone door"
  - "myscribe.local"
components:
  - "scribe.web.phone_door"
  - "scribe.web.phone_ui"
symbols:
  - "Door"
  - "lan_address"
  - "ZeroconfPublisher"
  - "file_upload"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-022 A phone may send a recording through a temporary upload-only door with a secret URL

## Status

Proposed, 2026-09-26.

## Status History

```yaml
status_history:
  - date: 2026-09-26
    status: Proposed
    changed_by: Claude (agent, session 2026-09-26)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-26
    status: Proposed
    changed_by: Claude (agent, session 2026-09-26)
    reason: Related to ADR-001
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Robert records on his iPhone and wants the file on the laptop without a cloud
service (2026-09-26). MyScribe cannot take it today, on purpose:

* **The app answers one machine.** It binds `127.0.0.1`
  (`scribe/__main__.py`, `HOST`), has no login, and `scribe/guard.py` refuses
  any `Host` but `127.0.0.1` and `localhost` (`ALLOWED_HOSTS`). README's "One
  user, one machine" says not to put it on a network, because nothing in it
  checks who is asking: every route, trash and purge included, answers
  whoever reaches it.
* **A phone is another machine.** On the same Wi-Fi it can reach the laptop's
  LAN (local area network) address, and nothing of MyScribe listens there.

So a phone can only send a file if something listens on the LAN, and that
something must not be the app.

## Decision Drivers

* Nothing of the library may be reachable from the network, ever.
* No cloud service, no account, no app to install on the phone.
* The phone half has to work in Safari with nothing but the laptop to talk to.

## Considered Options

* A separate upload-only listener, opened from the laptop, closing by itself.
* OneDrive (or another sync service) plus a watch folder.
* The whole app on the LAN, with a pairing code.
* LocalSend, as a separate app on both devices.

## Decision Outcome

Chosen option: **a separate upload-only listener**, decided by Robert on
2026-09-26 with the four options in front of him: no cloud; a door he opens
from the laptop that closes by itself after 15 minutes; upload only, the
phone sees nothing of the library. The reason as this record puts it: the
exception is as small as a network door can be, and it is shut unless a
person on the laptop just opened it.

What it protects: the library. The listener has no route of the app, so
nothing can be read, changed or deleted through it. What it does not
protect: anyone on the same network who sees the QR code or the URL before
the door closes can send a file, which becomes a recording and a queued
transcription. And the upload is plain HTTP: whoever can watch traffic on
that network can read the recording as it passes.

### Confirmation

TASK-096 builds it; `tests/test_phone_door.py` and `tests/test_web_phone.py`
are the proof, with mutants on a copy, and a real run on the laptop. The
phone half, an iPhone scanning the code, is Robert's to confirm.

## Decision Contract

### Must

* Listen only while open: opened by a form post to the app, so behind
  `scribe.guard`, and closed after 15 minutes, on Close, or when the app
  stops. Nothing persists it open.
* Serve its own ASGI (Asynchronous Server Gateway Interface) app on its own port (4243) and the LAN address of
  the default route; no route, static file or template of the app.
* Put a new `secrets.token_urlsafe(32)` in the path for every opening; answer
  404 to any other path before reading a body; compare with
  `hmac.compare_digest`.
* Keep the secret out of every log: the app log (ADR-014 still holds: the log
  observes) and uvicorn's access log, which is off for this listener only.
* Take audio and video only, by the extensions `probe.MEDIA_EXTENSIONS`
  lists, up to 4 GiB counted as the bytes arrive.
* Hand an accepted file to `transcribe_dialog.file_upload`, the helper the
  laptop's upload uses, with the stored defaults and no folder.

### Must Not

* Widen `guard.ALLOWED_HOSTS` or bind the app to anything but `127.0.0.1`.
* Add a firewall rule, or run anything elevated.
* Serve anything to the phone but the upload form and a sentence back.

### Exceptions

* None.

### Verification

* `tests/test_web_phone.py::test_no_route_of_the_main_app_answers_through_the_door`
  walks every route of `create_app()` through the door's port, with and
  without the secret in front.
* `tests/test_phone_door.py::test_the_secret_appears_in_no_log` gives
  `uvicorn.access` a handler, as the app's server does, and reads the app log.
* `tests/test_web_phone.py::test_a_restart_finds_the_door_closed_and_the_old_port_released`.

## Consequences

### Positive

* A recording reaches the library from a phone with no cloud and no app.
* The app's guard, bind address and routes are exactly as they were.

### Negative

* A person on the same network who sees the code within 15 minutes can fill
  the queue; the size limit and the timeout bound it, nothing prevents it.
* Plain HTTP on the LAN: no certificate is possible for a `.local` name or an
  address without a warning Safari would put in front of every upload.
* Windows Firewall decides whether the phone gets through; MyScribe can only
  say so (measured in TASK-096: this laptop's Wi-Fi is filed as Public).

## Pros and Cons of the Options

### A separate upload-only listener (chosen)

* Good, because the library stays unreachable and the door is shut by default.
* Bad, because it is a second listener to keep small, with a firewall prompt.

### OneDrive plus a watch folder

* Good, because the watch folder exists already (`scribe/ingest/watching.py`).
* Bad, because it is a cloud service, which Robert ruled out.

### The whole app on the LAN with a pairing code

* Good, because the phone could see its transcripts.
* Bad, because every route would then answer the network, behind one code.

### LocalSend

* Good, because it needs nothing from MyScribe.
* Bad, because it is a second app on both devices and a folder to watch.

## Open Questions

None.

## Related Decisions

* ADR-001 (one web process): the listener is a thread of that process, it
  loads no model, and hands work to the queue like every other door.
* ADR-014 (the application log observes): the door logs that it opened and
  closed, never the URL.

## References

* README.md, "One user, one machine"; `scribe/guard.py`.
* Backlog TASK-096 (the build, the firewall measurement, the real run).

## Enforcement

One tripwire, not a proof; the tests under Verification are the proof. It
flags an added line in `scribe/` that hands the opening's secret or one of
its URLs to the app log.

```json
{
  "forbid_pattern": [
    {"pattern": "applog\\.log\\(.*\\b(?:secret|ip_url|mdns_url)\\b", "path_glob": "scribe/**", "message": "The door's secret and URLs never reach the app log (ADR-022)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
