---
id: "ADR-015"
title: "First-run setup is one app-side engine behind a JSON contract, a skipped question selects nothing, and third-party software is installed only when absent, shown and agreed to"
status: "Proposed"
date: "2026-09-20"
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
topics:
  - "setup"
  - "installer"
  - "packaging"
  - "secrets"
  - "ollama"
  - "llm"
aliases:
  - "first run"
  - "scribe.setup"
  - "install.py"
  - "--plan"
  - "--apply-stdin"
  - "setup.json"
  - "Ollama install"
  - "no provider row"
components:
  - "scribe.setup"
  - "packaging.launcher"
  - "install.py"
  - "scribe.llm"
symbols:
  - "default_provider"
  - "sweep_speaker_passes"
  - "setup_command"
  - "run_streaming"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-015 First-run setup is one app-side engine behind a JSON contract, a skipped question selects nothing, and third-party software is installed only when absent, shown and agreed to

## Status

Proposed, 2026-09-20.

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
```

## Context and Problem Statement

MyScribe's first run asks four questions in a fixed Tk form inside the
frozen launcher (`ask_setup`, `packaging/launcher/myscribe_launcher.py:515-594`)
and hands the answers to `python -m scribe.setup` as command-line flags
(`setup_command`, `:466-485`). On 2026-09-20 Robert asked for one installer
that works on every platform and from a plain clone; that looks before it
asks; that asks for a provider key when one is needed and lets every
question be skipped; and that offers to install Ollama and a model - but
only when Ollama is absent: "Als Ollama er al is, dan niets doen."

The facts that shape the answer, read in the code on 2026-09-20 (a line
reference with no file name is `packaging/launcher/myscribe_launcher.py`):

* **The questions live in one door.** The form is Tk code in the launcher,
  and `scribe.setup` takes flags only (`scribe/setup.py:158-167`): it cannot
  ask. A clone user is asked nothing, and neither is a headless start. A
  question added to the form has to be added again wherever else it belongs.
* **The form cannot see the machine.** It opens on a hard-coded provider and
  tier (`:558`, `:565`), always shows the token field (`:547-549`), and
  nothing on the install path looks for a key, a Hugging Face login or
  Ollama. OpenRouter and OpenAI can be chosen (`:561`), and the install path
  has no question, flag or write path for their API (application programming interface) key;
  Settings has one (`scribe/web/settings.py:926-946`).
* **Secrets ride on the command line.** The token goes out as
  `--hf-token <value>` (`:475-476`) to a child that stays alive for the whole
  model download, and it is the only channel there is: `run_streaming` gives
  the child no stdin (`:275-280`). A provider key added the same way would
  put a billable credential in the process list.
* **The order is wrong on a fresh machine.** `begin()` runs setup first and
  `launch.run()` second (`:651-668`), and `launch.run()` is what creates the
  environment (`:430-439`), so on a fresh home the interpreter that setup is
  started with does not exist yet. Established by reading; nobody has watched
  it in a real Tk window. It is fixed on its own in TASK-089.01.
* **The gate is a file's existence.** `setup.json` present means "asked"
  (`scribe/setup.py:60-61`, launcher `:462-463`). Its content is
  `{provider, tier, hf_token, fetched}` (`scribe/setup.py:143-154`), with no
  record of what was skipped.
* **Launcher and app ship as one payload.** The payload sits inside the
  frozen bundle (`:74-83`) and the app runs from that source tree, not from
  an installed package (`pyproject.toml:63-65`, launcher `:207`). In a clone,
  the installer and `scribe/` come from the same checkout. Both sides of any
  contract between them are always the same version.
* **Everything a release installs hangs off one home.** `env/`, `python/`,
  `cache/` and `data/` are properties of the home (`:95-106`), the launcher
  forces `SCRIBE_DATA_DIR` to `<home>/data` (`:200-207`), and the weights live
  under the data directory (`scribe/paths.py:40`). The home moves only with
  `MYSCRIBE_HOME` or `--home` (`:59-64`, `:693`), which nothing persists and
  nothing asks. The first sync alone is about 3 GB (gigabytes) on Windows and
  Linux, by the launcher's own message (`:434-435`).
* **The card belongs to the runner.** ADR-001 keeps GPU (graphics processing unit) work inside runner children,
  one at a time, and ADR-013 made that a clause in the claim
  (`scribe/jobs.py:142-150`). Settings therefore runs its model-loading
  checks as a queued `doctor` job (`scribe/web/settings.py:8-14`,
  `:154-163`). On Windows the launcher's Quit is `taskkill /T /F` on the
  app's process tree (`:376-382`).
* **Quit does not reach the setup child.** `quit_app` calls `launch.stop()`
  and nothing else (`:617-621`); that stops the app process and does nothing
  while there is none (`:452-454`), which is the case during setup.
  `run_streaming`, which runs the sync and the setup child, keeps no handle
  on its child (`:275-286`), and the thread that runs it is a daemon
  (`:659-666`). So today a Quit during setup closes the window and stops
  nothing. Established by reading; nobody has watched what becomes of that
  child.
* **Ollama's own installers do not know "only when missing".** As served by
  ollama.com on 2026-09-20: `install.sh` on macOS stops a running Ollama and
  removes `/Applications/Ollama.app` before it downloads (`install.sh:60-69`);
  on Linux it requires root or sudo (`install.sh:110-118`), removes an
  existing library directory (`install.sh:164-167`), writes and restarts a
  systemd unit (`install.sh:215-238`) and can install graphics drivers through
  the package manager (`install.sh:353`, `install.sh:381`). `install.ps1`
  installs unconditionally (`install.ps1:319-323`), running `OllamaSetup.exe`
  with its silent-install switches (`install.ps1:271`).
* **A proxy variable hijacks the loopback.** Measured on this Windows
  machine on 2026-09-20, with Ollama 0.34.0 answering
  (`probe_proxy_loopback.py` and its output, under References): with
  `HTTP_PROXY` and `HTTPS_PROXY` set to a closed port and no `NO_PROXY`, a
  default `urllib` opener and a default `httpx2` 2.12.0 client both fail to
  reach `127.0.0.1:11434`, after about 2 s; `ProxyHandler({})` and
  `trust_env=False` answer in under 0.1 s. That is the two library defaults,
  not the product. The Ollama provider builds a default client
  (`scribe/llm/ollama.py:170`) and the launcher's `/health` probe is a
  default `urlopen` (`:309`); that those two fail the same way is an
  inference nobody has run, and TASK-089.05 runs it before it fixes anything. "No
  answer on the loopback" is one leg of "absent", and absent is what licenses
  the install offer.

Two recorded choices are revised knowingly by this record.

* **"Four answers, and no more than four"** (`scribe/setup.py:9`, launcher
  `:518`, the final summary of TASK-040.06); the CHANGELOG (`CHANGELOG.md:107-110`) says
  the same in its own words. The reason behind the number is kept: each question is something the app cannot
  work out for itself. Detection is what keeps the list short now. The number
  itself goes. On 2026-09-20 Robert added three questions, because each is
  today a separate thing to do afterwards: adopt an existing library, a
  folder to watch, and start MyScribe at login, with No as the default. A
  start script or shortcut for a clone was put to him and not chosen; it is
  not built. Deliberately not asked, so that nobody adds them later: the
  transcription language (removed on evidence on 2026-09-02,
  `scribe/web/transcribe_dialog.py:63-69`), the timezone, the language of the
  interface, and a choice between the card and the CPU (central processing unit), which ADR-012 settled.
  The list and the reasons are in `docs/superpowers/specs/2026-09-20-installer-design.md`.
* **"Defaults: commercial providers (user decision)"**
  (`docs/superpowers/specs/2026-09-01-myscribe-design.md:219`). In code, a
  missing `llm_provider` row falls through to OpenRouter
  (`scribe/llm/tasks.py:456-459`, `scribe/llm/__init__.py:164-175`, and the
  fallbacks at `scribe/stages/llm_stage.py:94`, `:271`, `:345`). That default
  was safe while choosing a provider was something a person did. Once every
  question can be skipped, the skip is the default most people get, and on a
  machine that holds an OpenRouter key anywhere it sends transcript text to a
  cloud nobody chose. The widest path is not a button:
  `finalize.sweep_speaker_passes` runs at every app start
  (`scribe/app.py:261`) over the whole back catalogue with "no ceiling on how
  many it queues" (`scribe/stages/finalize.py:155-220`, docstring `:177-179`),
  through `queue_speaker_pass`, which asks `default_provider`
  (`scribe/stages/finalize.py:437`). Robert reversed the default on
  2026-09-20.

This record is written before the code, on purpose: Robert asked for the
decision, a design spec (`docs/superpowers/specs/2026-09-20-installer-design.md`) and the tasks to be recorded first.

## Decision Drivers

* A question exists once. Every door shows the same list: the window, the
  console, a clone, and CI (continuous integration, the workflows that build and test on every change).
* Look before asking, and say where a thing was found without showing it.
* A secret is never printed, never put on a command line, and never copied
  from where it was found to somewhere less protected.
* Every question can be skipped, so a skip has to be safe: nothing leaves
  the machine because nobody answered.
* "Als Ollama er al is, dan niets doen."
* The launcher stays stdlib-only and learns nothing about the app (ADR-011).
* The card stays the runner's (ADR-001).
* An answer given during install is never the only place it can be given
  (`scribe/setup.py:21-23`).
* As much of the first run as possible is provable without a person at a
  screen.

## Considered Options

* One app-side engine, `python -m scribe.setup`, behind a versioned JSON (JavaScript Object Notation) contract,
  rendered by thin front-ends. It asks after the sync, except the one
  question that can only come before it.
* The questions as pages in the Inno Setup wizard.
* A `/welcome` page in the web UI (user interface), shown after the app has started.
* Ask everything before the sync.
* Delegate "only when missing" to Ollama's own install scripts.
* Within the first option: `install.py` shares the launcher's code through a
  clone layout, or shares only its sequence.

## Decision Outcome

Chosen option: **one app-side engine behind a versioned JSON contract**,
because it is the only option in which a question exists once, detection
uses the app's own lookup order instead of a second copy of it, and no
secret has to cross a process boundary on a command line. It was the
recommendation of the design run of 2026-09-20, where three designs tied at
7.0 and the `/welcome` page was the runner-up. It stays Proposed until
Robert accepts it. The record decides three things, because each is unsafe
without the other two.

### The engine and its contract

`python -m scribe.setup` is the only code that detects, decides which
questions are open, checks an answer, writes an answer and proves the
result.

* `--plan` prints one JSON document: a `contract` number, which is the
  version of this contract; what was found and where -
  a name, a source, any other source that also defines it and whether they
  disagree, never a value; the Ollama state; the open questions; and what
  this platform and tier would download, in bytes. A question carries its
  id, kind, text, choices, current value, default, a `shown_if` condition,
  what skipping costs and where it can be answered later.
* `--apply-stdin` reads one JSON document of answers. A missing or null
  answer is a skip and writes nothing for that question.
* Run bare on a TTY (a terminal device), the engine asks the same questions itself, with
  hidden input for secrets. Without one it asks nothing, says so and writes
  no stamp.
* `--prove` ends a sitting on a report of measurements.

The front-ends - the Tk dialog, the console, `install.py`, CI - start that
child, draw what `--plan` says, send what was typed and show what comes back.
The only condition a front-end interprets is `shown_if`. The contract needs
its `contract` number and no compatibility window, because both sides always
ship together.

A typed secret is written to its settings row and nowhere else, because that
row is what the Clear button in Settings removes
(`scribe/web/settings.py:517-519`, `:940-942`) and what the two lookups that
serve a job read first: the provider-key resolver
(`scribe/llm/base.py:252-258`) and the diarize stage
(`scribe/stages/diarize.py:265-271`, called at `:625`). Today's setup
writes the token to `.env` as well (`scribe/setup.py:114-116`), a second
copy in a file whose permissions
nothing restricts (launcher `:258-263`). A credential that was found is used
where it is: a Hugging Face login file rotates, and a copy goes stale.

Two lookups do not read the row today. The doctor calls `hf_token(None)`
(`scribe/doctor.py:482`), and `python -m scribe.models --fetch` falls back
to `default_token()` (`scribe/models.py:246`, `:307-312`), which reads two
environment variables and nothing else. With the `.env` copy gone, neither
would see a typed token, and a proof built on the doctor would report "no
token" for a token that was just saved. So the row-only rule takes effect
with the single resolver of TASK-089.04 and not before it; the engine depends on
that task.

Whether a start opens a sitting is read from the stamp alone. The stamp
records the contract version and the ids answered, skipped and still open.
A start opens a sitting in three cases: there is no stamp; the stamp is in
the old format, once, keeping its answers as answered and putting only the
questions it never covered; or the stamp's contract version is older than
the payload's, for the questions that version added. A start does not open
one for an id recorded as skipped: that question stays reachable through
`--setup`, the Setup button and Settings. Nor does it open one for an id
that a failure left open, such as a download that did not finish, or for a
state that became open again later, such as a token that was removed: the
report and `--plan` say so, and `--setup` reaches it. That last rule is this
record's reading of "does not nag"; reopening once after a failure is the
alternative, and it is Robert's to choose.

"Save and start" ends a sitting and writes the stamp. Closing the window, or
"Ask me next time", writes none, as today (launcher `:527-528`, `:590-594`),
so that sitting returns at the next start. The comparison reads the stamp
and one number out of the payload as data: a constant read as text, the way
`app_version` reads the version (launcher `:119-126`), or a file holding
that one number, as `docs/superpowers/specs/2026-09-20-installer-design.md` proposes. It starts no child, and how the
number is spelled is TASK-089.11's. A door with neither a window nor a terminal
starts no child because a sitting is due: it says in one line that nothing
was asked and how to ask, and goes on.

Setup does not write the `default_diarize` row at all, in either direction:
it does not switch "Recognise speakers" off to guard against a missing
token, and it does not switch it on. That default is "the last options
submitted", rewritten from seven call sites on every upload
(`scribe/web/ingest_ui.py:375`, `:438`, `:509`,
`:888`; `scribe/web/transcribe_dialog.py:247`, `:280`;
`scribe/web/settings.py:492`), so a note saying setup set it goes stale after
the first upload. The protection is TASK-089.08 alone: a job keeps its transcript
when the speaker weights cannot load, and the note it leaves on the job
links to the token field in Settings. The one path by which setup writes the
row today goes as well: the scripted `--diarize/--no-diarize` flag
(`scribe/setup.py:125-136`, `:164-165`), which no question asks and no
front-end sets, because "setup does not write the row" can only be checked
when no path does. The row stays reachable where it belongs, in the
transcribe dialog and in Settings. This is a choice made for this record,
with that reason; it is Robert's to overturn, and keeping the flag is under
Open Questions.

### Installing somebody else's software

MyScribe installs third-party software only when every one of these holds:
the software is absent by MyScribe's own test, in which any doubt reads as
present; the exact command line and the exact artifact have been shown before
the question, the artifact by its URL (Uniform Resource Locator), its size and its sha256; the answer was an
explicit yes, with No as the default; the artifact is pinned and its sha256 verified
before anything runs; no remote script is piped into a shell; and on Linux
MyScribe shows the vendor's commands - download, read, run - and the person
runs them, because a root script that can install drivers is not something
to run behind a yes/no.

Which model goes with the offer is the spec's matter (`docs/superpowers/specs/2026-09-20-installer-design.md`), inside
two limits set here. `qwen3.5:4b` is the default: it is the model the code
records as having answered on this machine (`scribe/llm/ollama.py:221-225`).
`gemma4:12b` is offered only above a memory threshold stated in GiB (gibibytes, 2**30 bytes), labelled an
estimate and set clearly above a nominal 16 GB card, because the same
comment records that the 12B failed to start "while 12.7 GB of the 16 GB
card was in use" (`:221-224`). That card reports 17,179,344,896 bytes, which
is 15.9995 GiB, so a threshold at 16 would flip on its unit; the byte count
was read by the design run's critic and has not been re-measured for this
record.

An Ollama that is present is left alone in every state (Robert,
2026-09-20). Installed and not answering: one sentence and "Check again". It
is never started, because people stop Ollama on purpose to free VRAM (video memory).
Running with no chat-capable model: one sentence, the copyable command
`ollama pull qwen3.5:4b`, and "Check again"; nothing is pulled into it and
no pull is offered. Ready: one line saying it was left alone.

A marker records an install MyScribe itself made. It is written only after
the installer has finished successfully; the installer is never offered
again while any Ollama binary exists; and the marker is cleared once Ollama
has been seen ready. A failed or cancelled download therefore leaves no
marker and the state is simply "absent" again. While the marker stands, the
one thing MyScribe still does to that Ollama is finish the pull of the one
model that was shown and agreed to before the install; the bounds are under
Exceptions. MyScribe does not start the Ollama it installed either. If it
does not answer within the wait, the report reads "installed, not answering
yet" and gives the copyable pull command and "Check again", and the marker
rules hold unchanged.

The pin lives under `scribe/`, because the payload ships `scribe/` and not
`packaging/` (`packaging/build_payload.py:38`). It has an owner: a step in
`docs/RELEASING.md` to bump and re-verify it, and a CI check that the pinned
URL still resolves and its digest still matches. What MyScribe installed
belongs to the user and survives an uninstall; the installer's welcome text
(`packaging/windows/myscribe.iss:51`) and the readme say so, and the readme
says what `install.py` leaves in a clone: `.tools/` (TASK-089.23). The same
welcome text names one fixed folder, the per-user default home, as where
everything is kept; once the location can be chosen, it names that folder
as the default only.

### What a skipped question means

No `llm_provider` row means no provider (Robert, 2026-09-20). The AI (artificial intelligence) panel
asks for a choice, and nothing that nobody asked for is queued for any
provider: not the pass finalize queues after a transcription, and not
`sweep_speaker_passes` at app start. Adopting an existing library with no
provider chosen queues nothing.

This is decided here and not only referenced, for three reasons. It is the
meaning of the contract's central promise: "a skip writes nothing" is only
honest if nothing written means nothing sent. Setup itself becomes the
trigger of the widest path, because pointing a fresh install at an existing
library is what starts the sweep over every recording in it. And the default
it reverses is marked "user decision" in the spec; this repository records
such reversals, with their reason, in a decision record, and no other record
covers the provider default. The argument against is real: the rule also
binds machines where setup never runs, and a later change of mind about it
needs a successor that restates the other two rules, which is what ADR-013
cost. Its bullets are grouped under their own heading below so a successor
can lift them whole.

### Confirmation

Nothing below exists yet; each line names the task that owes the evidence.

* TASK-089.09: a `main()`-level test pins the shape of `--plan`; a test plants a
  marker value in every credential source and finds it in no output, stamp
  or log; an all-skipped run in a scratch directory writes no settings row
  and leaves `.env` unchanged.
* TASK-089.04: red first, with the token only in its settings row, the doctor's
  diarization line and `python -m scribe.models --fetch` both see it. The
  row-only rule for typed secrets is not switched on before this is green.
* TASK-089.15: a test reads the child's argv and finds no secret in it; the
  sequence location, tools, sync, plan, sitting, apply, prove, start is
  asserted with a fake uv; Quit during a download leaves no orphaned python
  process.
* TASK-089.14: nothing is downloaded, and nothing is written under any home,
  before the location question has been answered or skipped.
* TASK-089.11: deciding whether a sitting is due starts no child process; an
  old-format stamp opens one sitting and no second; a skipped id does not
  open one.
* TASK-089.05: a test, red first under a dead proxy, shows the Ollama probe and
  the launcher's `/health` probe both reach `127.0.0.1`.
* TASK-089.13: the serve check leaves the real library's files untouched, and
  with a fake model loader that raises, prove loads nothing while `/health`
  answers or a job is running.
* TASK-089.06 and TASK-089.18: with a process starter and a downloader that raise,
  nothing runs or downloads in any state but absent; the executed command
  equals the shown command. On a machine that has Ollama, `ollama list` and the version
  endpoint read the same before and after a whole sitting.
* TASK-089.07: one test per `default_provider` call site (eight on 2026-09-20:
  `scribe/stages/finalize.py:437`; `scribe/web/ai_ui.py:650`, `:837`, `:953`,
  `:1016`, `:1181`, `:1202`; `scribe/web/library.py:994`); one per fallback
  in `scribe/stages/llm_stage.py`, none of which is such a call site: a job
  whose parameters name no provider ends with a sentence and makes no
  request, red first; and one that starts the app on a copy of a library of
  diarized recordings with no provider row and counts zero queued jobs.
* TASK-089.19: on a copy of a library, adopting it with the provider question
  skipped queues nothing at the first start.
* TASK-089.10: a provider that was chosen and cannot answer - no key, Ollama not
  running, the saved model not pulled - shows a card and creates no job row.
* Only a person can answer these, once per operating system: the real Tk
  window, and the Ollama install on a machine without Ollama - Windows
  Sandbox or a virtual machine, and a Mac. Nobody has done either. A run that
  skips them says "not run".
* The Enforcement patterns below were tried on 2026-09-20 against adr-kit
  0.57.0's own glob translator and sample lines; see Enforcement. The full
  `adr-judge --dry-run-enforcement ADR-015` on a scratch copy marked Accepted
  is TASK-089.02's, and has not been run.

## Decision Contract

### Must

The engine and its contract:

* `python -m scribe.setup` is the only code that detects, decides what is
  open, checks, writes and proves. `--plan` goes out and `--apply-stdin` comes
  in, each one JSON document carrying the `contract` number.
* Detect before asking. A plan made for a start lists only the questions
  that are open and were never put; a plan asked for through `--setup` also
  lists the skipped and the answered ones, each with its current value. The
  plan names where a credential was found, never its value.
* Every question can be skipped and says what skipping costs. A skip writes
  nothing for that question.
* Every question names where it can be answered later. A question whose
  answer is a stored setting is asked only when Settings can take the same
  answer; one with no counterpart there waits until it has one, as start at
  login waits for TASK-089.21. A question that is an act - install Ollama, pull
  its model, adopt a library, download the weights - names the command or
  the sitting that does it later.
* A typed secret goes to its settings row only, from the moment every lookup
  reads that row through the one resolver of TASK-089.04, and not before. A
  refused credential is not saved, and its question reopens.
* "Save and start" writes the stamp, failed downloads included, with the
  contract version and the ids answered, skipped and open. Closing the
  sitting writes none.
* Whether a start opens a sitting is read from the stamp and one number
  out of the payload, as data, without starting a child. It opens one when
  there is no stamp; once for a stamp in the old
  `{provider, tier, hf_token, fetched}` format, putting only what that stamp
  never covered; and when the stamp's contract version is older than the
  payload's. It opens none for an id that was skipped, that a failure left
  open, or that became open again later; `--setup`, the Setup button and
  Settings reach those (TASK-089.11).
* A door with neither a window nor a terminal starts no setup child because
  a sitting is due; it says in one line that nothing was asked and how to
  ask.
* `--plan` reports the model catalogue as it is today, labelled as such,
  until the platform-aware catalogue of TASK-089.16 lands; the key question, the
  found table and the Ollama state do not wait for it. That mlx-whisper
  loads its weights from a local folder is unverified: whoever has the Mac
  answers it under TASK-089.16, and until then the macOS line of the catalogue in
  `--plan` is labelled unverified.
* `--prove` exits 0 only when every required line is OK. A skipped or
  untestable line reads "not tested" with where to finish it. While the app
  answers `/health`, or a job is running in this library, prove queues the
  existing `doctor` job or reports "not tested" with the reason. Otherwise
  it measures in its own process, exactly as `python -m scribe.doctor` does
  from a terminal (`scribe/doctor.py:381`). That command is the precedent,
  and it is why this is not read as a breach of ADR-001: with no app and no
  running job, nothing else holds the card (TASK-089.13).
* The launcher keeps the setup child's handle, and Quit stops that child:
  that one process, not its tree (TASK-089.15). The tree kill the launcher uses
  for the app (`taskkill /T /F`, launcher `:376-382`) is not used here, so
  that none passes over an Ollama the installer has just started; what a
  single-process stop can leave behind is argued in `docs/superpowers/specs/2026-09-20-installer-design.md`. While a
  third-party installer runs, Quit is refused with a sentence saying why.
  Nothing on screen says Quit is clean until the experiment of TASK-089.18 has
  been run.
* `install.py` needs nothing the environment would provide and runs on the
  Python a clone user already has; it uses only the pinned, sha256-verified
  uv of `packaging/tools.json`.
* Both doors check free space before the first sync (TASK-089.14, TASK-089.17).

Installing somebody else's software:

* Every loopback probe - Ollama's and `/health` - ignores a configured
  proxy. A proxy that is found is shown in the found table and never asked
  about.
* Offer only when absent, show before asking, run exactly what was shown,
  verify the pinned sha256 first. A missing artifact or a mismatch ends in
  the vendor's download page and "Check again".
* Write the marker only after the installer has finished successfully; never
  offer the installer while any Ollama binary exists; clear the marker once
  Ollama has been seen ready.
* Keep the pin under `scribe/`, with its release step and its CI check.
* Tell the user, in the installer and the readme, what stays behind after an
  uninstall and what `install.py` leaves in a clone (`.tools/`). The
  installer's text stops naming one fixed folder once the location can be
  chosen (TASK-089.23).

What a skipped question means:

* With no `llm_provider` row there is no provider: the panel asks for a
  choice and no automatic pass is queued, `sweep_speaker_passes` included.
  Adopting an existing library with no provider chosen queues nothing.
* The spec and the changelog each carry a line saying the default changed.

### Must Not

* Put a secret on a command line, in the plan, in the stamp, in a log or on
  the screen.
* Copy a credential from where it was found into another store.
* Let a front-end decide, check or write an answer, or interpret any
  condition other than `shown_if`.
* Install over, upgrade, start, stop, pull into or reconfigure an Ollama
  that is present, whatever its state - except the one pull under
  Exceptions.
* Pipe a remote script into a shell, run a script as root on the user's
  behalf, or fall back to an unpinned download.
* Load a model in the setup child while the app answers `/health` or a job
  is running in this library (ADR-001).
* Start a second app on the live data directory to prove that it serves: its
  lifespan migrates the database and runs the sweep
  (`scribe/app.py:247-261`). The proof uses a scratch data directory, or
  "`/health` already answers".
* Write the `default_diarize` row from setup, in either direction: not from
  a plan question, not from a front-end and not from a flag.
* Import anything in `install.py` that the environment would have to
  provide. The standard library is allowed; this repository's own stdlib-only
  packaging modules are allowed only if the open question on sharing code is
  answered that way.

### Exceptions

* **Where everything goes.** A release asks one question before the sync:
  the folder for the environment, the data and the weights, with the free
  space per volume, what this install downloads and the disk it needs
  (TASK-089.14). The launcher asks it and persists it itself, in a one-line
  pointer file next to the default home that `home_dir()` reads. The engine
  cannot ask it, because the engine lives in the environment whose place is
  being chosen. It is stdlib-only, so inside ADR-011; the folder is never
  the install directory (ADR-011); skipping keeps today's location. It is
  the only answer a front-end decides and writes. It is also outside the
  rule about a counterpart in Settings, and has none: Settings lives under
  the home it chooses. It is changed later by editing the pointer file, which
  `--setup` shows with the path in force; nothing is moved for anybody. How
  a release points at an adopted library is not settled: `docs/superpowers/specs/2026-09-20-installer-design.md`
  proposes that the engine's result names the data directory and the
  launcher writes it into the same file as a second fact, because the
  launcher forces `SCRIBE_DATA_DIR` (`:203`). TASK-089.19 decides that before it
  is built, and this record is amended if it holds (Open Questions).
* **The pull into MyScribe's own install.** The Must Not on a present Ollama
  has one carve-out: the pull of the one model that was shown and agreed to
  before the install, into the Ollama MyScribe installed, while its marker
  stands - that is, until that Ollama has first been seen ready. In the
  sitting of the install it follows the install. After that sitting it is
  offered again only in a sitting somebody opened with `--setup`, never by a
  start, and it is the same one model. Nothing else is done to that Ollama,
  and it is never started. This is the record's reading of the marker rule,
  not something Robert decided; the narrower reading is under Open
  Questions.
* **`--hf-token`, for now.** This one contradicts the first Must Not, and
  the record says so: the flag puts a secret on a command line. It is
  tolerated only because it is documented for people's own use in the README (the repository's front page,
  `README.md:224`), and whether it survives is Robert's call (Open
  Questions). Until he answers, it keeps working and prints one line saying
  it is deprecated and why. No front-end uses it, and no other
  secret-bearing flag is added.
* **The other documented flags.** `--status`, `--provider`, `--tier` and
  `--fetch-models` (`README.md:224`, `scribe/setup.py:160-167`) keep working
  as the scripted door; `docs/superpowers/specs/2026-09-20-installer-design.md` names `--fetch-models` as where the
  weights question is answered later. `--diarize/--no-diarize`, documented
  on the same line, is the one that goes, with its README line: it writes
  `default_diarize` (`scribe/setup.py:125-136`), and the Must Not leaves
  setup no writer of that row. Nothing in the product loses by it: no plan
  question and no front-end sends it, and the launcher's answers carry no
  such key today (launcher `:579-584`). Whether it stays after all, as an
  explicit-only flag, is Robert's call (Open Questions).
* **macOS.** The verified `.dmg` is opened and the person drags the app
  across; MyScribe waits with "Check again". Unverified: nobody in the design
  run had a Mac. The same holds for the macOS line of the model catalogue:
  that mlx-whisper loads from a local folder is unverified (TASK-089.16).

### Verification

* The tests named under Confirmation, by task key, once they exist.
* `grep -n -- "--hf-token" packaging/launcher/myscribe_launcher.py` returns
  line 476 on 2026-09-20 and nothing after TASK-089.15.
* `grep -rn --include=*.py "DEFAULT_PROVIDER" scribe/` returns five lines on
  2026-09-20 (`scribe/llm/tasks.py:456`, `scribe/llm/__init__.py:175`,
  `scribe/stages/llm_stage.py:94`, `:271`, `:345`); after TASK-089.07 the same
  command returns no line in `scribe/stages/llm_stage.py` and none in
  `scribe/llm/__init__.py`. Whether the constant itself survives in
  `tasks.py` is TASK-089.07's to say.
* `<repo>/.venv/Scripts/python probe_proxy_loopback.py` (References) fails
  twice and answers twice on 2026-09-20; the test of TASK-089.05 is what holds
  after the change.
* `adr-judge --dry-run-enforcement ADR-015` on a scratch copy marked
  Accepted, with a probe diff that adds `import scribe` to `install.py` and
  `command += ["--llm-key", key]` to the launcher: both flagged.

## Consequences

### Positive

* Adding a question is adding data in one place; the window, the console, a
  clone and CI pick it up.
* Secrets leave the command line. Typed secrets have one store; found ones
  stay where they were found.
* Clone users, headless starts and CI walk the path release users walk, and
  empty answers make an unattended install.
* An install where every question was skipped sends nothing anywhere.
* An Ollama somebody already has is never touched, and that is checkable on
  any machine that has one.
* The `/welcome` page stays possible: it would be one more front-end over the
  same plan and apply.

### Negative

* Two waits with one sitting between them - the sync, then the questions,
  then the downloads - plus the one question before the sync. The window says
  so up front.
* The launcher grows. It draws a plan, feeds stdin, shows progress and error
  states. It stays stdlib-only; it does not stay as small.
* The Tk dialog is still code only a person at a screen can confirm, once per
  operating system.
* A Windows release gets no console door: the binary is frozen `--windowed`
  (`packaging/build_release.py:81`), so Tk is the only one there. A Windows
  clone has it, through `install.py` in a terminal.
* MyScribe now owns a pin on somebody else's release cadence. A stale pin
  turns the offer into a link; the release step and the CI check are what
  that costs.
* On Linux and macOS installing Ollama is not one click.
* Somebody who relied on the fall-through to OpenRouter without ever choosing
  has to choose once.
* Typed secrets live in the library's database, so a copy of the library
  carries them (ADR-013: the database plus the media directory is the whole
  backup). That was already true for a key typed in Settings.
* Three rules in one record: revisiting one needs a successor that restates
  all three.
* `packaging/fetch_models.py:58` still takes `--token` on its command line.
  It is a build-machine tool, not a front-end, and this record does not reach
  it.

## Pros and Cons of the Options

### One app-side engine behind a JSON contract (chosen)

* Good, because a question, its default and its cost of skipping exist once.
* Good, because detection is the app's own lookup order, not a stdlib copy
  of it in the launcher.
* Good, because the third-party installer runs from a plain child process,
  outside the single job lane (`scribe/jobs.py:142-150`), so it never blocks a
  transcription.
* Bad, because it asks after the sync: two waits, not one.
* Bad, because the launcher grows and the Tk pixels still need a person.
* Bad, because it does not escape the kill-tree question either: the setup
  child is a child of the launcher, Quit does not reach it today (Context),
  and what a tree kill does to an Ollama that the installer just started is
  unverified (Open Questions). The Must therefore stops the setup child
  alone, and the app's own tree kill at a later Quit is still to be tested.

### Pages in the Inno Setup wizard

* Good, because that is the moment a Windows user already calls installing,
  and it comes before anything downloads.
* Bad, because only Windows has a wizard: today it has one task, the desktop
  icon, and no code section (`packaging/windows/myscribe.iss:31-32`). The
  `.dmg`, the AppImage and a clone would need the questions a second time.
* Bad, because the wizard runs before the environment exists, so it cannot
  use the app's lookup order or check a token, and its answers would have to
  reach the app through a file holding secrets or through a command line.
* Bad, because the suite and the release smoke test drive Python; a Pascal
  page would be the one piece of first-run logic neither reaches.

### A /welcome page in the web UI (runner-up)

* Good, because the launcher shrinks: the Tk form goes, and the launcher
  never touches an answer or a secret, not even over stdin.
* Good, because every question is testable with the test client on all three
  CI runners, with no person at a screen.
* Good, because validation is live - a progress bar per download, a check
  beside a found token - and a re-run shows the truth about the machine by
  construction.
* Good, because a headless start, an AppImage without Tk and Windows all get
  the identical page. Settings already takes the token and the keys over a
  loopback form, so nothing new crosses a boundary.
* Bad, because the app starts before anything is answered. The rule about a
  skipped question takes most of the sting out of that; it does not remove
  the redirect: bouncing `/` to a wizard while `setup.json` is missing would
  bounce an existing library too, and 44 call sites in 10 test files get `/`
  (counted 2026-09-20).
* Bad, because the Ollama installer would run as a job or from the web
  process. As a job it holds the only lane for the length of a download of
  more than a gigabyte (size as read in the design run). Either way it sits
  inside the tree that Quit force-kills on Windows (launcher `:376-382`), and
  an Ollama killed half-written then reads as present, which the leave-alone
  rule keeps that way.
* Bad, because it gives the web process an endpoint that downloads and runs
  an executable, which is a larger thing to defend than a command-line child.
* Neutral: it tied on the judges' score and scored highest, 7.5, with the
  judges who read as maintainers. If this record is overruled in its favour,
  the engine and the rule that front-ends never apply answers carry over
  unchanged; the install job does not, until the lane, the kill tree and the
  marker are solved for it.

### Ask everything before the sync

* Good, because it is one attended minute and then everything runs
  unattended; the person can walk away once.
* Bad, because the environment does not exist yet, so looking before asking
  would mean the lookup order the resolver of TASK-089.04 will have - settings
  row, environment, `.env`, for provider keys the registry
  (`scribe/llm/base.py:260-266`), and the Hugging Face login file, which
  nothing reads today - written a second time in stdlib code, in the program
  whose own docstring says it "should not learn how" (launcher `:469-471`).
* Bad, because nothing can be written yet either: answers, secrets included,
  would have to be held across a 3 GB sync, in memory or in a file.
* One question can only be asked before the sync - where everything goes -
  and the chosen option takes exactly that one.

### Delegate "only when missing" to Ollama's own scripts

* Good, because the vendor maintains them: no pin to own, and they follow
  Ollama's packaging when it changes.
* Bad, because they do not implement the rule. Read on 2026-09-20,
  `install.ps1` installs unconditionally, and `install.sh` stops and removes
  an existing app on macOS and replaces the library directory on Linux (line
  numbers in Context). "Niets doen" has to be MyScribe's own gate.
* Bad, because what runs is whatever the server serves that day: it cannot be
  shown in advance, pinned or checked against a sha256.
* Bad, because on Linux it needs root and may install drivers, and the frozen
  launcher has no terminal to ask for a password in (launcher `:275-280`).

### install.py shares the launcher's code, or only its sequence

Under either, the asker is shared, because the engine asks in a clone. What
is left to share is the bootstrap: fetch the tools, run uv, compare a stamp.
Nobody has counted those lines.

* Sharing code, good: one bootstrap function, one test that asserts the order
  for both layouts, and the ordering bug of TASK-089.01 is fixed once.
* Sharing code, bad: the layout's fields become optional and the environment
  builders skip missing ones, branches that ship in the frozen binary and
  that only a caller which is never frozen can reach.
* Sharing code, bad: `install.py` would import the launcher module under
  whatever Python the clone user has. The launcher parses as Python 3.9
  today (checked 2026-09-20); this turns that into a runtime promise for a
  file that so far only runs under the interpreter it was frozen with.
* Sharing the sequence, good: the launcher is untouched by clone concerns,
  which is what the packaging reader of the design run advised, and
  `install.py` stays one file a person can read top to bottom.
* Sharing the sequence, bad: two implementations of tools, sync, setup,
  prove, start. A test that drives both against a fake uv holds the order and
  not the details, and drift is how a step goes missing in one door.

This record leans to sharing the sequence, because ADR-011's launcher is
small on purpose and the duplicated part is the small part. It is left open
below because nobody has measured it.

## Open Questions

- [x] Where should a new Ollama keep its models? It is not asked. Setting `OLLAMA_MODELS` for an install MyScribe itself makes touches no existing Ollama, so the leave-alone rule is not engaged, but it is still configuring somebody else's software. Without it, a default volume that is too small ends the offer with both numbers and no way out. Robert decides whether it is ever asked. — **Answered 2026-09-20 by User: Robert van den Breemen:** It is asked only when the default volume is too small, and never otherwise. With room, Ollama keeps its own default and there is no question. Without it, the installer shows both numbers and asks whether the models should live with MyScribe instead, with the default on Yes because the other answer ends the offer. On Windows MyScribe then sets `OLLAMA_MODELS` as a variable of the user's account - the same place the app already reads through `windows_env` (`scribe/llm/base.py:194-222`) - before the installer starts, so that the daemon it starts gets it; on macOS and Linux it stays a sentence with the exact command until somebody has measured the mechanism there, because a systemd unit wants root and a launch agent is a second mechanism nobody has run. It engages no existing Ollama, since it happens only for an install MyScribe itself makes in that sitting. It is what Robert's own machine shows to be needed: his models were moved to another drive by hand, and somebody who put MyScribe on a second drive because the system drive is small would otherwise run aground here. Not verified: whether the daemon started by the silent installer inherits the variable. That belongs to the sandbox run of TASK-089.18, and until it has been run nothing in the product says the models went where they were asked to go without checking.
- [x] Does `install.py` share the launcher's code or only its sequence? If code: one bootstrap, and the frozen launcher carries branches only a clone reaches and must run under a clone user's Python. If sequence: the launcher stays as it is and the bootstrap exists twice. This record leans to sequence; the count of duplicated lines that would settle it has not been made. — **Answered 2026-09-20 by User: Robert van den Breemen:** Only its sequence, with a contract test. The count was made on 2026-09-20: `packaging/launcher/myscribe_launcher.py` is 758 lines, and about 100 of them are plumbing both doors need - the lock digest, the stamp, the sync decision, running a child, the setup call; the home, the environment and the tools differ per door. The part where drift would hurt is not in the launcher at all: the launcher copies its tools out of the payload, and fetching them and checking their sha256 is done in `packaging/build_payload.py`, which is what `install.py` shares that part with. So the launcher stays as it was built and shipped in v0.5.0 and v0.5.1, with the 26 tests of `tests/test_launcher.py` pinning it, and gains no branch only a clone reaches. Against drift in the duplicated plumbing, one contract test gives both doors the same lock and stamp and demands the same sync decision. A shared module under `packaging/launcher/` was weighed and is feasible - PyInstaller follows imports (`packaging/build_release.py:89`) and the import guard already covers that folder - and was set aside because it rebuilds shipped code whose frozen path only the release smoke and a person at the screen exercise.
- [x] How does `install.py` know that the MyScribe answering `/health` runs from this checkout, so that it refuses to sync under it? `/health` returns only `ok` and `version` (`scribe/app.py:296-298`) and the launcher's single-instance rule reads only `ok` (launcher `:306-312`). The candidate, set out in `docs/superpowers/specs/2026-09-20-installer-design.md`, is two more fields naming the source tree and the data directory, with an answer that lacks them treated as doubt. It moves a body that `tests/test_app.py:45-48` pins exactly, and it cannot see an app on a port nobody named. If Robert judges that unsound, the refusal becomes a warning. A lock or pid file is what ADR-001 and ADR-013 avoid. The same blind spot bears on the prove rule: `--port 4299` is this repository's documented way to run a second app beside the one on 4242 (the project's instruction file, under Commands), and such an app does not answer the `/health` that prove asks. TASK-089.13 adds a check for a running job row in the same library, which needs no port; what that still misses - an app on another port, serving another library, on the same card - is said in the report and is open here. — **Answered 2026-09-20 by User: Robert van den Breemen:** By two more fields in what `/health` answers: the source tree the app runs from and the data directory it serves. `install.py` asks the port it was given - 4242, or `--port` - and refuses to sync when the source tree is this checkout, goes on when it is another, goes on when nothing answers, and treats an answer without the two fields (an older MyScribe) as doubt, which refuses. The same two fields answer the proof's question, whether the app that answers serves this library. It gives nothing away: the host check covers the whole app (`scribe/guard.py:110`), so a web page cannot read the answer, and a local process that can read it can read the file system as well. It moves `scribe/app.py:296-298` and the test that pins the body exactly (`tests/test_app.py:45-48`); the launcher reads only `ok` (launcher `:306-312`) and notices nothing. The blind spot is kept and named: an app on a port nobody mentioned is not seen, and this repository's own `--port 4299` is such a case. A row in the database that the running app writes was weighed - it would see an app on any port and fits ADR-013 - and set aside for now because it needs a stale row handled after a crash and opens the live database from an installer, for a case that `--port` already covers when somebody knows about it. A warning in place of a refusal was set aside because what a sync does to locked files on Windows has been measured by nobody.
- [ ] On a Windows machine without Ollama (Windows Sandbox or a virtual machine): does the silent installer start the daemon, and would a tree kill (`taskkill /T /F`, the way the launcher stops the app) take that daemon down - on the setup child, which the Must for that reason stops alone and not by its tree, and on the app at a later Quit? Ollama's own script waits for the installer process only because "using -Wait would wait for Ollama to exit too" (`install.ps1:294-295`, read 2026-09-20), which suggests the daemon is the installer's child. Process lineage under `taskkill /T` after the Ollama installer has exited is verified by nobody. Until a run answers it, nobody promises that Quit is clean; whoever has the sandbox answers it, under TASK-089.18.
- [ ] The proxy behaviour was measured for the two library defaults on one Windows machine with a proxy variable (Context); that MyScribe's own two probes fail the same way is still an inference. Not measured at all: a Windows system proxy set in the registry and not in a variable, macOS, and Linux. TASK-089.05 runs the product's probes first and pins the bypass with a test that is red first under a dead proxy; whoever has the Mac and the Linux machine answers the rest, and until then the presence test's "any doubt reads as present" is what protects an Ollama behind a proxy.
- [x] Is there a free way to check an OpenRouter key? None was verified. Without one, "a refused credential is not saved" holds for OpenRouter only after the paid one-word probe, which runs on consent. — **Answered 2026-09-20 by Claude (agent, session 2026-09-20):** Yes. An authenticated request to `https://openrouter.ai/api/v1/key` costs nothing and is answered 401 for a key that is missing, invalid or disabled; a valid key gets its limit, its remaining credit and its usage back. Two sources, both 2026-09-20: OpenRouter's documentation (openrouter.ai/docs/api-reference/limits), read through a summarising fetch and not word for word, and a probe from Robert's machine that sent no key - `/api/v1/key` answered 401 while a nonsense path answered 404, so the address exists and asks for credentials. Not run: a call with a real key, valid or revoked, because no key of Robert's was used to find this out. So a refused OpenRouter key can be turned away without the paid one-word probe; TASK-089.09 builds the check, and its first run with a real key is the proof still owed.
- [x] Should the marker be bound to what was installed - version and path - so that an Ollama the user installs after removing MyScribe's is never mistaken for MyScribe's own unfinished work? — **Answered 2026-09-20 by User: Robert van den Breemen:** Yes: to the version and to the path, and any doubt drops it. The marker records what MyScribe installed, where and when, and it counts only while that same version still stands at that same path; anything else makes it lapse, after which the Ollama that is there is left alone and the report gives the copyable pull command. No binding is watertight, and this one is chosen for the side it fails to. Ollama updates itself, so the version changes for an honest reason and the marker then lapses: the worst outcome is that an interrupted download is no longer offered and somebody types one command. The path alone would survive that update but cannot tell MyScribe's Ollama from one the user installed afterwards, because the default path is the same for everybody - which is the very case the leave-alone rule exists for. What version and path together do not catch is somebody removing MyScribe's Ollama and installing the same version at the same path; they then get one question whose default is No, and nothing happens without their yes. A shelf life on top of this was weighed and set aside as one more rule to explain for a gap that small.
- [x] Does `--hf-token` survive at all? A secret never rides on a command line (Must Not), and this flag does. Keeping it, deprecated, honours `README.md:224`, which documents it for people's own scripts. Removing it in TASK-089.09 and correcting the README honours the rule, and the second Enforcement pattern then loses its allowance for the flag. Launcher and app ship together, so nothing in the product needs a window. Robert decides. — **Answered 2026-09-20 by User: Robert van den Breemen:** No. It goes in TASK-089.09, and it goes with a sentence and not with an argparse error: the flag is still recognised and is refused, saying that a token on a command line can be read by other processes and ends up in shell history, and that the token belongs in `HF_TOKEN` - in the environment or in `.env`, where setup now finds it by itself - or in the document piped to `--apply-stdin`; exit 2. It costs nothing that works today: detection already looks in the environment, in `.env` and in the registry, the product stops passing the flag once the launcher pipes its answers (launcher `:475-476` does so now, and `tests/test_launcher.py:404` pins it and moves with it), the flag has existed since 2026-09-18, and the line in `README.md:224` that documents it was written on 2026-09-20 by the same session that raised this question, so it is no evidence that anybody relies on it. What it buys: the Must Not has no exception, and the second Enforcement pattern loses its allowance for the flag. A deprecation window for one release was weighed and set aside, because it protects a script nobody is known to have written while leaving the rule with a hole in it.
- [x] Does `--diarize/--no-diarize` go from `scribe.setup`? This record and `docs/superpowers/specs/2026-09-20-installer-design.md` say yes: the flag is the one path by which setup writes `default_diarize` today (`scribe/setup.py:125-136`, `:164-165`), no question asks it and no front-end sets it, and "setup does not write the row" can only be checked when no path does. The other way keeps it as an explicit-only flag outside that rule, because somebody who types `--no-diarize` is choosing and not guarding, and `README.md:224` documents it; the Must Not then names it as the one writer setup keeps. Robert decides. — **Answered 2026-09-20 by User: Robert van den Breemen:** No, it stays, as an explicit choice and nothing else. What had to go was setup switching the speakers default off BY ITSELF when no token was found, on a row that every upload rewrites; that guard is gone either way. Somebody who types `--no-diarize` is choosing, not guarding, and the flag has shipped in v0.5.0 and v0.5.1 (`scribe/setup.py:164-165`), so taking it away would break a command line people may have scripted - an unattended install on a machine that will never separate speakers is the case that wants it. Its removal came from this record reading 'setup does not write the row at all' literally, not from anything Robert asked for. So the Must Not names the flag as the one writer setup keeps, the test for 'setup does not write the row' covers every path except that one, and the text of this record and of the spec that says the flag is removed is corrected. Unchanged and still to be fixed under TASK-089.09: a tier answer alone writes the diarize row today, because `save_defaults` writes both together (`scribe/setup.py:125-136`).
- [x] Does the pull into MyScribe's own install outlive the sitting of the install? This record and `docs/superpowers/specs/2026-09-20-installer-design.md` say yes: while the marker stands, and only in a sitting opened with `--setup` (Exceptions). Robert's rule is that an Ollama that is there is left alone, and after that sitting it is there. The narrower rule: the pull happens in the install's sitting or not at all, and afterwards the report gives the copyable `ollama pull` command. Robert decides. — **Answered 2026-09-20 by User: Robert van den Breemen:** Yes. While the marker stands, a sitting that somebody opens with `--setup` offers the unfinished pull again, and the offer is a question with its default on No - never a pull that starts by itself. Robert chose this over the narrower rule the agent recommended (the pull in the install's own sitting or not at all, and no marker): finishing an interrupted download with the same button matters more to him than having no state to keep, and a copyable command in a terminal is the separate thing afterwards he asked to avoid. What it costs is accepted with it: the marker is state that can go stale, 'an Ollama that is there is left alone' carries one exception that has to be explained and tested, and the next question - what the marker is bound to - has to be answered for that exception to be safe. The marker rules stand as written: it is written only after the installer has finished successfully, the installer itself is never offered while any Ollama binary exists, and the marker is cleared once Ollama has been seen ready.
- [x] How does a release point at an adopted library? The launcher forces `SCRIBE_DATA_DIR` (launcher `:203`), so `.env` cannot carry it. `docs/superpowers/specs/2026-09-20-installer-design.md` proposes a second fact in the pointer file, decided by the engine and written by the launcher, which would make that file more than one line and the launcher the writer of a value it did not ask for. TASK-089.19 settles it before building, and this record's Exception is amended to match. — **Answered 2026-09-20 by Claude (agent, session 2026-09-20):** That is TASK-089.19's to settle with a test, and it is below the level of this record. What this record fixes is the principle: where things live is kept in a small file that the launcher can read with the standard library before anything else exists, the launcher writes it only on the engine's instruction and never decides its content, and the app is never asked to move a library. Whether that file holds one fact - the home - or a second one for a library adopted in place follows from that principle either way, because the launcher stays a reader and a writer of facts it was handed. So the wording 'one-line' in the Exception is loosened to 'a small pointer file' when this record is split, and the second fact remains the spec's proposal until TASK-089.19 has built and measured it. Proposed by the agent on 2026-09-20; Robert sees it in the acceptance packet and can overturn it there.
- [x] macOS is unverified throughout: the `.dmg` flow, the install locations the presence test looks in, the console asker under a Mac terminal, and whether mlx-whisper loads its weights from a local folder, which the platform-aware catalogue of TASK-089.16 rests on. Who has the Mac answers it, and until then every task that touches macOS reports it as not run. — **Answered 2026-09-20 by User: Robert van den Breemen:** The Mac belongs to somebody else and can be asked for now and then, so macOS questions are bundled and not asked one at a time. Each milestone collects its macOS criteria into one list - per point the command to run and the output to expect - so that the machine's owner can run all of it in one sitting. Until that sitting every macOS claim stands in its task as 'not run', no criterion that needs a real Mac is checked on an assumption, and the release notes say what continuous integration proves on its macOS runner - the build, the first sync, the health answer, a page, the stop - and nothing more. The decision in this record does not wait on it: nothing here is different on a Mac, only unproven there. It also settles a contradiction in the repository that the grill turned up: the README presents an Apple M2 as verified, a task of 2026-09-19 reports a session on a Mac, and an older task calls that machine somebody else's; the README sentence gets its date and says whose machine it was.
- [x] The pinned artifact addresses, sizes and digests and the Windows signer's name were read from Ollama's release data by a reader in the design run, not by this record's author. TASK-089.18 re-verifies them before anything is pinned. — **Answered 2026-09-20 by Claude (agent, session 2026-09-20):** Verified in part on 2026-09-20 by the orchestrator against the release data for the tag v0.34.2 of ollama/ollama, read with the GitHub command-line client: the release exists, is the latest, is not a prerelease, and was published on 2026-09-15. `OllamaSetup.exe` is 1,569,993,232 bytes with sha256 8c9eb7ba71f3c6a62df4c7d204cc4739d90c335e1cbeb07c99fd471544066a8b, and `Ollama.dmg` is 197,873,582 bytes with sha256 ca3c5c1587fe05eebedacd33ec9448e7999fcce9c19dafcb4ca7eca2f89d5d7b; both sizes match what this record quotes. Not verified: the name of the Windows signer, which needs the installer downloaded, and the Linux artifact. Ollama releases often, so TASK-089.18 verifies again at the moment it pins.

