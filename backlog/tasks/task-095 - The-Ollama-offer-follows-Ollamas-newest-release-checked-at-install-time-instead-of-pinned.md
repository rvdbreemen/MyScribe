---
id: TASK-095
title: >-
  The Ollama offer follows Ollama's newest release, checked at install time
  instead of pinned
status: To Do
assignee: []
created_date: '2026-09-26 17:49'
labels:
  - ollama
  - installer
  - security
dependencies: []
priority: medium
ordinal: 169000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert decided on 2026-09-26: MyScribe no longer pins the Ollama release it offers (scribe/ollama_release.json, v0.34.3). Asked with three options, he chose 'newest release, still checked': when setup builds the offer (Ollama absent, and the provider is Ollama or undecided), MyScribe asks GitHub for Ollama's newest non-draft, non-prerelease release, shows its URL, size and sha256 before the question, and after the download checks the file against both the GitHub API asset digest and the release's own sha256sum.txt, plus on Windows the Authenticode signer (O=Ollama Inc.). The pin and its upkeep go away: scribe/ollama_release.json as the source of truth, the check-pin step in ci.yml, step 2b in docs/RELEASING.md, and TASK-094. This changes ADR-017's 'from a pinned and verified artifact', so a Proposed successor ADR (ADR-021) records it; Robert accepts it. What stays from ADR-017: offer only when absent, shown and agreed to, never touch an Ollama that is there, the marker rules, Linux shows commands and runs nothing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: with the release API answering a newer release than the old pin, the offer shows that release's URL, size and sha256; today it shows the pin's
- [ ] #2 No network call happens unless an offer is actually built (state absent and provider ollama or undecided): a start, a plan in any other state and every present state make no request to GitHub, proven by a client that raises
- [ ] #3 The download is refused unless its sha256 equals the API digest AND the entry in the release's sha256sum.txt; a mismatch with either, a missing digest, or an unreachable sha256sum.txt ends on ollama.com/download plus Check again. Windows still verifies the signer. A test per refusal
- [ ] #4 When GitHub cannot be reached or rate-limits, the sitting says so in one sentence, offers the vendor page, and writes nothing; a proxy is named the way the other downloads name it
- [ ] #5 The model figures and the launcher's location question no longer read an Ollama installer size from a pin: scribe/footprint.json's ollama block either comes from the release metadata at offer time or is marked an estimate, and the test that held pin and footprint equal is replaced, not deleted silently
- [ ] #6 scribe/ollama_release.json is removed or reduced to what is not a pin (the vendor page, model sizes); ci.yml's check-pin step and RELEASING.md's step 2b are gone; the notes say what replaced each
- [ ] #7 ADR-021 is drafted as Proposed, superseding ADR-017 in its pinned-artifact clause only, signed as the agent; acceptance is Robert's
- [ ] #8 The per-file suite is green and CI is green on all three runners
<!-- AC:END -->
