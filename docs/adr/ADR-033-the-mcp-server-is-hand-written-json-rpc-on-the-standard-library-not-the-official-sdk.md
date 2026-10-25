---
id: "ADR-033"
title: "The MCP server is hand-written JSON-RPC on the standard library, not the official SDK"
status: "Accepted"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "automation"
  - "mcp"
  - "dependencies"
aliases:
  - "mcp server"
  - "model context protocol"
components:
  - "scribe.mcp_server"
symbols:
  - "serve"
  - "PROTOCOL_VERSIONS"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-033 The MCP server is hand-written JSON-RPC on the standard library, not the official SDK

## Status

Accepted, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-25
    status: Accepted
    changed_by: Robert van den Breemen (said yes in session 2026-10-25; recorded by Claude)
    reason: Robert accepted ADR-033 in the session of 2026-10-25
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Robert decided on 2026-10-24 to build TASK-108 phase 2: an MCP server that
offers the `/api/v1` actions (ADR-025) to an agent over stdio, as a thin
HTTP client like the stdlib CLI (`scribe/cli.py`). What speaks the protocol?
Checked on 2026-10-25:

* A stdio server needs `initialize`, `ping`, `tools/list` and `tools/call`:
  newline-delimited JSON-RPC 2.0.
* The official SDK, `mcp` 2.3.0 (MIT), in a scratch lock (uv 0.12.18) adds 12 packages to `uv.lock` (cryptography, cffi, pywin32, rpds-py,
  jsonschema, pyjwt, sse-starlette, ...): four native, 11.8 MB of Windows wheels.
* 12 SDK advisories since 2025-07, mostly HTTP and OAuth code stdio never runs.
* `fastmcp` 4.1.3 (Apache-2.0) depends on `mcp` and adds more.
* The launcher runs `uv sync --frozen --no-dev` without `--extra`
  (`packaging/launcher/myscribe_launcher.py`, `sync_command`).

## Decision Drivers

* It works in every install with nothing extra, and the frozen environment
  (ADR-011, ADR-012) does not grow for a feature most users never start.

## Considered Options

* **A.** Hand-written stdlib JSON-RPC in `scribe/mcp_server.py`. **B.** The `mcp` SDK.
* **C.** The SDK as an optional extra (`myscribe[mcp]`). **D.** FastMCP.

## Decision Outcome

Chosen option: **A**. B puts 12 packages and 11.8 MB of wheels into every
install for a few hundred lines of protocol. C never reaches the installed
app, because the launcher installs no extras. D is B with more on top.

### Confirmation

`tests/test_mcp_server.py` drives the protocol and every tool against the
real routes, and spawns the entry point. On 2026-10-25 the SDK 2.3.0 client
(scratch venv) got `-32601` for its `server/discover` probe, fell back to
`initialize`, listed the tools and called three.

## Decision Contract

### Must

* `scribe/mcp_server.py` imports only the standard library and `scribe.cli`.
* Every tool is one or a few `/api/v1` requests through `scribe.cli.Api`.
* An unknown method is answered with `-32601`, never with silence.

### Must Not

* Import `mcp`, `fastmcp` or any other protocol library at runtime.
* Open the database or the library; the app stays the only writer (ADR-013).

### Exceptions

* None. (A scratch venv with the SDK, to check interop, is not shipped.)

### Verification

* `tests/test_mcp_server.py`: `test_the_server_loads_nothing_but_the_standard_library`, `test_an_unknown_method_is_method_not_found`.

## Consequences

### Positive

* `python -m scribe.cli mcp` works in every install as shipped; no new advisories or native wheels.

### Negative

* Protocol revisions are ours to add (`PROTOCOL_VERSIONS`). The stateless
  2026-07-28 one is not spoken; if a client stops falling back, revisit B.
* Arguments are checked against a subset of JSON Schema, not all of it.

## Pros and Cons of the Options

* A: good, nothing to install; bad, protocol upkeep is ours.
* B: good, revisions come with an upgrade; bad, 12 packages for stdio.
* C: good, non-users carry nothing; bad, installs get no extras.
* D: good, shortest tool definitions; bad, heavier than B.

## Open Questions

- [x] Hand-written JSON-RPC (A) or the SDK (B)? Robert chose A on 2026-10-25.

## Related Decisions

* ADR-025 (the JSON API these tools call); ADR-013 (one writer: why it is an HTTP client).

## References

* TASK-108; `scribe/mcp_server.py`; <https://pypi.org/project/mcp/2.3.0/>, <https://github.com/modelcontextprotocol/python-sdk/security/advisories>;
  `mcp_types/version.py` in the `mcp-types` 2.3.0 wheel (protocol revisions).

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "^\\s*(?:import|from)\\s+(?:mcp|fastmcp)\\b", "path_glob": "scribe/**", "message": "The MCP server is stdlib JSON-RPC, not an SDK (ADR-033)."},
    {"pattern": "^\\s*(?:import|from)\\s+(?:sqlite3|scribe\\.db)\\b|^\\s*from\\s+scribe\\s+import\\s+.*\\bdb\\b", "path_glob": "scribe/mcp_server.py", "message": "The MCP server is a client of /api/v1; the app is the only writer (ADR-033, ADR-013)."}
  ],
  "forbid_pattern": [],
  "require_pattern": []
}
```
