---
id: TASK-089.02
title: >-
  ADR-016, ADR-015 and ADR-017 are each decided before the code that builds
  under them, and the stdlib-only guard covers install.py
status: Done
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 04:23'
labels:
  - adr
  - architecture
  - packaging
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 139000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three durable decisions are being made here, and each belongs in an ADR before the code exists (.adr-kit/ADR-guide.md:40-43). They were first drafted as one record. Robert grilled it on 2026-09-20 and split it in three, because adr-kit asks for one decision per record (brief: G1). ADR-015: first-run setup is one app-side engine behind a JSON contract, and every front-end only renders it; with it goes where everything is kept, the pointer file that home_dir() reads. ADR-016: a missing provider row selects no provider, and nothing is sent until somebody has chosen (brief: R1, W3). ADR-017: MyScribe installs third-party software only when it is absent, shown and agreed to, and leaves an Ollama that is there alone (brief: R2).

ADR-015 was written as Proposed in the recording step of 2026-09-20 (brief: R4); ADR-016 and ADR-017 were split off from it the same day and are Proposed as well. This task carries each to a decision, and each before the code that builds under it: ADR-016 before TASK-089.07, ADR-015 before TASK-089.09, ADR-017 before TASK-089.18. Two more build under ADR-017 and land while it is still Proposed: TASK-089.15 criterion 10 (M10, how the setup child is stopped on Quit) and TASK-089.23 (M8, what stays behind after an uninstall); TASK-089.18 criterion 13 then confirms or changes what TASK-089.15 promised. Acceptance is Robert's: the guide forbids accepting on one's own initiative. The three do not become acceptable at the same moment, because `adr accept` refuses a record that still has an unanswered Open Question (adr-kit 0.57.0, bin/adr:700-704). ADR-016 has none. ADR-015 has one, a measurement: whether MyScribe's own two loopback probes fail behind a proxy the way the library defaults did (TASK-089.05). ADR-017 has one, a measurement too: what the silent Ollama installer starts and how a tree kill treats it (TASK-089.18 criterion 13). TASK-089.09 and TASK-089.14 name this task as a dependency. What they build under is ADR-015, so criterion 1 is what they wait for; ADR-017's acceptance (criterion 9) waits for a run that TASK-089.18 makes, and TASK-089.18 comes after both.

Two things the earlier plan decided silently were argued in the record and then decided by Robert in the grill. First, whether install.py shares the launcher's CODE through a CloneLayout or only its SEQUENCE (brief: M11). The packaging reader advised sequence only, because the launcher must stay frozen-safe and ADR-011 keeps it small on purpose; the plan chose shared code in a files table. Robert chose the sequence, with one contract test (brief: G2; ADR-015). Second, the rules for the Ollama marker, which as first proposed let MyScribe install over an Ollama the user had put there (brief: M1). They stand as rewritten, the unfinished pull may be offered again in a sitting opened with `--setup`, and the marker is bound to version and path (brief: G6, G7; ADR-017).

A correction to the plan. It proposed adding a second forbid_pattern to ADR-011's Enforcement block. ADR-011 is Accepted, and the guide says never to rewrite an Accepted ADR (.adr-kit/ADR-guide.md:52). This repository's own practice agrees: ADR-013 and ADR-014 are restatements made for exactly that reason. The guard for install.py therefore lives in ADR-015's own Enforcement block, or ADR-011 is superseded. It is not edited in place.