## Related Decisions

* ADR-011 (the launcher): respected and extended, not edited. The launcher
  still imports nothing from the app, reads no `.env` and writes no setting
  row; it learns to read one JSON document, write one, and keep one pointer
  file. ADR-011's Enforcement guards `packaging/launcher/**` and cannot reach
  `install.py`, which runs before the environment exists for the same reason.
  An Accepted record is never rewritten, so the rule for `install.py` is in
  this record's own Enforcement block: the judge reads the block of every
  Accepted record, so both apply side by side once this one is accepted, and
  ADR-011's text and status do not move. If Robert prefers one record to hold
  both globs, the route is a successor to ADR-011 through `adr supersede`,
  which is human-gated, as ADR-013 was for ADR-009. Either way the pattern is
  a deny-list of seven module names - `import requests` passes it - and the
  proof that `install.py` is stdlib-only is the AST (abstract syntax tree) allow-list test of TASK-089.17.
* ADR-001 (one runner at a time): `--prove` loads no model beside a running
  app or a running job; it queues the `doctor` job the way Settings does.
  With neither, it measures in its own process, as the doctor command does
  from a terminal.
* ADR-012 (one lockfile): both doors install with `uv sync --frozen` from the
  committed lock using the pinned uv, and no question offers a choice between
  the card and the CPU: one lock, a per-platform torch source.
