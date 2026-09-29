---
id: "ADR-024"
title: "A recording pinned private may go to an Ollama on the local network"
status: "Accepted"
date: "2026-09-29"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "privacy"
  - "llm"
  - "ollama"
aliases:
  - "ollama on the network"
  - "ollama address"
  - "local network privacy"
components:
  - "scribe.llm.ollama"
symbols:
  - "_local_network_only"
  - "normalise_host"
  - "SETTING_HOST"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-024 A recording pinned private may go to an Ollama on the local network

## Status

Accepted, 2026-09-29.

## Status History

```yaml
status_history:
  - date: 2026-09-28
    status: Proposed
    changed_by: Claude (agent, session 2026-09-28)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-29
    status: Accepted
    changed_by: Robert van den Breemen (via Claude, session 2026-09-29)
    reason: "Robert accepted in the session: Accept ADR 024"
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

A recording pinned private may only be sent to a provider whose class says
`is_local = True` (`scribe/llm/privacy.py`), and that is Ollama. Until
2026-09-28 "local" meant this computer: `scribe/llm/ollama.py` refused any
host but loopback (`_this_machine_only`), and no setting could name another.
Robert asked for Ollama on another machine in the local network - a GPU box or
a NAS - to be usable from the AI settings (TASK-100). The pin reads the
provider class, never an instance, so the host an instance talks to decides
what "local" really means.

## Decision Drivers

* A private recording must never reach the internet.
* An Ollama on a machine in the house should be usable for every recording, private ones included.
* One setting, and no second Ollama provider to explain.

## Considered Options

* **A.** Trust the local network: Ollama may run on this machine or on a local-network address, and the pin allows it.
* **B.** A separate "Ollama on the network" provider with `is_local = False`, so private recordings are refused there.
* **C.** Keep Ollama on this machine only.

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-09-28 with A and B in front of
him ("trust my network"), together with an address field on the existing
Ollama provider. The privacy promise becomes "never leaves your local
network". What bounds it is the host check, not the pin: `_local_network_only`
refuses every address outside this machine and the local network, so a
settings row cannot point a private transcript at the internet.

### Confirmation

`tests/test_llm_ollama.py` pins the accepted and refused addresses and the
saved host; `tests/test_web_ai.py` pins the settings field. A real run on
2026-09-28 sent the provider test through this laptop's LAN address to a real
Ollama (TASK-100).

## Decision Contract

### Must

* Accept only loopback, private, link-local and 100.64.0.0/10 addresses, and local names: single-label, `.local`, `.lan`, `.internal`, `.home.arpa`.
* Refuse any other address with a sentence, and save nothing when the settings form carries one.
* Read the saved address (`llm_ollama_host`) in `OllamaProvider.__init__`, so every Ollama call uses it; an empty field means this computer.
* Say "never leaves your local network" wherever the product describes where a private recording may go.

### Must Not

* Accept a public IP address or an internet domain name for Ollama.
* Fall back silently to this computer when a saved address is refused.
* Change the Ollama install offer, which stays about this computer (`ollama_setup.state`).

### Exceptions

* None.

### Verification

* `tests/test_llm_ollama.py::test_a_host_outside_the_local_network_cannot_be_constructed`
* `tests/test_llm_ollama.py::test_a_host_on_the_local_network_is_accepted`
* `tests/test_web_ai.py::test_an_ollama_address_on_the_internet_is_refused_and_not_saved`

## Consequences

### Positive

* A stronger machine in the house can answer for every recording, private ones included.
* The internet stays out of reach for a private recording: the check refuses it.

### Negative

* A private transcript can now cross the local network, over plain HTTP. Anyone who can read traffic on that network can read it.
* A name like `nas` or `gpu-box.local` is trusted by its form; if the local DNS resolves it to the internet, the check does not see that.

## Pros and Cons of the Options

### A

* Good, because an Ollama in the house is usable for every recording with one setting.
* Bad, because "private" now includes the local network, over HTTP.

### B

* Good, because "private" keeps meaning this computer.
* Bad, because private recordings could not use the stronger machine, and two Ollama entries need explaining.

### C

* Good, because nothing changes.
* Bad, because it does not do what Robert asked.

## Open Questions

- [x] Does Robert accept this record, and with it that a private recording may cross the local network over HTTP? — **Answered 2026-09-29 by Claude (agent, session 2026-09-29) for Robert van den Breemen:** Yes: Robert accepted it on 2026-09-29, after 0.8.0 shipped the feature.

## Related Decisions

* None.

## References

* `scribe/llm/ollama.py` (`_local_network_only`, `normalise_host`, `OllamaProvider.__init__`).
* `scribe/llm/privacy.py` (`assert_allowed` reads `is_local`).
* TASK-100.