ADR-011's pattern today is a deny-list of seven module names over packaging/launcher/** (its Enforcement block, lines 237-247). `import requests` would pass it. The real stdlib-only proof is the AST allow-list test in TASK-089.17, and the ADR text says so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ADR-015 - the engine, its JSON contract and where everything is kept - is Accepted by Robert, or amended and then accepted, before the code of TASK-089.09 lands. Nobody else runs the accept. `adr accept` refuses a record with an unanswered Open Question (adr-kit 0.57.0, bin/adr:700-704), and ADR-015 has one: whether MyScribe's own two loopback probes fail behind a proxy the way the library defaults did. TASK-089.05 makes that run, and its answer is recorded with `adr answer` first. The same question also names a system proxy in the Windows registry, macOS and Linux; those parts stay 'not run' per G9 and do not hold the accept - Robert's answer of 2026-09-20 says the decision does not wait on the Mac - so the answer records the Windows run and says what it did not run. ADR-016 and ADR-017 have criteria 8 and 9.
- [x] #2 One decision per record (brief: G1), each with the alternatives considered. ADR-015 records the engine contract and the home pointer file, against questions in the Inno wizard, a /welcome page in the web UI, and asking everything before the sync. ADR-017 records the rule for installing third-party software, against leaving 'only when missing' to Ollama's own scripts. ADR-016 records that a missing provider row selects no provider, against keeping the fall-through it reverses. A sentence that belongs to a sibling record is not repeated: the sibling is named by its id. ADR-015 also says how `python -m scribe.doctor` from a terminal, and `--prove` when no app answers, sit with ADR-001: that record's Must keeps GPU work inside runner children and its Exceptions read 'None' (docs/adr/ADR-001-...md:116-118, :128-131), and no ADR records the doctor command as an exception today (TASK-089.13 criterion 9).
- [x] #3 Sharing the launcher's code with install.py versus sharing only its sequence is argued in ADR-015 as a considered option, with its consequences for the frozen binary (brief: M11), and the record carries Robert's answer of 2026-09-20 (brief: G2): only its sequence, with one contract test that gives both doors the same lock and stamp and demands the same sync decision. Fetching the tools and checking their sha256 is shared with packaging/build_payload.py, and no CloneLayout is built. TASK-089.17 builds it.
- [x] #4 The marker rules are in ADR-017 as decided: written only after the installer finished successfully; the installer never offered again while any Ollama binary exists; cleared once Ollama has been seen ready (brief: M1). With them stand Robert's two answers of 2026-09-20. While the marker stands, a sitting opened with `--setup` offers the unfinished pull again, as a question whose default is No (brief: G6, chosen against the agent's recommendation of the narrower rule). And the marker is bound to version and path, and any doubt drops it (brief: G7).
- [x] #5 Each record's context names the recorded choice it revises knowingly. ADR-015 names TASK-040.06's 'Four answers, and no more than four' (scribe/setup.py:9). ADR-016 names the spec's 'Defaults: commercial providers' (docs/superpowers/specs/2026-09-01-myscribe-design.md:219).
- [x] #6 The stdlib-only guard covers install.py without editing ADR-011 in place. On a scratch COPY of the repo in which ADR-015 is marked Accepted, `adr-judge --dry-run-enforcement ADR-015` flags a probe diff that adds `import scribe` to install.py and, to the launcher, both `command += ["--llm-key", key]` and `command += ["--hf-token", token]` - the second because G4 took the flag's allowance out of the Enforcement block (ADR-015, Open Questions); the launcher's existing line (packaging/launcher/myscribe_launcher.py:475-476) stays unflagged until TASK-089.15 removes it, because a forbid rule reads the added lines of a diff only (adr-kit 0.57.0, bin/adr-judge:528-535) - and the output is shown: that is ADR-015's own Verification line. The judge skips a record that is not Accepted, which is why the copy is marked, and why this can be shown before criterion 1 is met without anybody running the accept in the repository. After acceptance the same diff is blocked by `/adr-kit:judge` - the kit's `adr_judge`, or `bin/adr-judge --json` from its CLI (.adr-kit/ADR-guide.md:22); the guide lists no `adr-kit judge` command. ADR-016 and ADR-017 get the same dry run for whatever patterns their Enforcement blocks carry; a block whose arrays are empty says why in one sentence, and the notes quote it.
- [x] #7 ADR-INDEX.md and ADR-INDEX.json are updated by the kit, not by hand.
- [x] #8 ADR-016 - a missing provider row selects no provider - is Accepted by Robert, or amended and then accepted, before the code of TASK-089.07 lands. Nobody else runs the accept. It has no Open Question, so nothing but his decision stands between it and acceptance (brief: G1). It is accepted while the fall-through is still in the code. A pattern in its Enforcement block therefore guards against the fall-through coming BACK and does not fail a commit that merely touches those files in the meantime: the judge applies a forbid rule to the added lines of a diff and a require rule to the whole file (adr-kit 0.57.0, bin/adr-judge:530 and its --snapshot help). The notes show the pattern tried both ways, or quote the one sentence saying why the arrays are empty.
- [x] #9 ADR-017 - third-party software only when it is absent, shown and agreed to, and an Ollama that is there left alone - is Accepted by Robert, or amended and then accepted, before the code of TASK-089.18 lands. Nobody else runs the accept. Its one Open Question is a measurement: what the silent Ollama installer starts and how a tree kill treats it. TASK-089.18 criterion 13 makes that run on a machine without Ollama, with that task's own code on a branch build, because the run installs through the setup child; its answer is recorded with `adr answer` before the accept, and until then `adr accept` refuses the record (adr-kit 0.57.0, bin/adr:700-704). That the run comes before that task's code lands is the agent's reading of 'decided before the code that builds under it', not Robert's words. The task-level cycle this makes is named in the notes, and resolving it is not this criterion's to do.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Task-level cycle, found by the verifiers on 2026-09-20 and not resolved by the fixer: criterion 9 needs the sandbox run of TASK-089.18 criterion 13, and TASK-089.18 depends on this task through TASK-089.15 and TASK-089.14 (dependencies as recorded: .18 -> .15 -> .14 -> .02, and .09 -> .02). TASK-089.07 carries no dependency on whatever accepts ADR-016, and TASK-089.18 none on whatever accepts ADR-017, so 'ADR-016 before TASK-089.07' and 'ADR-017 before TASK-089.18' exist in prose only. Two ways out, for the orchestrator or Robert to choose: split this task per record - keep this one for ADR-015 and the stdlib guard, one subtask each for ADR-016 and ADR-017, then point TASK-089.07 at the ADR-016 task and TASK-089.18 and TASK-089.23 at the ADR-017 task - or let TASK-089.18 carry ADR-017's acceptance and retire criterion 9 here. Not done by the fixer because it changes the task graph that TASK-089.07, TASK-089.18 and the three records cite by this task's criterion numbers.

