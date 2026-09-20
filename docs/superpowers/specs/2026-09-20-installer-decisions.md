# The installer design: what was asked, what was decided, and what the review found

This is the key to `2026-09-20-installer-design.md` and to the backlog tasks
under TASK-089. Both refer to decisions and review findings by short keys -
R1, W5, M2, U7 - and this file says what each key means. It is a record, not
a design: the design is in the spec, the work is in the backlog, and the
durable rules are in ADR-015.

How it came about, all on 2026-09-20. Ten readers mapped the current
onboarding and returned 112 evidence-backed gaps. Four independent designs
were each judged through three lenses (the person installing, the engineer
maintaining it, everything that goes wrong); three of the four tied at 7.0. A
synthesis chose a spine. A completeness critic then re-opened the code and
found seven things the synthesis had wrong (W), eleven it had left out (M),
and nine questions nobody had proposed (U). Robert decided four forks (R)
interactively the same day. A second run drafted the spec, the tasks and the
ADR, put each in front of two verifiers - one holding it against this list,
one against the repository - and fixed 87 defects; a cross-check then settled
20 disagreements between the three, 19 of them outright and one as an open
question for Robert.

## What Robert asked for

In his words across the session, translated:

1. One installation script that works on all platforms.
2. It asks for API keys when needed, and skipping must be an option for every
   question.
