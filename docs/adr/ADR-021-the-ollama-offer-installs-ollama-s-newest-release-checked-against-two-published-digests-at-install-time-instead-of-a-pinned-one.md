---
id: "ADR-021"
title: "The Ollama offer installs Ollama's newest release, checked against two published digests at install time, instead of a pinned one"
status: "Accepted"
date: "2026-09-26"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes:
  - "ADR-017"
superseded_by: null
related:
  - "ADR-015"
  - "ADR-017"
topics:
  - "third-party software"
  - "ollama"
  - "release integrity"
aliases:
  - "Ollama newest release"
  - "unpinned Ollama"
  - "releases/latest"
  - "sha256sum.txt"
components:
  - "scribe.ollama_setup"
  - "scribe/ollama_offer.json"
symbols:
  - "latest_release"
  - "release_client"
  - "install_plan"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-021 The Ollama offer installs Ollama's newest release, checked against two published digests at install time, instead of a pinned one

## Status

Accepted, 2026-09-26.
"from a pinned and verified artifact". Accepting this record and running
`adr supersede` are Robert's; neither has been done.

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
    reason: Related to ADR-017
    changed_via: adr-kit lifecycle
  - date: 2026-09-26
    status: Proposed
    changed_by: Claude (agent, session 2026-09-26)
    reason: Related to ADR-015
    changed_via: adr-kit lifecycle
  - date: 2026-09-26
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert on 2026-09-26 in the session: the Ollama offer follows the newest release, checked at install time, as he chose from three options the same day."
    changed_via: adr-kit lifecycle
  - date: 2026-09-26
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Superseded by ADR-021 at Robert's decision of 2026-09-26: no pinned Ollama release any more; everything else of this record is carried over unchanged."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-017 (Accepted 2026-09-21) lets MyScribe install Ollama only when it is
absent, shown and agreed to, "from a pinned and verified artifact".
TASK-089.18 built that pin as `scribe/ollama_release.json` (release v0.34.3),
with a CI (continuous integration) step and a release step as its owners.
Ollama releases often - v0.34.2 on 2026-09-15 and v0.34.3 on 2026-09-19, by
the dates ADR-017 and the pin file recorded, and v0.34.4 on 2026-09-23, the
day the pin was read, as GitHub's API answered on 2026-09-26 - and keeping
the pin current already needed its own question to Robert (TASK-094).

On 2026-09-26 Robert was offered three options (TASK-094, TASK-095):

1. the newest release, still checked at install time;
2. no install at all, only a link to ollama.com/download;
3. keep a fixed pin and bump it per MyScribe release.

He chose the first. This record writes that choice down and says what it
changes in ADR-017 and what it leaves alone.

## Decision Drivers

* Robert's choice of 2026-09-26: newest release, still checked.
* An offer should not age with MyScribe's release cadence.
* Nothing runs that was not shown first and verified against more than one
  published source.
* A plan stays cheap: no network request where no offer is made.

## Considered Options

* Newest release, asked of GitHub when the offer is built, verified against
  the API (application programming interface) digest and the release's own
  `sha256sum.txt`, plus the Windows signer.
* No install: the offer becomes a link to ollama.com/download.
* A fixed pin, bumped by a release step and checked by CI (ADR-017 as built).

## Decision Outcome

Chosen option: **newest release, checked at install time**, because Robert
chose it and it keeps what ADR-017 protects - shown, agreed, verified - without
a pin that goes stale between MyScribe releases.

Everything else in ADR-017's decision is carried unchanged: an offer only
when Ollama is absent by MyScribe's own test; shown before the question and
agreed to, default No; an Ollama that is there is left alone in every state;
the marker, written only after a finished install and bound to version and
path; Linux shows the commands and runs nothing; the macOS path is guided.

### Confirmation

Built under TASK-095; tests in `tests/test_ollama_setup.py` and
`tests/test_setup_plan.py`, all against a fake GitHub:

* `test_the_offer_shows_the_newest_release_s_url_size_and_sha256`
* `test_every_refusal_ends_on_the_vendor_page_and_check_again` (eleven cases)
* `test_a_file_that_matches_the_api_but_not_sha256sum_is_refused` and its
  mirror
* `test_a_plan_in_a_present_state_asks_github_nothing`,
  `test_a_start_after_the_offer_was_put_asks_github_nothing`,
  `test_the_one_path_that_builds_the_offer_does_reach_github`
* `test_a_github_token_goes_to_the_api_only_and_is_never_shown`
* `test_the_marker_records_the_version_of_the_release_that_was_fetched`

Not run: an install of a fetched release on a machine without Ollama. That
run still belongs to TASK-089.18 criterion 16.

## Decision Contract

### Must

* Build the offer only when Ollama is absent, the provider is Ollama or
  undecided, and the install question will be put. Only then ask
  `repos/ollama/ollama/releases/latest`, which skips drafts and prereleases,
  and the release's `sha256sum.txt`: two GETs, never an installer.