## Verification (orchestrator, 2026-09-22)

All three records were accepted by Robert in session on 2026-09-21: ADR-016 on
his "Accept ADR 016", ADR-015 and ADR-017 on his "accept adr-015 en adr-017".
Nobody else ran an accept. `docs/adr/ADR-INDEX.md:23-25` and
`ADR-INDEX.json` (17 records) show all three Accepted, dated 2026-09-21; both
were written by `adr accept` and `git status --short docs/adr` is empty, so
nothing was touched by hand (AC #7).

### The Enforcement blocks bite (AC #6, AC #8)

Run in the repository itself, not on a scratch copy: the three records are
Accepted now, so `--dry-run-enforcement` reaches them, and `adr-judge` is
read-only towards the tree - it reads a unified diff from a file. Probe diffs
in the scratchpad, never committed.

ADR-015, probe adding `import scribe` and `from packaging import build_payload`
to a new `install.py`, and three lines to `setup_command`:

    VIOLATION  ADR-015  forbid_pattern  packaging/launcher/myscribe_launcher.py:646
               > command += ["--llm-key", answers["llm_key"]]
    VIOLATION  ADR-015  forbid_pattern  packaging/launcher/myscribe_launcher.py:647
               > command += ["--hf-token", answers["hf_token"]]
    VIOLATION  ADR-015  forbid_pattern  scribe/setup.py:152
               > parser.add_argument("--llm-key", default="")
    VIOLATION  ADR-015  forbid_import  install.py:3
               > import scribe
    VIOLATION  ADR-015  llm_judge
    5 violation(s), 0 advisory

The existing `--hf-token` line, `packaging/launcher/myscribe_launcher.py:645`,
is NOT among them: it is context, and a forbid rule reads added lines only.
It goes with TASK-089.15. `from packaging import build_payload` and
`"--apply-stdin"` passed, as the record's Verification line promises.

ADR-016, probe adding four lines to `scribe/llm/__init__.py`: `return name or
DEFAULT_PROVIDER`, a comment quoting the idiom, and `name if name else
DEFAULT_PROVIDER` were each flagged; `name or settings.get(...)` passed.
4 violation(s), 0 advisory. The comment being flagged is what the record's own
Enforcement text says happens - it needs `ADR_KIT_OVERRIDE`.

ADR-017, probe adding a `curl ... | sh`, an `["ollama", "serve"]` and an
`ollama.com/download/OllamaSetup.exe` URL: three violations, one per rule.
`subprocess.run([str(pinned), "/VERYSILENT"])` - the honest form - passed.
4 violation(s), 0 advisory.

Outputs: `<scratchpad>/TASK-089.02/judge-015.txt`, `judge-016-017.txt`;
probes `probe.diff`, `probe-016.diff`, `probe-017.diff`. Each run also made
one `claude -p` call for the record's `llm_judge` pass, which agreed in all
three cases; that pass takes ~15-19 s and trips the kit's 5000 ms pre-commit
warning.

### The task-level cycle is resolved by a deferral, not by a run (AC #9)

The cycle the verifiers found on 2026-09-20 - criterion 9 needs the sandbox run
of TASK-089.18 criterion 13, and TASK-089.18 depends on this task - was broken
by answering the Open Question with an explicit deferral rather than with the
measurement. The recorded answer says so in its first words: "DEFERRED, NOT
MEASURED - and the decision does not rest on it". It stands because the Must is
conservative either way: the setup child is stopped alone and not by its tree,
and Quit is refused while a third-party installer runs, so a tree kill never
reaches a daemon whatever the answer turns out to be. Robert accepted the
record the same day knowing this.

What is therefore still NOT known, and belongs to TASK-089.18: whether the
silent installer's daemon survives the installer process exiting, what
`taskkill /T /F` does to it, and whether that daemon inherits `OLLAMA_MODELS`.
The Windows signer's name and the Linux artifact's digest are unverified too;
this machine has Ollama 0.34.0 running, so the installer cannot be started here.

### Content (AC #2, #3, #4, #5)

Read and confirmed in the records: one decision per record, each with its
alternatives; ADR-015 argues sharing the launcher's code against sharing only
its sequence and carries Robert's answer with the line count that settled it
(:367), and says why `--prove` and `python -m scribe.doctor` are no breach of
ADR-001's "Exceptions: None" (:209-214); ADR-017 carries the four marker rules
and both of Robert's answers (:183, :208, :219, and the version-and-path
binding); ADR-015 names TASK-040.06's "Four answers, and no more than four"
(:131) and ADR-016 the spec's "Defaults: commercial providers" (:89).