3. It asks for the Hugging Face token when one is not already found.
4. It downloads the models.
5. Rather a few extra questions during installation than separate things to
   do afterwards ("liever een paar extra vragen tijdens installatie, dan weer
   los extra dingen doen").
6. A standalone Python installer for a plain clone, alongside the
   platform-specific installers and not instead of them.
7. Detect before asking: test whether keys are already in `.env`, in the
   operating system's environment, or in a config file.
8. Offer to install and configure Ollama, and offer a model such as gemma4.
9. But only when Ollama is absent: "Als Ollama er al is, dan niets doen."

## R - Robert's four decisions

* **R1 - No provider row means no provider.** Today a missing
  `llm_provider` row falls through to OpenRouter (`scribe/llm/tasks.py:456`,
  `scribe/llm/__init__.py:164-175`, and the fallbacks at
  `scribe/stages/llm_stage.py:94`, `:271`, `:345`). He chose: the AI panel says
  "choose a provider", and the automatic speaker-naming pass waits until
  somebody has chosen. That has to cover `sweep_speaker_passes`
  (`scribe/stages/finalize.py:155`, called from `scribe/app.py:261`), which runs
  at every app start over the whole back catalogue with no ceiling. It reverses
  "Defaults: commercial providers" in `2026-09-01-myscribe-design.md:219`.
* **R2 - An Ollama that is already there is left alone, in every state.**
  Present with no chat-capable model: one sentence, the copyable
  `ollama pull qwen3.5:4b`, and a "Check again" - never an offer to pull into
  it. Installed but not running: one sentence and a "Check again"; it is never
  started, because people stop Ollama on purpose to free video memory.
* **R3 - Three extra questions go in**, because each is today a separate thing
  afterwards: adopt an existing library, a folder to watch, start MyScribe at
  login (default No). He did not select "a start script or shortcut for a
  clone" (U5), so that one is declined. The install location before the sync
  (M2) was taken on the facts and not as a preference.
* **R4 - Record only.** This spec, the backlog tasks and ADR-015 as Proposed.
  No code in that step.

## W - what the synthesis had wrong

* **W1** `--check` does not "touch nothing". Its serve check starts the app on
  the real data directory, whose lifespan migrates the database, reconciles,
  and queues LLM jobs. Proof runs on a scratch `SCRIBE_DATA_DIR`, or is
  replaced by "/health already answers" when the app is up.
* **W2** "`install.py` refuses to sync while MyScribe answers /health from this
  checkout" cannot be determined: `/health` returns only `ok` and `version`,
  the launcher's single-instance rule is port-only, and the app writes no run
  file. It is an open question inside its task, with two named candidates.
* **W3** The task that implements R1 needs a criterion that names
  `sweep_speaker_passes`.
* **W4** The gemma4:12b offer has to state its unit. Robert's "16 GB" card
  measures 17,179,344,896 bytes, which is 15.9995 GiB, and
  `scribe/llm/ollama.py:221-224` records that the 12B failed to start on it
  beside Whisper. The threshold sits clearly above that class of card, is
  labelled an estimate, and qwen3.5:4b stays the default.
* **W5** ADR-001: GPU work runs only inside a runner child, one at a time.
  `--prove` must not load models from the setup child while the app may be
  transcribing. When `/health` answers, it queues the existing `doctor` job or
  reports "not tested (app running)".
* **W6** The "Recognise speakers" guard rested on a wrong model:
  `default_diarize` is "the last options submitted", rewritten on every upload,
  so a provenance marker on it goes stale. Setup does not write that row at
  all; "a job keeps its transcript when the speaker weights cannot load"
  carries the behaviour. Read literally, that also removes
  `--diarize/--no-diarize` from `scribe.setup`, which the spec lists as an open
  question because nobody asked for it to go.
* **W7** The "first run works with no environment yet" task was counted under
  two parents. It lives under TASK-089 only.

## M - what the synthesis had left out

* **M1** The Ollama marker broke R2. It is written only after the installer
  finished successfully; the installer is never offered while any Ollama binary
  exists; the marker is cleared once Ollama has been seen ready.
* **M2** The release user's disk. `env/`, `python/`, `cache/`, `data/` and
  `models/` all hang off the home, so the location can only be asked before the
  sync: where everything goes, with the free space per volume and the total.
  Persisted in a one-line pointer file that `home_dir()` reads, which keeps it
  stdlib-only and inside ADR-011.
* **M3** No free-space check ran before the ~3 GB `uv sync`, in either door.
* **M4** The gate. When the first-run sitting appears was never defined, and
  people who finished the old setup would never see the new questions. Reading
  the gate must be cheap; a deliberately skipped question does not nag.
* **M5** Adopting an existing library. It interacts with R1: adopting a library
  with no provider chosen must not queue cloud jobs.
* **M6** Proxy and offline handling was absent. See "measured since" below.
* **M7** The pinned Ollama release needs an owner: a step in
  `docs/RELEASING.md`, and a CI check that the pinned URL and digest still hold.
* **M8** Uninstall. An Ollama and a model that MyScribe installed survive an
  uninstall and belong to the user, and the texts have to say so.
* **M9** Ordering. The API-key question, the found-credentials table and the
  Ollama detection must not wait for the models re-pin.
* **M10** How the setup child is stopped on Quit, and how `taskkill /T` treats
  a freshly installed Ollama, is tested before anything promises Quit is clean.
* **M11** `install.py` sharing the launcher's code versus only its sequence is
  argued in ADR-015 as a considered option, not settled in a files table.

## U - the questions nobody had proposed

* **U1** Where everything goes, asked before the sync (with M2). Taken.
* **U2** "An existing MyScribe library was found - use it, or start a new
  one?" (with M5). Taken, R3.
* **U3** "Start MyScribe when you log in?" Taken, R3. It needs a counterpart in
  Settings first: `scribe/setup.py:21-23` says an answer given at the start must
  never be the only place it can be given.
* **U4** "Is there a folder MyScribe should watch?" Taken, R3. It writes the
  same `watch_folder` row Settings writes.
* **U5** A start shortcut or script for a clone. Declined: Robert did not
  select it.
* **U6** One consent that shows the total download, where today the sum is
  spread over separate moments (with M2). Taken.
* **U7** "Where should Ollama keep its models?" That is Ollama-side
  configuration. Not included; it is an open question for Robert.
* **U8** A proxy is detected and shown, not asked about (with M6). Taken.
* **U9** Deliberately not asked, and said so that nobody adds them later: the
  transcription language (removed on evidence on 2026-09-02, see
  `scribe/web/transcribe_dialog.py:63-69`), the timezone, the language of the
  interface, and a GPU-versus-CPU choice (ADR-012: one lock, a per-platform
  torch source).

## Verified by nobody, as of 2026-09-20

The real Tk window. Anything on macOS. Whether `OllamaSetup.exe /VERYSILENT`
starts the daemon. An OpenRouter endpoint that checks a key for free.
mlx-whisper loading from a local folder. Process lineage under `taskkill /T`
after the Ollama installer exits. The byte counts assumed for 20 GB and 24 GB
cards behind the 12B threshold.

## Measured since the review

The proxy item started as an inference from the HTTP libraries' defaults. It
was then measured on Robert's Windows machine on 2026-09-20, with
`HTTP_PROXY` pointing at a closed port and `NO_PROXY` unset, against the Ollama
answering on `127.0.0.1:11434`:

```
urllib default              2.12s  FAIL  URLError      [WinError 10061]
urllib ProxyHandler({})     0.00s  OK    {"version":"0.34.0"}
httpx2 default              2.23s  FAIL  ConnectError  [WinError 10061]
httpx2 trust_env=False      0.00s  OK    {"version":"0.34.0"}
```

The probe and its full output are in `2026-09-20-installer-evidence/`. What
this proves is the behaviour of the two library defaults that MyScribe's
probes are built from (`scribe/llm/ollama.py:170`, the launcher's
`running_instance`). What it does not prove: those two probes themselves were
not called, and a system proxy set in the Windows registry, macOS and Linux
were not run. So behind a proxy variable MyScribe can today report a running
Ollama as unreachable - a defect in the app as it stands, not only a concern
for the installer.

Also checked on 2026-09-20, against Ollama's install scripts as served that
day (their digests are in the evidence folder): on macOS `install.sh` stops a
running Ollama and removes `/Applications/Ollama.app` before it downloads, on
Linux it removes an existing `lib/ollama` and needs root, and `install.ps1`
installs unconditionally with `/VERYSILENT /NORESTART /SUPPRESSMSGBOXES`. That
is why "only when it is missing" is MyScribe's own test and is never delegated
to those scripts.