* ADR-013 (SQLite is the only coordination): it forbids a lock or pid file
  for job state, and ADR-001 counts "no lock files" among what it wanted. This
  record introduces none for "is MyScribe running from this checkout" either,
  and the settings rows setup writes are the ones Settings writes.

## References

* `docs/superpowers/specs/2026-09-20-installer-design.md` - the design spec written with this record: the question list, what
  is deliberately not asked, the model offer, the proof report.
* `docs/superpowers/specs/2026-09-20-installer-evidence/` - `probe_proxy_loopback.py` and
  `probe_proxy_loopback.output-2026-09-20.txt`: the command, its output and
  what it does and does not prove about a proxy variable and the loopback.
  `ollama-install-scripts.txt` there records the two Ollama scripts as served
  that day - their addresses, sizes and sha256 digests, and the lines read in
  them - without copying the scripts, which are Ollama's.
* `packaging/launcher/myscribe_launcher.py:59-64`, `:95-106`, `:119-126`,
  `:200-207`, `:275-286`, `:306-312`, `:376-382`, `:430-439`, `:452-454`,
  `:462-485`, `:515-594`, `:617-621`, `:651-668`.
* `scribe/setup.py:9`, `:21-23`, `:60-61`, `:114-116`, `:125-136`, `:143-154`,
  `:158-167`.