No code changed in this task and no test was run for it.

### Two sub-claims closed afterwards (orchestrator, 2026-09-22)

AC #6 says the guard covers `install.py` *without editing ADR-011 in place*,
and I had only verified the positive half. The negative: `git log --oneline --
docs/adr/ADR-011*.md` shows two commits after its acceptance (297e6d5), and
both are the kit's own work - `e36eaff` and `de9e790` add nothing but
`related:` entries for ADR-015 and ADR-017 and their `status_history` lines,
each stamped `changed_via: adr-kit lifecycle`. ADR-011's Enforcement block
still carries exactly one forbid rule, the deny-list it was accepted with. The
guard for `install.py` lives in ADR-015's own block, as the task asked.

AC #8's 'or quote the one sentence saying why the arrays are empty'. Quoted:

* ADR-016, on its empty `require_pattern`: "No \: it reads
  whole files and fails every commit until the code lands."
* ADR-015 and ADR-017 carry empty `forbid_import`/`require_pattern` arrays
  too. ADR-015's text explains its three rules as "Three tripwires on the
  obvious form, not proofs" and names the real proof (the syntax-tree
  allow-list test of TASK-089.17); ADR-017's says the same in its own words,
  "Three tripwires on the obvious form, not a proof ... the tests of
  TASK-089.18 are the proof". Neither claims a require rule it has not got.

AC #6's last clause - 'after acceptance the same diff is blocked by
/adr-kit:judge' - was shown with `--dry-run-enforcement`, which is the
pre-acceptance form and reaches the same declarative block. The plain
`adr-judge --diff` over all records was started and stopped: it fans out to
one `claude -p` call per `llm_judge` record, twelve of them. The per-record
runs above each made that call for their own record and all three agreed.

**Correction to the bullet above.** A backtick inside a double-quoted shell
argument made bash substitute a command, and the quoted sentence lost two
words: it reads "No \: it reads whole files" where it should read, in full and
as ADR-016's Enforcement block has it:

    No `require_pattern`: it reads whole files and fails every commit until
    the code lands.

Nothing else in that note was affected, and no ADR was touched.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
ADR-015, ADR-016 and ADR-017 are each Accepted by Robert (2026-09-21), one decision per record, each carrying its alternatives and his own answers. Verified: ADR-INDEX.md and ADR-INDEX.json were written by the kit and docs/adr is clean; adr-judge --dry-run-enforcement flags the probe diff for each record - ADR-015 5 violations including 'import scribe' in install.py and both added secret flags, with the launcher's existing line 645 unflagged because a forbid rule reads added lines only; ADR-016 4; ADR-017 4, with the honest pinned-artifact form passing. The task-level cycle around ADR-017 was broken by answering its Open Question with an explicit deferral ('DEFERRED, NOT MEASURED'), not with the sandbox run - that measurement, the Windows signer and the Linux digest stay TASK-089.18's. No code changed.
<!-- SECTION:FINAL_SUMMARY:END -->
