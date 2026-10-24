---
id: "ADR-025"
title: "How an outside program drives MyScribe without the web pages"
status: "Proposed"
date: "2026-10-06"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "automation"
  - "api"
  - "security"
aliases:
  - "json api"
  - "api v1"
  - "automation surface"
  - "bearer token"
components:
  - "scribe.app"
  - "scribe.guard"
symbols:
  - "create_app"
  - "cross_site_reason"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-025 How an outside program drives MyScribe without the web pages

## Status

Proposed, 2026-10-06. Option B chosen by Robert on 2026-10-24 and built
(TASK-108 phase 0); not yet accepted.

## Status History

```yaml
status_history:
  - date: 2026-10-06
    status: Proposed
    changed_by: Claude (agent, session 2026-10-06)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

Hermes, the agent that tested the macOS build for Jim, drove a test walk with
curl against the HTML (HyperText Markup Language) forms and read source for
field names (TASK-108). It proposes, as phase 0, versioned JSON (JavaScript
Object Notation) twins under `/api/v1/*` with a bearer token, coded errors and
idempotency keys. Phases 1 and 2 (a `myscribe` command, an agent tool server)
would be thin clients on it; they are not decided here.

What existed before `/api/v1`, checked on 2026-10-24:

* 81 of the 92 route decorators in `scribe/` carry `include_in_schema=False`.
* The others are `/health`, `POST /api/shutdown` (ADR-028), `POST /api/media`,
  six job routes in `scribe/app.py` and the job log stream (`scribe/web/jobs_ui.py:557`).
* The pages post forms to those job routes (`scribe/templates/_macros.html:95`);
  `priority` and `move` take form fields. Errors are JSON `{"detail": ...}`, without a code.
* `FastAPI(...)` in `create_app` keeps the default `/openapi.json` and `/docs`.
* `POST /api/media` queues a new job even for known content:
  a client that retries pays the GPU (graphics processing unit) work twice.
* `scribe.guard` passes a request with neither `Origin` nor `Sec-Fetch-Site`
  (`cross_site_reason`), so any local process can already drive every route,
  `POST /api/shutdown` included.

The question is whether to finish and version the surface that half exists.

## Decision Drivers

* What the pages can do, a script can do without reading source.
* Nothing of the library reachable from the network (ADR-022).
* Every action keeps one implementation, whichever door calls it.

## Considered Options

* **A.** Versioned JSON twins under `/api/v1/*` with a bearer token stored in the data folder (mode 0600), as TASK-108 proposes.
* **B.** The same versioned twins without a token, behind the loopback bind and `scribe.guard` as the pages are.
* **C.** No API (application programming interface): a command that opens the database and library code in its own process.
* **D.** Do nothing now.

## Decision Outcome

Chosen: **B** (Robert, 2026-10-24), as recommended, because the token in A
stops no one the guard does not already stop. Every process the user runs can
read a 0600 file, and another account on the machine could still use the form
routes. A token for every mutating route is a separate decision, best taken
once.

### Confirmation

`tests/test_api_v1.py` pins each endpoint's shape and error codes against
the page or route it twins, and walks every `/api/v1` path through the phone
door. The guard's own refusals answer before any route, in their own shape:
a plain-text 400 for a foreign Host, the guard's JSON 403 for another site.

## Decision Contract

For option B, as built in `scribe/api_v1.py`.

### Must

* Each `/api/v1` route calls the same helper its HTML route calls; neither holds business logic of its own (the Exceptions name what a route adds).
* Errors under `/api/v1` are JSON with a stable `code` and the `detail` the pages already show, an unhandled exception included (`internal_error`, without its text); other paths keep `{"detail": ...}` and the plain-text 500.
* `POST /api/v1/media` honours an idempotency key, kept in the database, so a retried request queues no second job; the key compares content and options, and is decided after hashing and before filing, so a refused reuse or a replay files nothing. A stored answer whose job is gone is forgotten, and the same key while its first request runs is a 409 `idempotency_key_in_progress`.
* The routes appear in `/openapi.json`.

### Must Not

* Bind anything but `127.0.0.1`, or widen `guard.ALLOWED_HOSTS`.
* Serve an `/api` route through the phone door (ADR-022).
* Change an HTML route's path or form fields.

### Exceptions

* The guard's refusals answer before any route, in their own shape (see Confirmation).
* `GET /api/v1/settings` assembles its answer from the settings helpers through an allowlist: no page returns this JSON, and the `setting` table holds keys and tokens.
* `POST /api/v1/media/{id}/transcribe` refuses a body field outside the dialog's options and `params` (400), as `_ingest_options` refuses a foreign params key; an empty body repeats the last request.

### Verification

* `tests/test_web_phone.py::test_no_route_of_the_main_app_answers_through_the_door`.
* `tests/test_api_v1.py`.

## Consequences

### Positive

* An agent, a script or a later `myscribe` command drives MyScribe from a published schema.
* Phases 1 and 2 become thin clients, like the setup front-ends of ADR-015.

### Negative

* A second surface to keep in step: each change touches two routes and a contract test.
* `/api/v1/jobs` beside the unversioned `/api/jobs` the pages call: two paths, or the templates move.
* Same-user processes keep full control, as today.

## Pros and Cons of the Options

### A

* Good, because a token is the precondition for revoking a client or ever leaving the loopback.
* Bad, because guarding `/api/v1` alone protects nothing the form routes leave open.

### B

* Good, because it adds a schema and nothing to the threat model.
* Bad, because it adds no protection either.

### C

* Good, because no network surface is added.
* Bad, because `db.LOCK` is in-process only (`scribe/db.py:17`, ADR-013), and what the running app holds in memory, such as the phone door, is out of reach.

### D

* Good, because nothing new to maintain.
* Bad, because the next tester reads source again.

## Open Questions

- [x] Which option: A, B, C or D? B (Robert, 2026-10-24).
- [x] If a token: does it guard every mutating route, or only `/api/v1`? No token.
- [x] Are phases 1 and 2 (the `myscribe` command and the agent tool server) wanted? Yes, both on this API.

## Related Decisions

* ADR-001 (one web process): the API runs inside it.
* ADR-013 (SQLite coordination): what option C's second writer relies on.
* ADR-014 (the log observes): a token, if any, stays out of it.
* ADR-015 (setup behind a JSON contract): one engine, thin front-ends.
* ADR-022 (the phone door): its walk refuses every new route.

## References

* Backlog TASK-108 (Hermes's proposal, 2026-10-05).
* `scribe/api_v1.py`, `scribe/idempotency.py`, `scribe/app.py`, `scribe/guard.py`.

Enforcement: tests/test_api_v1.py and the door walk; no gate until accepted.
