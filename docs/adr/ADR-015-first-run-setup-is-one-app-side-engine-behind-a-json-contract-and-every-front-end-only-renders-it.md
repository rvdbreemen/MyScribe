---
id: "ADR-015"
title: "First-run setup is one app-side engine behind a JSON contract, and every front-end only renders it"
status: "Accepted"
date: "2026-09-21"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-001"
  - "ADR-011"
  - "ADR-012"
  - "ADR-013"
  - "ADR-016"
  - "ADR-017"
topics:
  - "setup"
  - "installer"
  - "packaging"
  - "secrets"
aliases:
  - "first run"
  - "--plan"
  - "--apply-stdin"
  - "setup.json"
  - "pointer file"
components:
  - "scribe.setup"
  - "packaging.launcher"
  - "install.py"
symbols:
  - "setup_command"
  - "run_streaming"
  - "home_dir"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-015 First-run setup is one app-side engine behind a JSON contract, and every front-end only renders it

## Status

Accepted, 2026-09-21.

## Status History

```yaml
status_history:
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-011
    changed_via: adr-kit lifecycle
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-001
    changed_via: adr-kit lifecycle
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-012
    changed_via: adr-kit lifecycle
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-013
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-016
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-017
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert on 2026-09-21, asked for in the session as 'accept adr-015 en adr-017'. Its one open question was measured rather than waved through before this: both of MyScribe's own probes do fail behind a proxy variable, and the Ollama one reports a running daemon as not running. That measurement changes nothing in the decision - setup is one engine behind a JSON (JavaScript Object Notation) contract and every front-end only renders it - and it gives TASK-089.05 a red-first test to write. Accepting turns this record's three Enforcement rules on for every commit from here, including the one that asks the line refusing --hf-token to say 'refuse' on itself."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

MyScribe's first run asks four questions in a fixed Tk form inside the frozen
launcher (`ask_setup`, `packaging/launcher/myscribe_launcher.py:515-594`) and
hands the answers to `python -m scribe.setup` as command-line flags
(`setup_command`, `:466-485`). On 2026-09-20 Robert asked for one installer for
every platform and for a plain clone, that looks before it asks, asks for a
provider key when one is needed, and lets every question be skipped. He grilled
this record the same day and split it in three: this one keeps the engine and
its contract, ADR-016 the provider default, ADR-017 third-party software. "The
spec" below is `docs/superpowers/specs/2026-09-20-installer-design.md`.

Read in the code on 2026-09-20; a line reference with no file name is the
launcher, and numbers under `scribe/` are those of commit 360ce1b, in which
TASK-089.03 landed.

* **The questions live in one door.** `scribe.setup` takes flags only
  (`scribe/setup.py:149-158`), so a clone and a headless start are asked nothing.
  The form opens on a hard-coded provider and tier (`:558`, `:565`), and the
  install path neither looks for a key or a login nor takes a provider key, as
  Settings does (`scribe/web/settings.py:926-946`).
* **Secrets ride on the command line.** The token goes out as `--hf-token
  <value>` (`:475-476`) to a child that lives for the whole model download, and
  `run_streaming` gives it no stdin (`:275-280`). A provider key sent that way
  would put a billable credential in the process list (`README.md:224` documents the flag).
* **Everything a release installs hangs off one home** (`:95-106`, `:200-207`,
  `scribe/paths.py:40`). It moves only with `MYSCRIBE_HOME` or `--home`
  (`:59-64`, `:693`), which nothing persists or asks, and the first sync alone
  is about 3 GB (gigabytes) on Windows and Linux (`:434-435`).
* **A proxy variable hijacks the loopback.** Measured on Robert's Windows
  machine on 2026-09-20, `HTTP_PROXY` on a closed port: default `urllib` and
  `httpx2` clients failed to reach `127.0.0.1:11434` after 2.12 s and 2.23 s and
  answered in under 0.1 s with the proxy bypassed (References). The product's
  probes use those defaults (`scribe/llm/ollama.py:170`, launcher `:309`).

One recorded choice is revised knowingly: "Four answers, and no more than four"
(`scribe/setup.py:9`, launcher `:518`, TASK-040.06). Its reason stays - each
question is something the app cannot work out for itself - and the number goes:
Robert added adopting a library, a watched folder and start at login. The list,
and what is deliberately not asked, is in the spec (§0, §1).

## Decision Drivers

* A question exists once: every door - the window, the console, a clone, CI (continuous integration) -
  shows the same list.
* Look before asking. Say where a thing was found; never show it, put it on a
  command line or copy it to somewhere less protected.
* Every question can be skipped, a skip writes nothing, and no answer can be
  given only during install (`scribe/setup.py:21-23`).
* The launcher stays stdlib-only (ADR-011); the card stays the runner's (ADR-001).
* As much of the first run as possible is provable with no person at a screen.

## Considered Options

* One app-side engine, `python -m scribe.setup`, behind a versioned JSON (JavaScript Object Notation) contract,
  rendered by thin front-ends.
* The questions as pages in the Inno Setup wizard.
* A `/welcome` page in the web UI (user interface), shown after the app has started.
* Ask everything before the sync.
* Within the first: `install.py` shares the launcher's code, not only its sequence.

## Decision Outcome

Chosen option: **one app-side engine behind a versioned JSON contract**: only
there does a question exist once, does detection use the app's own lookup order
and not a second copy, and does no secret cross a process boundary on a command
line. Four rules below are Robert's, decided in the grill of 2026-09-20 and
signed under Open Questions: `install.py` shares the sequence and not the code,
`/health` gains two fields, `--hf-token` goes now, `--diarize/--no-diarize` stays.

`python -m scribe.setup` is the only code that detects, decides which questions
are open, checks an answer, writes it and proves the result. `--plan` prints one
JSON document: a `contract` number; what was found and where - a name and its
sources, never a value; the open questions, each with its default, a `shown_if`
condition, what skipping costs and where it can be answered later; and what
this platform and tier would download, in bytes. `--apply-stdin` reads one JSON
document of answers, `--prove` ends a sitting on a report of measurements, and
run bare on a TTY (a terminal device) the engine asks itself, with hidden input for secrets. The
front-ends - the Tk dialog, the console, `install.py`, CI - start that child,
draw the plan, send what was typed and show what comes back. Both sides ship
together (`:74-83`, `pyproject.toml:63-65`), so the contract needs its number
and no compatibility window.

**Secrets.** A typed secret goes to its settings row and nowhere else: that row
is what Clear in Settings removes (`scribe/web/settings.py:517-519`) and what
the lookups that serve a job read first (`scribe/llm/base.py:252-258`,
`scribe/stages/diarize.py:265-271`); today's setup also copies the token to
`.env` (`scribe/setup.py:105-107`), which nothing restricts. A found credential
is used where it is: a login file rotates, a copy goes stale. The doctor
(`scribe/doctor.py:482`) and `scribe.models --fetch` (`scribe/models.py:307-312`)
do not read the row today, so the row-only rule waits for the resolver of TASK-089.04.

**The gate.** The stamp replaces `setup.json`, whose presence alone meant
"asked" (`scribe/setup.py:60-61`), and whether a start opens a sitting is read
from it as data, the way `app_version` reads the version (`:119-126`). A skipped
id opens none; neither does one a failure left open, or a state that opened
again later, such as a removed token: `--plan` says so and `--setup` reaches it.
That reading of "does not nag" is the agent's; reopening once after a failure
is the alternative, and Robert's call.

**Two doors.** `install.py` follows the launcher's sequence - tools, sync, plan,
sitting, apply, prove, start - and shares none of its code, so the launcher of
v0.5.0 and v0.5.1 gains no branch only a clone reaches. It shares fetching and
sha256-checking the tools with `packaging/build_payload.py`, and one contract
test guards the plumbing both doors duplicate (Open Questions).

**`/health`** gains the source tree the app runs from and the data directory it
serves (`app_dir` and `data_dir` in the spec, §3.9). They tell `install.py`
whether it would sync under a running app, and the proof whether the app that
answers serves this library; an answer without them is doubt, and doubt
refuses. No web page reads the answer (`scribe/guard.py:110`). The blind spot is
kept and named: an app on a port nobody mentioned, such as `--port 4299`.

**The proof.** Starting the app on the real data directory migrates the
database and queues jobs (`scribe/app.py:247-261`), so the serve check never
does that. With no app answering and no job running, `--prove` measures in its
own process, exactly as `python -m scribe.doctor` does from a terminal
(`scribe/doctor.py:381`). That command is the precedent, and why this is no
breach of ADR-001, whose Exceptions read "None": nothing else holds the card.

### Confirmation

Nothing here exists yet. Each Must names the task that owes its test, red first
where the code is wrong today (TASK-089.04, TASK-089.05). Three tests carry the
most weight: a marker value planted in every credential source appears in no
output, stamp, log or argv (TASK-089.09, TASK-089.15); with a model loader that
raises, prove loads nothing while `/health` answers or a job runs (TASK-089.13);
and the contract test between the doors, which TASK-089.17's criterion #17
owes. Only a person can confirm the real Tk window, once per operating system;
nobody has, and a run that skips it says "not run".

## Decision Contract

### Must

* `python -m scribe.setup` alone detects, decides what is open, checks, writes
  and proves. `--plan` goes out and `--apply-stdin` comes in, each one JSON
  document carrying the `contract` number.
* Detect before asking. A plan made for a start lists the questions that are
  open and were never put; one asked for with `--setup` lists all, with current
  values. It names where a credential was found, never its value. Its loopback
  probes ignore a configured proxy; a found proxy is shown, not asked about (TASK-089.05).
* Every question can be skipped, says what skipping costs and names where it
  can be answered later; a missing or null answer writes nothing. One whose
  answer is a stored setting waits until Settings can take it (TASK-089.21).
* A typed secret goes to its settings row only, once every lookup reads that
  row through the resolver of TASK-089.04. A refused credential is not saved
  and its question reopens; an OpenRouter key is checked for free first.
* `--hf-token` is still recognised and is refused: a sentence saying where a
  token belongs - `HF_TOKEN` in the environment or `.env`, or the document piped
  to `--apply-stdin` - and exit 2 (TASK-089.09); the other flags of `README.md:224` keep working.
* "Save and start" writes the stamp - the contract version and the ids
  answered, skipped and open - failed downloads included; closing the sitting
  writes none (`:527-528`, `:590-594`). A start opens a sitting when there is no
  stamp, once for an old-format stamp and only for what it never covered, and
  when the stamp's contract version is older than the payload's; deciding that
  starts no child (TASK-089.11). With no window and no terminal, one line says so.
* `install.py` runs on the Python a clone user already has, with the pinned,
  sha256-verified uv of `packaging/tools.json`; it asks `/health` on 4242 or on
  `--port` and refuses to sync when the source tree is this checkout or the two
  fields are missing (TASK-089.17). One contract test gives both doors the same
  lock and stamp and demands the same sync decision.
* `--prove` exits 0 only when every required line is OK; an untestable line
  reads "not tested" with where to finish it. While the app answers `/health` or
  a job runs in this library, it queues the `doctor` job or says "not tested" (TASK-089.13).

### Must Not

* Put a secret on a command line, in the plan, in the stamp, in a log or on the
  screen, or copy it from where it was found into another store. No exception.
* Let a front-end decide, check or write an answer, or interpret a condition
  other than `shown_if`.
* Load a model in the setup child while the app answers `/health` or a job runs
  in this library (ADR-001), or start a second app on the live data directory:
  the serve check uses a scratch data directory, or "`/health` already answers" (W1).
* Write `default_diarize` from setup by itself: not from a plan question, not as
  a guard (TASK-089.08 protects instead), and not as the side effect a tier
  answer has today (`scribe/setup.py:116-126`, TASK-089.09). The scripted
  `--diarize/--no-diarize` flag is the one writer setup keeps.
* Import in `install.py` the launcher or anything the environment would provide:
  it may import the standard library and the stdlib-only `packaging/build_payload.py`.

### Exceptions

* **Where everything goes.** The engine cannot ask where it will live, so a
  release asks before the sync where the environment, the data and the weights
  go, with the free space per volume and what this install needs (TASK-089.14).
  The launcher keeps the answer in a small pointer file next to the default
  home, which `home_dir()` reads with the standard library, so inside ADR-011.
  It is the only answer a front-end takes and writes, and it has no counterpart
  in Settings, which lives under that home; whether the file also names an
  adopted library is TASK-089.19's (Open Questions).
* **macOS** is unproven, not different: its criteria are bundled per milestone
  and read "not run" until that sitting (G9, Robert's; Open Questions).

### Verification

* `grep -n -- "--hf-token" packaging/launcher/myscribe_launcher.py` returns
  line 476 on 2026-09-20 and nothing after TASK-089.15.
* `probe_proxy_loopback.py` (References): two failures and two answers on
  2026-09-20; TASK-089.05's test is what holds after the change.
* `adr-judge --dry-run-enforcement ADR-015`, as run under Enforcement.

## Consequences

### Positive

* Adding a question is adding data in one place; every door picks it up.
* Secrets leave the command line, with no exception; typed ones have one store.
* Clone users, headless starts and CI walk the path release users walk, and
  empty answers make an unattended install.

### Negative

* Two waits with a sitting between them, plus the one question before the sync;
  the launcher grows - it draws a plan, feeds stdin, shows progress and errors -
  and its Tk dialog still needs a person at a screen, once per operating system.
  A Windows release has no console door (frozen `--windowed`, `packaging/build_release.py:81`).
* `/health` moves a body that `tests/test_app.py:45-48` pins exactly, and a
  script that passes `--hf-token` stops working, with a sentence and exit 2.
* Typed secrets live in the library's database, so a copy of the library
  carries them (ADR-013), as it already does for a key typed in Settings.
* `packaging/fetch_models.py:58` still takes `--token`: a build tool, not a door.

## Pros and Cons of the Options

### One app-side engine behind a JSON contract (chosen)

* Good, because a question, its default and its cost of skipping exist once,
  and detection is the app's own lookup order, not a stdlib copy of it.
* Bad, because it asks after the sync: two waits, not one.

### Pages in the Inno Setup wizard

* Good, because that is the moment a Windows user already calls installing.
* Bad, because only Windows has a wizard - one task and no code section today
  (`packaging/windows/myscribe.iss:31-32`) - and it runs before the environment
  exists: the `.dmg`, the AppImage and a clone would ask a second time, nothing
  could be detected or checked, and no test here drives Pascal.

### A /welcome page in the web UI (runner-up)

* Good, because the launcher shrinks: the Tk form goes, and the launcher never
  touches an answer or a secret, not even over stdin.
* Good, because every question is testable with the test client on all three CI
  runners; a headless start, an AppImage without Tk and Windows get the same
  page, and Settings already takes the keys over a loopback form.
* Bad, because the app starts before anything is answered; ADR-016 takes most
  of the sting out of that, not the redirect: bouncing `/` to a wizard while
  `setup.json` is missing would bounce an existing library too (44 call sites in
  10 test files get `/`, counted 2026-09-20).
* Bad, because a third-party installer (ADR-017) would hold the only job lane
  (`scribe/jobs.py:142-150`) for a download of over a gigabyte, or run from the
  web process, and either way sits in the tree Quit force-kills on Windows (`:376-382`).

### Ask everything before the sync

* Good, because it is one attended minute and then everything runs unattended.
* Bad, because the environment does not exist yet: the lookup order would be
  written a second time in stdlib code, in the program whose docstring says it
  "should not learn how" (`:469-471`), and answers, secrets included, would be
  held across a 3 GB sync. Only the location question cannot come later.

### install.py shares the launcher's code

* Good, because there is one bootstrap function and one test for both layouts.
* Bad, because it rebuilds shipped code whose frozen path only the release smoke
  and a person at the screen exercise; Robert chose the sequence (Open Questions).

## Open Questions

- [x] Does `install.py` share the launcher's code or only its sequence? If code: one bootstrap, and the frozen launcher carries branches only a clone reaches and must run under a clone user's Python. If sequence: the launcher stays as it is and the bootstrap exists twice. This record leans to sequence; the count of duplicated lines that would settle it has not been made. — **Answered 2026-09-20 by User: Robert van den Breemen:** Only its sequence, with a contract test. The count was made on 2026-09-20: `packaging/launcher/myscribe_launcher.py` is 758 lines, and about 100 of them are plumbing both doors need - the lock digest, the stamp, the sync decision, running a child, the setup call; the home, the environment and the tools differ per door. The part where drift would hurt is not in the launcher at all: the launcher copies its tools out of the payload, and fetching them and checking their sha256 is done in `packaging/build_payload.py`, which is what `install.py` shares that part with. So the launcher stays as it was built and shipped in v0.5.0 and v0.5.1, with the 26 tests of `tests/test_launcher.py` pinning it, and gains no branch only a clone reaches. Against drift in the duplicated plumbing, one contract test gives both doors the same lock and stamp and demands the same sync decision. A shared module under `packaging/launcher/` was weighed and is feasible - PyInstaller follows imports (`packaging/build_release.py:89`) and the import guard already covers that folder - and was set aside because it rebuilds shipped code whose frozen path only the release smoke and a person at the screen exercise.
- [x] How does `install.py` know that the MyScribe answering `/health` runs from this checkout, so that it refuses to sync under it? `/health` returns only `ok` and `version` (`scribe/app.py:296-298`) and the launcher's single-instance rule reads only `ok` (launcher `:306-312`). The candidate, set out in `docs/superpowers/specs/2026-09-20-installer-design.md`, is two more fields naming the source tree and the data directory, with an answer that lacks them treated as doubt. It moves a body that `tests/test_app.py:45-48` pins exactly, and it cannot see an app on a port nobody named. If Robert judges that unsound, the refusal becomes a warning. A lock or pid file is what ADR-001 and ADR-013 avoid. The same blind spot bears on the prove rule: `--port 4299` is this repository's documented way to run a second app beside the one on 4242 (the project's instruction file, under Commands), and such an app does not answer the `/health` that prove asks. TASK-089.13 adds a check for a running job row in the same library, which needs no port; what that still misses - an app on another port, serving another library, on the same card - is said in the report and is open here. — **Answered 2026-09-20 by User: Robert van den Breemen:** By two more fields in what `/health` answers: the source tree the app runs from and the data directory it serves. `install.py` asks the port it was given - 4242, or `--port` - and refuses to sync when the source tree is this checkout, goes on when it is another, goes on when nothing answers, and treats an answer without the two fields (an older MyScribe) as doubt, which refuses. The same two fields answer the proof's question, whether the app that answers serves this library. It gives nothing away: the host check covers the whole app (`scribe/guard.py:110`), so a web page cannot read the answer, and a local process that can read it can read the file system as well. It moves `scribe/app.py:296-298` and the test that pins the body exactly (`tests/test_app.py:45-48`); the launcher reads only `ok` (launcher `:306-312`) and notices nothing. The blind spot is kept and named: an app on a port nobody mentioned is not seen, and this repository's own `--port 4299` is such a case. A row in the database that the running app writes was weighed - it would see an app on any port and fits ADR-013 - and set aside for now because it needs a stale row handled after a crash and opens the live database from an installer, for a case that `--port` already covers when somebody knows about it. A warning in place of a refusal was set aside because what a sync does to locked files on Windows has been measured by nobody.
- [x] The proxy behaviour was measured for the two library defaults on one Windows machine with a proxy variable (Context); that MyScribe's own two probes fail the same way is still an inference. Not measured at all: a Windows system proxy set in the registry and not in a variable, macOS, and Linux. TASK-089.05 runs the product's probes first and pins the bypass with a test that is red first under a dead proxy; whoever has the Mac and the Linux machine answers the rest, and until then the presence test's "any doubt reads as present" is what protects an Ollama behind a proxy. — **Answered 2026-09-21 by Claude (agent, session 2026-09-21):** Measured on 2026-09-21 on Robert's Windows machine, and the inference holds for both: with HTTP_PROXY and HTTPS_PROXY at a closed port and NO_PROXY unset, OllamaProvider.available() spends its 2 s budget and answers (False, 'Ollama is not running at http://127.0.0.1:11434 (start it ...)') for an Ollama that is running, and the launcher's running_instance() answers False after 1.08 s for a MyScribe that is answering. Setting NO_PROXY=127.0.0.1,localhost restores both (0.01 s and 0.06 s). The Ollama half is worse than a failure: it is a confident wrong sentence telling somebody to start what is already started, and by ADR-017's presence test that reads as absent, which is what the offer to install is gated on. The launcher half breaks the single-instance rule, so a second app would be started on a port that is already served. Both are TASK-089.05's to fix with a red-first test. Two traps had to be cleared before the numbers meant anything, and both first gave the opposite answer: OllamaProvider.client() caches in self._client and httpx reads the proxy when the client is built, so one provider asked twice measures the first environment twice; and urllib.request keeps its default opener in the module with the proxies it was built from, so install_opener(None) is needed between cases. The probe and its output are in docs/superpowers/specs/2026-09-20-installer-evidence/. Still not measured, and still inference: a Windows system proxy set in the registry rather than in a variable, macOS and Linux.
- [x] Is there a free way to check an OpenRouter key? None was verified. Without one, "a refused credential is not saved" holds for OpenRouter only after the paid one-word probe, which runs on consent. — **Answered 2026-09-20 by Claude (agent, session 2026-09-20):** Yes. An authenticated request to `https://openrouter.ai/api/v1/key` costs nothing and is answered 401 for a key that is missing, invalid or disabled; a valid key gets its limit, its remaining credit and its usage back. Two sources, both 2026-09-20: OpenRouter's documentation (openrouter.ai/docs/api-reference/limits), read through a summarising fetch and not word for word, and a probe from Robert's machine that sent no key - `/api/v1/key` answered 401 while a nonsense path answered 404, so the address exists and asks for credentials. Not run: a call with a real key, valid or revoked, because no key of Robert's was used to find this out. So a refused OpenRouter key can be turned away without the paid one-word probe; TASK-089.09 builds the check, and its first run with a real key is the proof still owed.
- [x] Does `--hf-token` survive at all? A secret never rides on a command line (Must Not), and this flag does. Keeping it, deprecated, honours `README.md:224`, which documents it for people's own scripts. Removing it in TASK-089.09 and correcting the README honours the rule, and the second Enforcement pattern then loses its allowance for the flag. Launcher and app ship together, so nothing in the product needs a window. Robert decides. — **Answered 2026-09-20 by User: Robert van den Breemen:** No. It goes in TASK-089.09, and it goes with a sentence and not with an argparse error: the flag is still recognised and is refused, saying that a token on a command line can be read by other processes and ends up in shell history, and that the token belongs in `HF_TOKEN` - in the environment or in `.env`, where setup now finds it by itself - or in the document piped to `--apply-stdin`; exit 2. It costs nothing that works today: detection already looks in the environment, in `.env` and in the registry, the product stops passing the flag once the launcher pipes its answers (launcher `:475-476` does so now, and `tests/test_launcher.py:404` pins it and moves with it), the flag has existed since 2026-09-18, and the line in `README.md:224` that documents it was written on 2026-09-20 by the same session that raised this question, so it is no evidence that anybody relies on it. What it buys: the Must Not has no exception, and the second Enforcement pattern loses its allowance for the flag. A deprecation window for one release was weighed and set aside, because it protects a script nobody is known to have written while leaving the rule with a hole in it.
- [x] Does `--diarize/--no-diarize` go from `scribe.setup`? This record and `docs/superpowers/specs/2026-09-20-installer-design.md` say yes: the flag is the one path by which setup writes `default_diarize` today (`scribe/setup.py:125-136`, `:164-165`), no question asks it and no front-end sets it, and "setup does not write the row" can only be checked when no path does. The other way keeps it as an explicit-only flag outside that rule, because somebody who types `--no-diarize` is choosing and not guarding, and `README.md:224` documents it; the Must Not then names it as the one writer setup keeps. Robert decides. — **Answered 2026-09-20 by User: Robert van den Breemen:** No, it stays, as an explicit choice and nothing else. What had to go was setup switching the speakers default off BY ITSELF when no token was found, on a row that every upload rewrites; that guard is gone either way. Somebody who types `--no-diarize` is choosing, not guarding, and the flag has shipped in v0.5.0 and v0.5.1 (`scribe/setup.py:164-165`), so taking it away would break a command line people may have scripted - an unattended install on a machine that will never separate speakers is the case that wants it. Its removal came from this record reading 'setup does not write the row at all' literally, not from anything Robert asked for. So the Must Not names the flag as the one writer setup keeps, the test for 'setup does not write the row' covers every path except that one, and the text of this record and of the spec that says the flag is removed is corrected. Unchanged and still to be fixed under TASK-089.09: a tier answer alone writes the diarize row today, because `save_defaults` writes both together (`scribe/setup.py:125-136`).
- [x] How does a release point at an adopted library? The launcher forces `SCRIBE_DATA_DIR` (launcher `:203`), so `.env` cannot carry it. `docs/superpowers/specs/2026-09-20-installer-design.md` proposes a second fact in the pointer file, decided by the engine and written by the launcher, which would make that file more than one line and the launcher the writer of a value it did not ask for. TASK-089.19 settles it before building, and this record's Exception is amended to match. — **Answered 2026-09-20 by Claude (agent, session 2026-09-20):** That is TASK-089.19's to settle with a test, and it is below the level of this record. What this record fixes is the principle: where things live is kept in a small file that the launcher can read with the standard library before anything else exists, the launcher writes it only on the engine's instruction and never decides its content, and the app is never asked to move a library. Whether that file holds one fact - the home - or a second one for a library adopted in place follows from that principle either way, because the launcher stays a reader and a writer of facts it was handed. So the wording 'one-line' in the Exception is loosened to 'a small pointer file' when this record is split, and the second fact remains the spec's proposal until TASK-089.19 has built and measured it. Proposed by the agent on 2026-09-20; Robert sees it in the acceptance packet and can overturn it there.
- [x] macOS is unverified throughout: the `.dmg` flow, the install locations the presence test looks in, the console asker under a Mac terminal, and whether mlx-whisper loads its weights from a local folder, which the platform-aware catalogue of TASK-089.16 rests on. Who has the Mac answers it, and until then every task that touches macOS reports it as not run. — **Answered 2026-09-20 by User: Robert van den Breemen:** The Mac belongs to somebody else and can be asked for now and then, so macOS questions are bundled and not asked one at a time. Each milestone collects its macOS criteria into one list - per point the command to run and the output to expect - so that the machine's owner can run all of it in one sitting. Until that sitting every macOS claim stands in its task as 'not run', no criterion that needs a real Mac is checked on an assumption, and the release notes say what continuous integration proves on its macOS runner - the build, the first sync, the health answer, a page, the stop - and nothing more. The decision in this record does not wait on it: nothing here is different on a Mac, only unproven there. It also settles a contradiction in the repository that the grill turned up: the README presents an Apple M2 as verified, a task of 2026-09-19 reports a session on a Mac, and an older task calls that machine somebody else's; the README sentence gets its date and says whose machine it was.

