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

Proposed, 2026-10-06.

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

What exists, checked on 2026-10-06:

* 80 of the 90 route decorators in `scribe/` carry `include_in_schema=False`.
* The ten others are `/health` (`scribe/app.py:302`), `POST /api/media` (`:318`),
  six job routes (`:339`-`:462`) and the job log stream (`scribe/web/jobs_ui.py:534`).
* The pages post forms to those job routes (`scribe/templates/_macros.html:95`);
  `priority` and `move` take form fields (`scribe/app.py:421`, `:445`). Errors are JSON `{"detail": ...}`, without a code.
* `FastAPI(...)` (`scribe/app.py:300`) keeps the default `/openapi.json` and `/docs`.
* `POST /api/media` queues a new job even for known content (`scribe/app.py:320`):
  a client that retries pays the GPU (graphics processing unit) work twice.
* `scribe.guard` passes a request with neither `Origin` nor `Sec-Fetch-Site`
  (`cross_site_reason`), so any local process can already drive every route.

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

Not decided; Robert decides. Recommended: **B**, because the token in A
stops no one the guard does not already stop. Every process the user runs can
read a 0600 file, and another account on the machine could still use the form
routes. A token for every mutating route is a separate decision, best taken
once.

### Confirmation

A contract test per endpoint pins its shape and error codes. The door walk
(`tests/test_web_phone.py:283`) iterates every route of `create_app()`, so it
covers new routes unchanged.

## Decision Contract

For the recommended option.

### Must

* Each `/api/v1` route calls the same helper its HTML route calls; neither holds logic of its own.
* Errors are JSON with a stable `code` and the `detail` the pages already show.
* `POST /api/v1/media` honours an idempotency key, so a retried request queues no second job.
* The routes appear in `/openapi.json`.

### Must Not

* Bind anything but `127.0.0.1`, or widen `guard.ALLOWED_HOSTS`.
* Serve an `/api` route through the phone door (ADR-022).
* Change an HTML route's path or form fields.

### Exceptions

* None.

### Verification

* `tests/test_web_phone.py::test_no_route_of_the_main_app_answers_through_the_door`.

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

- [ ] Which option: A, B, C or D?
- [ ] If a token: does it guard every mutating route, or only `/api/v1`?
- [ ] Are phases 1 and 2 (the `myscribe` command and the agent tool server) wanted, which decides whether phase 0 is worth its upkeep?

## Related Decisions

* ADR-001 (one web process): the API runs inside it.
* ADR-013 (SQLite coordination): what option C's second writer relies on.
* ADR-014 (the log observes): a token, if any, stays out of it.
* ADR-015 (setup behind a JSON contract): one engine, thin front-ends.
* ADR-022 (the phone door): its walk refuses every new route.

## References

* Backlog TASK-108 (Hermes's proposal, 2026-10-05).
* `scribe/app.py:300-472`, `scribe/guard.py`, `scribe/web/jobs_ui.py:534`.

Enforcement: none yet; the rules depend on the option chosen.
