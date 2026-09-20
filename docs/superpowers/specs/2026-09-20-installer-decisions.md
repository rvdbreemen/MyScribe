# The installer design: what was asked, what was decided, and what the review found

This is the key to `2026-09-20-installer-design.md` and to the backlog tasks
under TASK-089. Both refer to decisions and review findings by short keys -
R1, W5, M2, U7 - and this file says what each key means. It is a record, not
a design: the design is in the spec, the work is in the backlog, and the
durable rules are in ADR-015, ADR-016 and ADR-017.

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

## G - what the grill of ADR-015 decided

Robert ran `/adr-kit:grill ADR-015` later on 2026-09-20, one question at a
time. Each answer is recorded with its question in the record it belongs to;
this is the short form.

* **G1 - Three records, not one.** adr-kit asks for one decision per record.
  ADR-015 keeps the engine and its JSON contract. ADR-016 is R1, "a missing
  provider row selects no provider": it has no open question, so it can be
  accepted on its own and TASK-089.07 builds under it. ADR-017 is the rule
  for third-party software and carries the Ollama questions.
* **G2 - `install.py` shares the launcher's sequence, not its code**, plus one
  contract test that gives both doors the same lock and stamp and demands the
  same sync decision. Counted: about 100 of the launcher's 758 lines are
  plumbing both need. Fetching the tools and checking their sha256 is not in
  the launcher at all - it copies them out of the payload - so `install.py`
  shares that part with `packaging/build_payload.py`. This settles M11.
* **G3 - `--diarize/--no-diarize` stays**, as an explicit choice and nothing
  else. W6 removed the guard - setup switching the speakers default off by
  itself - and that stays removed. The flag's removal came from reading
  "at all" literally; nobody asked for it. Still to fix under TASK-089.09: a
  tier answer alone writes the diarize row today (`scribe/setup.py:125-136` at d80360a).
* **G4 - `--hf-token` goes now.** It is still recognised and is refused with a
  sentence saying where a token belongs - `HF_TOKEN` in the environment or
  `.env`, or the document piped to `--apply-stdin` - and exit 2. "A secret
  never rides on a command line" then has no exception.
* **G5 - `/health` gains two fields**: the source tree the app runs from and
  the data directory it serves. An answer without them is doubt, and doubt
  refuses the sync. The same fields tell the proof whether the answering app
  serves this library. The host check covers the whole app
  (`scribe/guard.py:110`), so no web page can read it. Blind spot, kept and
  named: an app on a port nobody mentioned. This settles W2.
* **G6 - The pull may be offered again later.** While the marker stands, a
  sitting opened with `--setup` offers the unfinished pull into MyScribe's own
  Ollama again, as a question whose default is No. Chosen against the agent's
  recommendation of the narrower rule with no marker at all.
* **G7 - The marker is bound to version and path, and any doubt drops it.** It
  fails to the safe side: after Ollama updates itself the marker lapses and
  somebody types one command. The path alone could not tell MyScribe's Ollama
  from one the user installed afterwards at the same default path.
* **G8 - Where a new Ollama keeps its models is asked only when the default
  volume is too small.** On Windows MyScribe then sets `OLLAMA_MODELS` as a
  variable of the user's account before the installer starts; on macOS and
  Linux it stays a sentence with the command. Whether the daemon the silent
  installer starts inherits it is unverified and belongs to the sandbox run
  of TASK-089.18. This settles U7.
* **G9 - The Mac is somebody else's.** macOS criteria are bundled per milestone
  into one list of commands and expected output, and until that sitting they
  say "not run". The README's sentence about the M2 gets its date and says
  whose machine it was.

Established by the agent during the grill, as facts and not decisions:
OpenRouter checks a key for free - an authenticated request to
`https://openrouter.ai/api/v1/key` answers 401 for a bad key, per its
documentation, and an unauthenticated probe got 401 there where a nonsense
path got 404; no real key was used. The pinned Ollama release v0.34.2 exists,
is the latest and is not a prerelease; `OllamaSetup.exe` is 1,569,993,232
bytes and `Ollama.dmg` 197,873,582, digests as recorded in ADR-017; the
Windows signer's name is still unverified. And one proposal by the agent: how
a release points at an adopted library is TASK-089.19's to settle, below the
level of a decision record.

Still open after the grill, and both are measurements: what the silent Ollama
installer starts and how a tree kill treats it (ADR-017, TASK-089.18), and
whether the app's own two probes fail behind a proxy the way the library
defaults did (ADR-015, TASK-089.05).

## Verified by nobody, as of 2026-09-20

The real Tk window. Anything on macOS. Whether `OllamaSetup.exe /VERYSILENT`
starts the daemon.
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