* `scribe/doctor.py:381`, `:482`, `scribe/models.py:246`, `:307-312`,
  `scribe/web/transcribe_dialog.py:63-69`, `scribe/llm/ollama.py:221-225`.
* `scribe/llm/tasks.py:456-459`, `scribe/llm/__init__.py:164-175`,
  `scribe/stages/llm_stage.py:94`, `:271`, `:345`,
  `scribe/stages/finalize.py:155-220`, `:437`, `scribe/app.py:247-261`,
  `:296-298`.
* `scribe/llm/base.py:252-258`, `scribe/stages/diarize.py:265-271`,
  `scribe/web/settings.py:8-14`, `:154-163`, `:517-519`, `:940-942`,
  `scribe/jobs.py:142-150`, `scribe/paths.py:40`, `scribe/llm/ollama.py:170`,
  `pyproject.toml:63-65`.
* `packaging/build_payload.py:38`, `packaging/build_release.py:81`,
  `packaging/windows/myscribe.iss:31-32`, `:51`, `packaging/tools.json`.
* `docs/superpowers/specs/2026-09-01-myscribe-design.md:219`,
  `CHANGELOG.md:107-110`, `README.md:224`, backlog TASK-040.06.
* https://ollama.com/install.sh and https://ollama.com/install.ps1, as served
  on 2026-09-20: 455 and 495 lines, sha256
  `25f64b810b947145095956533e1bdf56eacea2673c55a7e586be4515fc882c9f` and
  `310071580b654bb55de81c65152052771e1143018d7731c8d59b5f49f79c0503`. They are
  unpinned and will move, so the line numbers above are that day's.