## Related Decisions

* ADR-016 makes "a skip writes nothing" honest for the provider: nothing written
  means nothing sent, also when setup adopts an existing library.
* ADR-017 owns third-party software: the Ollama states the plan reports, the
  install offer, the marker, and how Quit stops the setup child.
* ADR-011 (the launcher) is extended, not edited: it still imports nothing from
  the app and writes no setting row. Its Enforcement cannot reach `install.py`;
  this record's block does (Enforcement).
* ADR-001: see "The proof". ADR-012: both doors run `uv sync --frozen`. ADR-013: no lock or pid file is added.

## References

* `docs/superpowers/specs/2026-09-20-installer-design.md`, the design;
  `2026-09-20-installer-decisions.md` beside it, the key to its short codes and
  to the grill (G1-G9); `2026-09-20-installer-evidence/`, the proxy probe.
* `packaging/launcher/myscribe_launcher.py`, `scribe/setup.py`, backlog TASK-089.
* https://openrouter.ai/docs/api-reference/limits - the free key check.

## Enforcement

Three tripwires on the obvious form, not proofs. The judge tests the added
lines of a diff, one at a time, for Accepted records only, so launcher line 476
is not flagged until touched; TASK-089.15 deletes it. The first rule misses a
secret passed positionally; the argv test of TASK-089.15 is the proof. The
second is the same rule on `scribe/setup.py` with no allowance for `--hf-token`:
the line that refuses it (TASK-089.09) is flagged when touched, like line 476.
The third is ADR-011's deny-list for `install.py`, plus the launcher module;
the proof is the syntax-tree allow-list test of TASK-089.17. Tried on 2026-09-21
with adr-kit 0.57.0 on a scratch copy marked Accepted: `import scribe`,
`from packaging.launcher import myscribe_launcher` and `from packaging import launcher`
in `install.py`, `command += ["--llm-key", key]` and `["--hf-token", token]` in the
launcher, and `parser.add_argument("--llm-key", default="")` and the `--hf-token`
refusal line in `scribe/setup.py` were flagged; `from packaging import build_payload`
and `"--apply-stdin"` passed.

```json
{
  "forbid_pattern": [
    {"pattern": "[\"']--[a-z0-9-]*(?:token|key|secret|password)[a-z0-9-]*[\"']", "path_glob": "{packaging/launcher/**,install.py}", "message": "A secret never travels on a command line: send the answers to scribe.setup on stdin (ADR-015)."},
    {"pattern": "[\"']--[a-z0-9-]*(?:token|key|secret|password)[a-z0-9-]*[\"']", "path_glob": "scribe/setup.py", "message": "scribe.setup takes secrets on stdin, and --hf-token is refused, not read (ADR-015)."}
  ],
  "forbid_import": [
    {"pattern": "^\\s*(?:(?:import|from)\\s+(?:scribe|torch|faster_whisper|ctranslate2|pyannote|fastapi|uvicorn|myscribe_launcher|packaging\\.launcher)\\b|from\\s+packaging\\s+import\\b.*\\blauncher\\b)", "path_glob": "install.py", "message": "install.py runs before the environment exists and shares the launcher's sequence, not its code: stdlib and packaging/build_payload.py only (ADR-015)."}
  ],
  "require_pattern": []
}
```