* Take the URL, size and sha256 of this platform's asset from that answer and
  show them before the question, with the command line, the folder, no
  administrator and self-updates (ADR-017's Must, unchanged).
* Refuse unless the API digest and the `sha256sum.txt` line agree, and unless
  the downloaded file equals both. On Windows also verify the signer
  `O=Ollama Inc.`.
* End every failure in one sentence with ollama.com/download and Check again,
  naming a configured proxy: GitHub unreachable or rate-limited, a 404, a
  missing asset, digest or `sha256sum.txt`, two digests that disagree.
  Nothing is written.
* Bind the marker to the version of the release that was fetched and to the
  path, as ADR-017 says.
* Send a `GITHUB_TOKEN` from the environment to api.github.com only, and
  never print, return or store it. Work without one.

### Must Not

* Download an Ollama installer that was not shown, or whose digest came from
  one source only.
* Ask GitHub from a plan that makes no offer: a present state, a stored cloud
  provider, or a start whose sitting already put the question.
* Download from a vendor URL that publishes no digest (the
  ollama.com/download installer links).
* Everything ADR-017's Must Not lists, which stands.

### Exceptions

* None beyond ADR-017's two, which stand: the pull into MyScribe's own
  install, and the guided macOS path.

### Verification

* The tests under Confirmation, and `adr-judge --dry-run-enforcement ADR-021`.

## Consequences

### Positive

* The offer never ages: no pin, no CI check of it, no release step for it.
* Two published sources must agree before a byte of the installer is kept,
  on the machine that installs, where the pin's CI check compared the
  pinned figures with them on a runner.

### Negative

* A plan on an absent machine now makes a network request; it has a short
  timeout (5 s connect, 10 s read) so a slow GitHub becomes a sentence.
* Plan and apply are two processes, so apply asks GitHub again. A release
  published in between is the one installed; apply names its tag, URL and
  sha256 before downloading. Accepted as small; closing it would need every
  front-end to hand the shown tag back.
* MyScribe installs a release nobody here has run. The silent flags and the
  signer were read on v0.34.3 only; a release that changes either fails safe.
* An unauthenticated machine gets 60 API requests an hour; past that the
  offer is a sentence and Check again.
* The launcher's installer size is now an estimate from v0.34.3, labelled as
  such; the offer shows the real size.

## Pros and Cons of the Options

### Newest release, checked at install time (chosen)

* Good, because nothing goes stale and the check happens on the file that
  runs.
* Bad, because the release is chosen by Ollama, not reviewed by MyScribe.

### No install, only a link

* Good, because MyScribe owns no installer path at all.
* Bad, because it is the separate thing afterwards Robert asked to avoid
  (ADR-017).

### A fixed pin (ADR-017 as built)

* Good, because what is installed was chosen and read in advance.
* Bad, because it goes stale within days and needs a release step and a CI
  check to own it.

## Open Questions

None.

## Related Decisions

* ADR-017: this record replaces its pinned-artifact clause - the third Must
  ("Pin the artifact..."), the seventh ("Keep the pin under `scribe/`..."),
  "fall back to an unpinned download" in its Must Not, and its third
  Enforcement tripwire, which forbids the `releases/latest` endpoint this
  record uses. The rest stands.
* ADR-015: the plan carries the offer; a front-end renders it. A refused
  offer is a sentence in the Ollama note, so the contract does not change.

## References

* TASK-095 (this change) and TASK-094 (the question it answers).
* https://docs.github.com/en/rest/releases/releases#get-the-latest-release -
  "the latest published full release", excluding drafts and prereleases.
* `scribe/ollama_setup.py:440` (the endpoint), `scribe/ollama_setup.py:550`
  (the two-source check of the asset) and `scribe/ollama_setup.py:1111` (the
  same check again before a byte is downloaded), as of TASK-095.

## Enforcement

ADR-017's first two tripwires, carried unchanged, and its third narrowed to
the vendor installer URLs: `releases/latest` is now the endpoint the offer
asks. While ADR-017 stays Accepted its own third tripwire still fires on that
endpoint; accepting this record and superseding ADR-017 is what ends that.

```json
{
  "forbid_pattern": [
    {"pattern": "(?i)\\b(?:curl|wget|irm|iwr|Invoke-WebRequest|Invoke-RestMethod)\\b[^|]*\\|\\s*(?:sudo\\s+)?(?:sh|bash|zsh|iex|Invoke-Expression)\\b", "path_glob": "{scribe/**,packaging/launcher/**,install.py}", "message": "A remote script is never piped into a shell, not even as a command shown to the user: download, read, then run (ADR-017, carried by ADR-021)."},
    {"pattern": "[\"']ollama(?:\\.exe)?[\"']\\s*,\\s*[\"']serve[\"']", "path_glob": "{scribe/**,packaging/launcher/**,install.py}", "message": "MyScribe never starts an Ollama, the one it installed included: say so and offer Check again (ADR-017, carried by ADR-021)."},
    {"pattern": "ollama\\.com/download/[^\\s\"'<>]+\\.(?:exe|dmg|zip|tgz|zst)\\b", "path_glob": "{scribe/**,packaging/launcher/**,install.py}", "message": "Only a release asset whose digest GitHub's API and sha256sum.txt both publish is downloaded; the vendor's installer links carry neither (ADR-021)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