* https://ollama.com/download - where a failed pin sends the user; not
  fetched for this record.

## Enforcement

Three rules, each a tripwire on the obvious form and not a proof. The judge
tests added lines one at a time, so a rule sees nothing that spans two lines
and does not flag launcher line 476 until somebody touches it; TASK-089.15 deletes
it. The first rule misses a secret passed positionally or a flag name built
at run time; the argv test of TASK-089.15 is the proof. The second keeps
`--hf-token` editable for its deprecation line and would still allow it back
after it is removed; if Robert removes the flag, the allowance goes with it.
The third is ADR-011's deny-list applied to `install.py`. Nothing
declarative is offered for the provider default: it is
behaviour, shown by a test per call site, and a pattern would have to guess
how TASK-089.07 spells it. Tried on 2026-09-20 with adr-kit 0.57.0: the brace glob
matches `packaging/launcher/myscribe_launcher.py` and a root `install.py` and
not `scripts/install.py`; the first pattern flags today's line 476 and
`'--llm-key'`, and passes `"--apply-stdin"`, `"--provider"` and
`"--fetch-models"`; the second passes the `"--hf-token"` line and flags
`"--llm-key"` and a bare `"--openrouter-token",` on a line of its own; the
third flags `import scribe` and `from scribe import setup`, indented or not,
and passes `import requests`. The block parses with the kit's own reader and
its structural check reports nothing.

```json
{
  "forbid_pattern": [
    {"pattern": "[\"']--[a-z0-9-]*(?:token|key|secret|password)[a-z0-9-]*[\"']", "path_glob": "{packaging/launcher/**,install.py}", "message": "A secret never travels on a command line: send the answers to scribe.setup on stdin (ADR-015)."},
    {"pattern": "[\"']--(?!hf-token[\"'])[a-z0-9-]*(?:token|key|secret|password)[a-z0-9-]*[\"']", "path_glob": "scribe/setup.py", "message": "scribe.setup takes secrets on stdin; no secret-bearing flag besides the deprecated --hf-token (ADR-015)."}
  ],
  "forbid_import": [
    {"pattern": "^\\s*(?:import|from)\\s+(?:scribe|torch|faster_whisper|ctranslate2|pyannote|fastapi|uvicorn)\\b", "path_glob": "install.py", "message": "install.py runs before the environment exists: stdlib only (ADR-015, as ADR-011 for the launcher)."}
  ],
  "require_pattern": []
}
```
