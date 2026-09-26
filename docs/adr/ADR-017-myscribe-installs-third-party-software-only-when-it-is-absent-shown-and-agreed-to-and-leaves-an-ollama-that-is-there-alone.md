---
id: "ADR-017"
title: "MyScribe installs third-party software only when it is absent, shown and agreed to, and leaves an Ollama that is there alone"
status: "Superseded"
date: "2026-09-26"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: "ADR-021"
related:
  - "ADR-011"
  - "ADR-015"
  - "ADR-016"
  - "ADR-021"
topics:
  - "third-party software"
  - "ollama"
aliases:
  - "Ollama install"
  - "the Ollama marker"
  - "OLLAMA_MODELS"
components:
  - "scribe.ollama_setup"
  - "scribe/ollama_release.json"
symbols:
  - "install_plan"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-017 MyScribe installs third-party software only when it is absent, shown and agreed to, and leaves an Ollama that is there alone

## Status

Superseded by ADR-021, 2026-09-26.

## Status History

```yaml
status_history:
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-016
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-015
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-011
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert on 2026-09-21, asked for in the session as 'accept adr-015 en adr-017'. Its one open question is recorded as explicitly deferred and not as measured: settling it needs a Windows machine with no Ollama, and this one has Ollama running. The decision does not rest on it - the Must already keeps the setup child's handle and stops that one process rather than its tree, and refuses Quit while a third-party installer runs, which is the safe rule whichever way the measurement falls. TASK-089.18 owns the run. One repair to note: when that deferral was recorded, two words of it were eaten by a shell substitution, and the sentence was restored by hand to what had been written. Accepting turns this record's Enforcement on for every commit from here."
    changed_via: adr-kit lifecycle
  - date: 2026-09-26
    status: Accepted
    changed_by: Claude (agent, session 2026-09-26)
    reason: Related to ADR-021
    changed_via: adr-kit lifecycle
  - date: 2026-09-26
    status: Superseded
    changed_by: "User: Robert van den Breemen"
    reason: "Superseded by ADR-021 at Robert's decision of 2026-09-26: no pinned Ollama release any more; everything else of this record is carried over unchanged."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

On 2026-09-20 Robert asked the installer to offer Ollama and a model, and
set the limit with it: "Als Ollama er al is, dan niets doen"
(`docs/superpowers/specs/2026-09-20-installer-decisions.md:38-39`). This
record says when MyScribe may install somebody else's software: a rule for
any third-party program, with Ollama as its first case. Scope, as this
record reads it: software that lands outside MyScribe's home and outlives an
uninstall. The pinned tools in the payload (`packaging/tools.json`) stay
under ADR-011.

Read on 2026-09-20:

* **Nothing looks for Ollama, and nothing can tell absent from stopped.**
  The first-run form opens on "Ollama, on this machine"
  (`packaging/launcher/myscribe_launcher.py:558-561`). To the only probe,
  `available()` (`scribe/llm/ollama.py:384-409`), not installed and stopped
  are the same refused connection (`:399`), so an offer built on it would
  land on an Ollama somebody stopped.
* **Ollama's own installers do not know "only when missing".** Fetched again
  for this record; both sha256 equal those in
  `docs/superpowers/specs/2026-09-20-installer-evidence/ollama-install-scripts.txt`.
  On macOS `install.sh` stops a running Ollama and removes
  `/Applications/Ollama.app` before it downloads (`install.sh:62-69`). On
  Linux it requires root or sudo (`install.sh:110-118`), removes the existing
  library directory (`install.sh:164-167`), writes and restarts a systemd
  unit (`install.sh:215-238`) and can install graphics drivers
  (`install.sh:353`, `install.sh:381`). `install.ps1` has no "already
  installed" test (`install.ps1:319-323`) and runs the installer silently
  (`install.ps1:271`). The install commands in Ollama's readme at v0.34.2
  pipe these scripts into a shell (`:16`, `:24`, `:32`).
* **Quit is a tree kill, and it does not reach setup.** On Windows the
  launcher stops the app with `taskkill /T /F` (launcher `:376-382`);
  `quit_app` stops only the app (`:617-621`) and `run_streaming` keeps no
  handle on the setup child (`:275-286`). What a tree kill does to a daemon
  an installer has just started is verified by nobody.

## Decision Drivers

* "Als Ollama er al is, dan niets doen."
* Rather a question now than a separate thing to do afterwards (Robert).
* Nothing runs that was not shown first and cannot be checked against a pin.
* Doubt falls to leaving things alone: a wrong "present" costs one typed
  command, a wrong "absent" overwrites somebody's install.

## Considered Options

* Install only when absent, shown and agreed to, pinned and verified, behind
  MyScribe's own test; a marker lets an interrupted pull be offered again.
* The same without a marker: the pull in the install's sitting or never.
* Delegate "only when missing" to Ollama's own install scripts.
* Never install, only link to the vendor's download page.
* Install silently as part of setup.

## Decision Outcome

Chosen option: **install only when absent, shown and agreed to, from a
pinned and verified artifact, with the marker**, because it keeps Robert's
sentence - an Ollama that is there is not touched - spares the person a
second install after the first, and lets an interrupted download be
finished with the same button. The rule is the Decision Contract below;
Ollama is its first case. Robert decided the leave-alone rule (R2), the
marker and its binding (G6 and G7, which keep the rules of M1) and the model
folder (G8) on 2026-09-20, the marker against the agent's recommendation of
the option without one. Which model goes with the offer, and the memory
threshold for a larger one, are the spec's (§3.3, W4).

An Ollama that is present is left alone, in every state (R2):

| State | What the sitting shows | What MyScribe does to it |
| --- | --- | --- |
| Installed, not answering | one sentence and "Check again" | nothing; it is never started, because people stop Ollama on purpose to free VRAM (video memory) |
| Running, no chat-capable model | one sentence, the copyable `ollama pull qwen3.5:4b` and "Check again" | nothing; no pull is offered and nothing is pulled into it |
| Ready | one line saying it was left alone | nothing |
| Cannot be read | a line saying so | nothing; it counts as present |

### Confirmation

Nothing below exists yet; each task owes the evidence named.

* TASK-089.06: an unreadable state never reads as absent; on Robert's
  machine `ollama list` and the version endpoint read the same before and
  after a whole sitting.
* TASK-089.18: with a process starter and a downloader that raise, nothing
  runs or downloads in any state but absent; the executed command equals
  the shown one; a test per marker rule; a wrong digest turns the pin's
  check red. On a machine without Ollama - Windows Sandbox, a Linux
  distribution, a Mac - one recorded run per path and the Quit experiment.
  Nobody has done any of them; a skipped run says "not run".
* TASK-089.15: Quit during a download leaves no orphaned python process.
  TASK-089.23: the diff of the welcome text and of the readme.

## Decision Contract

### Must

* Offer an install only when the software is absent by MyScribe's own test,
  in which any doubt, an unreadable state included, reads as present. For
  Ollama (TASK-089.06): none on the search path or in the known locations,
  no `OLLAMA_*` variable, and no answer on the loopback - asked past any
  proxy, which is ADR-015's rule.
* Before the question, show the artifact - its URL (Uniform Resource Locator), size and sha256 - the
  exact command line and where it installs. The default is No, and a skip
  is a No.
* Pin the artifact and verify its sha256, and on Windows its Authenticode
  signer, before anything runs; run exactly what was shown. Ollama's script
  accepts only a subject carrying `O=Ollama Inc.` (`install.ps1:87`); the
  pinned installer's certificate has not been read. A missing artifact or a
  mismatch ends on the vendor's download page and "Check again".
* Where the install needs root, show the vendor's commands as download,
  read, then run, and let the person run them: a root script that can
  install drivers is not run behind a yes or no.
* Write the marker only after the installer has finished successfully, so
  that a failed or cancelled download leaves none. Bind it to the version
  and the path of what was installed - the default path alone is the same
  for everybody - and drop it on any doubt. Clear it once Ollama has been
  seen ready (M1, G7).
* Ask where a new Ollama keeps its models only when the default volume is
  too small (G8). Its default is Yes, because a No ends the offer. On
  Windows set `OLLAMA_MODELS` for the user's account before the installer
  starts; elsewhere show the exact command. Unverified:
  whether the installer's daemon inherits it (TASK-089.18), so check where
  the models went before saying so.
* Keep the pin under `scribe/`, which the payload ships
  (`packaging/build_payload.py:38`). Its owner is a step in
  `docs/RELEASING.md` that bumps it and hashes the download, and a CI (continuous integration) check
  that the pinned URL resolves and its size and digest match (M7).
* Say in the installer's welcome text (`packaging/windows/myscribe.iss:51`)
  and the readme that an Ollama and a model MyScribe installed survive an
  uninstall and belong to the user, and how to remove them (M8).
* Keep the setup child's handle and stop that one process on Quit, not its
  tree; while a third-party installer runs, refuse Quit and say why (M10).

### Must Not

* Install over, upgrade, start, stop, pull into or reconfigure an Ollama
  that is present, whatever its state - except the pull under Exceptions.
* Offer the installer while any Ollama binary exists, marker or not.
* Start an Ollama, the one MyScribe has just installed included.
* Pipe a remote script into a shell, run a script as root on the user's
  behalf, leave the presence test to a vendor's installer, or fall back to
  an unpinned download.
* Set `OLLAMA_MODELS`, or any other setting of Ollama's, outside an install
  MyScribe makes in that sitting.
* Say that Quit is clean before TASK-089.18 has measured it.

### Exceptions

* **The pull into MyScribe's own install** (G6). While the marker stands,
  the unfinished pull of the one model that was shown and agreed to before
  the install may be offered again: in a sitting somebody opened with
  `--setup`, never by a start, as a question whose default is No. In the
  install's own sitting the pull follows the install. Nothing else is done
  to that Ollama. Robert chose this on 2026-09-20 against the agent's
  recommendation of no marker and no exception: finishing an interrupted
  download with the same button matters more to him than keeping no state.
* **macOS is guided, and unverified.** The verified `.dmg` is opened, the
  person drags the app across, and MyScribe waits with "Check again". Nobody
  has run that or the presence test's install locations: the Mac's criteria
  run in one bundled sitting (G9, in ADR-015) and read "not run" till then.

### Verification

* The tests named under Confirmation, once they exist, and
  `adr-judge --dry-run-enforcement ADR-017` as run under Enforcement.

## Consequences

### Positive

* An Ollama somebody already has is never touched, and that is checkable on
  any machine that has one.
* Somebody without Ollama gets it and a model in the same sitting, from an
  artifact they were shown and that was verified.

### Negative

* MyScribe owns a pin on somebody else's release cadence. A stale pin turns
  the offer into a link; the release step and the check are what it costs.
* The marker is state that can go stale, and "left alone" carries an
  exception that has to be explained and tested. Robert accepted that.
* On Linux and macOS installing Ollama is not one click.

## Pros and Cons of the Options

### Absent, shown, agreed, pinned - with the marker (chosen)

* Good, because an interrupted download is finished with the same button.
* Bad, because of the pin and the marker: two things to own.

### The same without a marker (the agent's recommendation)

* Good, because there is no state to keep and "left alone" has no exception.
* Bad, because an interrupted pull ends on a copyable command in a
  terminal: the separate thing afterwards that Robert asked to avoid.

### Delegate "only when missing" to Ollama's own scripts

* Good, because the vendor maintains them: no pin to own.
* Bad, because they install unconditionally, and on macOS and Linux they
  remove what is there first (Context).
* Bad, because what runs is whatever the server serves that day: it cannot
  be shown in advance, pinned or checked; and on Linux it needs root, which
  the frozen launcher has no terminal to ask for.

### Never install, only link

* Good, because MyScribe owns no pin, no marker and no installer process.
* Bad, because it is the separate thing afterwards: somebody who wants a
  local provider leaves setup with none and a link.
* Neutral: every failure path of the chosen option ends here anyway.

### Install silently as part of setup

* Good, because it is one wait and no question.
* Bad, because 1.57 GB (gigabytes; 1,569,993,232 bytes) on Windows, or the
  198 MB (megabytes; 197,873,582 bytes) `.dmg` on macOS, and a model land on
  a machine whose owner was not asked, and an executable runs unshown.

## Open Questions

- [x] Where should a new Ollama keep its models? It is not asked. Setting `OLLAMA_MODELS` for an install MyScribe itself makes touches no existing Ollama, so the leave-alone rule is not engaged, but it is still configuring somebody else's software. Without it, a default volume that is too small ends the offer with both numbers and no way out. Robert decides whether it is ever asked. — **Answered 2026-09-20 by User: Robert van den Breemen:** It is asked only when the default volume is too small, and never otherwise. With room, Ollama keeps its own default and there is no question. Without it, the installer shows both numbers and asks whether the models should live with MyScribe instead, with the default on Yes because the other answer ends the offer. On Windows MyScribe then sets `OLLAMA_MODELS` as a variable of the user's account - the same place the app already reads through `windows_env` (`scribe/llm/base.py:194-222`) - before the installer starts, so that the daemon it starts gets it; on macOS and Linux it stays a sentence with the exact command until somebody has measured the mechanism there, because a systemd unit wants root and a launch agent is a second mechanism nobody has run. It engages no existing Ollama, since it happens only for an install MyScribe itself makes in that sitting. It is what Robert's own machine shows to be needed: his models were moved to another drive by hand, and somebody who put MyScribe on a second drive because the system drive is small would otherwise run aground here. Not verified: whether the daemon started by the silent installer inherits the variable. That belongs to the sandbox run of TASK-089.18, and until it has been run nothing in the product says the models went where they were asked to go without checking.
- [x] On a Windows machine without Ollama (Windows Sandbox or a virtual machine): does the silent installer start the daemon, and would a tree kill (`taskkill /T /F`, the way the launcher stops the app) take that daemon down - on the setup child, which the Must for that reason stops alone and not by its tree, and on the app at a later Quit? Ollama's own script waits for the installer process only because "using -Wait would wait for Ollama to exit too" (`install.ps1:294-295`, read 2026-09-20), which suggests the daemon is the installer's child. Process lineage under `taskkill /T` after the Ollama installer has exited is verified by nobody. Until a run answers it, nobody promises that Quit is clean; whoever has the sandbox answers it, under TASK-089.18. — **Answered 2026-09-21 by Claude (agent, session 2026-09-21):** DEFERRED, NOT MEASURED - and the decision does not rest on it. Settling it needs a Windows machine with no Ollama on it; the machine this was written on has Ollama 0.34.0 running, so nobody here can start the installer that the question is about. What the record does in the meantime is the conservative thing either way: the Must says to keep the setup child's handle and stop that one process on Quit rather than its tree, and to refuse Quit while a third-party installer runs. So a tree kill never reaches a daemon whatever the answer turns out to be, and the measurement would only say how bad the alternative would have been. TASK-089.18 owns it: whoever has a sandbox or a spare Windows machine runs the silent installer, looks at whether the daemon it starts survives the installer process exiting, then tries a tree kill on the installing process and reports what happened to the daemon. Until that is run, nothing in the product promises that Quit is clean around an Ollama install, and the same run answers whether the daemon inherits OLLAMA_MODELS. Deferred by the agent on 2026-09-21 with Robert accepting the record the same day, knowing this.
- [x] Should the marker be bound to what was installed - version and path - so that an Ollama the user installs after removing MyScribe's is never mistaken for MyScribe's own unfinished work? — **Answered 2026-09-20 by User: Robert van den Breemen:** Yes: to the version and to the path, and any doubt drops it. The marker records what MyScribe installed, where and when, and it counts only while that same version still stands at that same path; anything else makes it lapse, after which the Ollama that is there is left alone and the report gives the copyable pull command. No binding is watertight, and this one is chosen for the side it fails to. Ollama updates itself, so the version changes for an honest reason and the marker then lapses: the worst outcome is that an interrupted download is no longer offered and somebody types one command. The path alone would survive that update but cannot tell MyScribe's Ollama from one the user installed afterwards, because the default path is the same for everybody - which is the very case the leave-alone rule exists for. What version and path together do not catch is somebody removing MyScribe's Ollama and installing the same version at the same path; they then get one question whose default is No, and nothing happens without their yes. A shelf life on top of this was weighed and set aside as one more rule to explain for a gap that small.
- [x] Does the pull into MyScribe's own install outlive the sitting of the install? This record and `docs/superpowers/specs/2026-09-20-installer-design.md` say yes: while the marker stands, and only in a sitting opened with `--setup` (Exceptions). Robert's rule is that an Ollama that is there is left alone, and after that sitting it is there. The narrower rule: the pull happens in the install's sitting or not at all, and afterwards the report gives the copyable `ollama pull` command. Robert decides. — **Answered 2026-09-20 by User: Robert van den Breemen:** Yes. While the marker stands, a sitting that somebody opens with `--setup` offers the unfinished pull again, and the offer is a question with its default on No - never a pull that starts by itself. Robert chose this over the narrower rule the agent recommended (the pull in the install's own sitting or not at all, and no marker): finishing an interrupted download with the same button matters more to him than having no state to keep, and a copyable command in a terminal is the separate thing afterwards he asked to avoid. What it costs is accepted with it: the marker is state that can go stale, 'an Ollama that is there is left alone' carries one exception that has to be explained and tested, and the next question - what the marker is bound to - has to be answered for that exception to be safe. The marker rules stand as written: it is written only after the installer has finished successfully, the installer itself is never offered while any Ollama binary exists, and the marker is cleared once Ollama has been seen ready.
- [x] The pinned artifact addresses, sizes and digests and the Windows signer's name were read from Ollama's release data by a reader in the design run, not by this record's author. TASK-089.18 re-verifies them before anything is pinned. — **Answered 2026-09-20 by Claude (agent, session 2026-09-20):** Verified in part on 2026-09-20 by the orchestrator against the release data for the tag v0.34.2 of ollama/ollama, read with the GitHub command-line client: the release exists, is the latest, is not a prerelease, and was published on 2026-09-15. `OllamaSetup.exe` is 1,569,993,232 bytes with sha256 8c9eb7ba71f3c6a62df4c7d204cc4739d90c335e1cbeb07c99fd471544066a8b, and `Ollama.dmg` is 197,873,582 bytes with sha256 ca3c5c1587fe05eebedacd33ec9448e7999fcce9c19dafcb4ca7eca2f89d5d7b; both sizes match what this record quotes. Not verified: the name of the Windows signer, which needs the installer downloaded, and the Linux artifact. Ollama releases often, so TASK-089.18 verifies again at the moment it pins.

## Related Decisions

* ADR-015 (the setup engine) asks this record's question: its plan carries
  the Ollama state and the install plan, and a front-end only renders them.
  The proxy rule for loopback probes and the answer about the Mac are there.
* ADR-016 (no provider row, no provider): a declined offer writes nothing,
  so it leaves no provider behind.
* ADR-011 (the launcher): its pinned tools are outside this rule, and it
  learns only to stop the setup child alone.

## References

* `docs/superpowers/specs/2026-09-20-installer-decisions.md` - the key legend: R2, M1, M7,
  M8, M10, W4, G6, G7, G8.
* `docs/superpowers/specs/2026-09-20-installer-design.md` - §1 the three states, §3.3
  detection, the marker and the model offer (`scribe.ollama_setup` and
  `scribe/ollama_release.json` under `components` are its proposed names,
  TASK-089.18; nothing exists yet), §5 the failure paths, §6.
* `docs/superpowers/specs/2026-09-20-installer-evidence/ollama-install-scripts.txt` -
  addresses, sizes, sha256 and the lines read; the scripts are not copied.
* https://ollama.com/install.sh and https://ollama.com/install.ps1 - 455 and
  495 lines on 2026-09-20; unpinned, so the line numbers are that day's.
* https://github.com/ollama/ollama/releases/tag/v0.34.2 - re-read for this
  record on 2026-09-20, as in the last answer under Open Questions: GitHub's
  published digest, a single source until the release step hashes it.

## Enforcement

Three tripwires on the obvious form, not a proof: the judge tests the added
lines of a diff one at a time, so a command built across two lines or at
run time passes, and the tests of TASK-089.18 are the proof. The first also
trips on a comment that quotes the pipe form; word it without the pipe.
Tried on 2026-09-20 with adr-kit 0.57.0: no line in the repository matches
today, 23 sample lines behaved as meant, and the judge's dry run on a
scratch copy marked Accepted gave three violations for a probe diff with
one forbidden line per rule and none for the honest forms.

```json
{
  "forbid_pattern": [
    {"pattern": "(?i)\\b(?:curl|wget|irm|iwr|Invoke-WebRequest|Invoke-RestMethod)\\b[^|]*\\|\\s*(?:sudo\\s+)?(?:sh|bash|zsh|iex|Invoke-Expression)\\b", "path_glob": "{scribe/**,packaging/launcher/**,install.py}", "message": "A remote script is never piped into a shell, not even as a command shown to the user: download, read, then run (ADR-017)."},
    {"pattern": "[\"']ollama(?:\\.exe)?[\"']\\s*,\\s*[\"']serve[\"']", "path_glob": "{scribe/**,packaging/launcher/**,install.py}", "message": "MyScribe never starts an Ollama, the one it installed included: say so and offer Check again (ADR-017)."},
    {"pattern": "ollama\\.com/download/[^\\s\"'<>]+\\.(?:exe|dmg|zip|tgz|zst)\\b|ollama/ollama/releases/latest\\b", "path_glob": "{scribe/**,packaging/launcher/**,install.py}", "message": "Only the pinned, sha256-verified artifact is downloaded; a failed pin ends on the vendor's download page (ADR-017)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
