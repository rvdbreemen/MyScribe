# Installing MyScribe: detect first, ask what is still open, prove the result

Status: revision 1, 2026-09-20, written in an autonomous session. Nothing
here is built: Robert's fourth decision of the day (R4) was that this step
records - this spec, the backlog tasks and ADR-015 as Proposed - and writes
no code. The document came out of a design run on 2026-09-20: ten mapping
readers went through the current onboarding and came back with 112
evidence-backed gaps; four independent designs were each judged through
three lenses, and three of the four tied at 7.0; a synthesis chose a spine
and grafted the rest onto it; a completeness critic then found seven factual
errors (W1-W7) and eleven omissions (M1-M11) in that synthesis, plus nine
questions nobody had asked (U1-U9). All eighteen findings are folded in
below, and each of the nine questions is taken or declined by name: U1 and
U6 with M2 and U2 with M5 (§2), U8 with M6 (§3.2), U3, U4, U5 and U7 in
§1, U9 at the end of §1. Robert decided four forks
interactively the same day (R1-R4, §0). The choices are stated with their
reasons so they can be overturned one at a time. A cross-check against the
backlog tasks and the ADR-015 draft, the same day, moved this text where the
three disagreed: line 3 of §1's table, the 12B threshold (§3.3),
`CUDA_VISIBLE_DEVICES` (§3.8), the names of the Settings sections, the wait
in §7, and the second fact in the pointer file, now marked as a proposal.
Robert's grill of ADR-015, later that day (G1-G9), split the record in
three (§3.14) and moved this text in §1 (row 8a), §2 (sequence, not code),
§3.1, §3.3, §3.9 and §4 to §8. Code is cited by file and line as of commit
d80360a, the state before TASK-089.03's build, which moves lines in every
file it touches; Accepted records are cited by section. The keys used
throughout - R for Robert's decisions, W, M and U for the critic's
findings, G for the grill - are spelled out in
`2026-09-20-installer-decisions.md`, and the measurements quoted here are
in `2026-09-20-installer-evidence/`.

The runner-up is a `/welcome` page in the web UI. It tied at 7.0 and was the
maintainers' favourite at 7.5, and it does five things better than what
follows. It shrinks the frozen launcher where this design grows it: about
130 lines of Tk form go, and the launcher never touches an answer or a
secret, not even over stdin. Every question becomes TestClient-testable on
all three CI runners, where this design still needs a person at the screen
once per OS for the Tk pixels. Validation is live: a progress bar per
download, a Re-check on every red row. Headless, an AppImage without Tk and
Windows all get the identical page, where this design gives a headless
Windows start nothing. And a re-run shows the truth about the machine by
construction. It was not chosen for three reasons. It reverses the shipped
behaviour of TASK-040.06 - one modal window before the app starts - and adds
a second moment of attention after the 3 GB sync. (The synthesis called the
modal Robert's own choice of 2026-09-18. The task does not say that: what it
attributes to him that day is the four things a first run settles, line 27
of the task file; the modal appears only in the implementation notes, line
57. Whether he chose it is unverified, and §8 asks him.) It runs a 1.57 GB
third-party installer as a runner job, which blocks every transcription
while it runs (one runner at a time, `scribe/jobs.py:148`) and sits inside
the process tree that Quit force-kills
(`packaging/launcher/myscribe_launcher.py:376-380`). And a redirect on `/`
while `setup.json` is missing would bounce an existing library into a
wizard. If Robert overrules: take its `firstrun.state()` and its rule that
front-ends never apply answers; do not take the install job without first
solving the lane, the kill tree and the marker.

Scope: the path from "downloaded it" or "cloned it" to a machine that
transcribes. `python -m scribe.setup` becomes the one place that detects,
asks, writes and proves; the frozen launcher's first run, a new stdlib
`install.py` for a clone, and the headless console all render it. Three
changes in the app itself are in scope because skipping is only honest
with them: no provider row means no provider (R1, ADR-016), a job keeps its
transcript when the speaker weights cannot load, and an AI action that
cannot be answered shows what is missing. A fourth was decided in the grill
(G5, §3.9): `/health` says which checkout and library it serves. Not in
scope: the Inno Setup wizard's pages (it keeps its one desktop-icon
question), code signing, and anything in the transcribe pipeline beyond the
two loaders in §3.7.

## 0. What is asked, and what is already there

The ask, in Robert's words across the session of 2026-09-20:

1. One installation script that works on all platforms.
2. It asks for API keys when needed; skipping must be an option for every
   question.
3. It asks for the Hugging Face token when one is not already found.
4. It downloads the models.
5. "Liever een paar extra vragen tijdens installatie, dan weer los extra
   dingen doen" - rather a few extra questions during installation than
   separate things to do afterwards.
6. A standalone Python installer for a plain clone, alongside the
   platform-specific installers.
7. Detect before asking: test whether keys are already in `.env`, in the OS
   environment, or in a config file.
8. Offer to install and configure Ollama, and offer a model such as gemma4.
9. But only when Ollama is absent: "Als Ollama er al is, dan niets doen."

His four decisions, the same day:

* **R1 - no provider row means no provider.** The AI panel says "choose a
  provider", and the automatic speaker-naming pass waits until somebody
  has chosen. This reverses "Defaults: commercial providers"
  (`docs/superpowers/specs/2026-09-01-myscribe-design.md:219`).
* **R2 - an Ollama that is already there is left alone, in every state.**
* **R3 - three extra questions go in**: adopt an existing library, a folder
  to watch, start MyScribe at login (default No). A start script or
  shortcut for a clone was offered and not selected: declined, not built.
* **R4 - record only.**

What exists today (every line below was opened on 2026-09-20):

* `scribe/setup.py` holds "four answers, and no more than four" (`:9`):
  the token, the provider, the tier, fetch now. A fifth rides along that
  the docstring does not count: `Answers.diarize` (`:51`) with
  `--diarize/--no-diarize` (`:164-165`), which the launcher would pass
  (`:481-482`) and its form never sets - a grep for `diarize` in the
  launcher finds those two lines only. `Answers` has no key field
  (`:46-53`) and `main` has no key flag (`:158-168`). `needed()` reports
  six facts, none about whether the chosen provider can answer (`:64-81`).
  `apply()` writes the stamp unconditionally at the end (`:142-154`), so a
  bare run stamps "done" having asked nothing. The stamp is
  `{provider, tier, hf_token, fetched}` (`:145-150`) and "done" means the
  file exists (`:60-61`). Its docstring already sets the rule this design
  keeps: "an answer given once at the start must never be the only place
  it can be given" (`:21-23`).
* The launcher asks in a fixed Tk form, `ask_setup` (`:515-594`): the
  provider radio starts on a hard-coded `ollama` (`:558`), the tier on
  `turbo` (`:565`), the download text says "about 1.6 GB" whatever was
  picked (`:574`), and the `layout` argument is unused. The token travels
  on argv (`:475-476`); the child's stdin is `DEVNULL` (`:280`). The gate
  is `setup_needed`: the stamp does not exist (`:462-463`).
* **The first run is ordered wrong.** `begin()` runs `run_setup` - a
  `Popen` on `env_python` - before `launch.run()`, and the sync that
  creates that Python lives inside `launch.run()` (`:651-668` against
  `:430-439`). On a fresh home the `Popen` raises `FileNotFoundError` on
  the worker thread, the tuple is never finished and `launch.run()` is
  never called. Read here; a reader's function-level probe in the design
  run printed exactly that. Nobody has seen it in a real Tk window.
* A headless or Tk-less start asks nothing: `run_headless` has no setup
  step (`:500-512`, `:730-736`). Quit stops the app and only the app
  (`:617-621`); `run_streaming` keeps its `Popen` local (`:277-286`).
* Everything a release installs hangs off one home: `env/`, `python/`,
  `cache/`, `data/`, `.env`, `logs/`, `pycache/` and `bin/` (`Layout`,
  `:98-105`), and the models under `data/` (`scribe/paths.py:40`). The
  launcher forces `SCRIBE_DATA_DIR=<home>/data` into the app's environment
  (`:200-213`). The home is `--home` (`:693`, `:709`), else
  `MYSCRIBE_HOME`, else a per-OS default (`home_dir`, `:59-71`); neither
  override is persisted or asked for.
* The single-instance rule is per port, not per install: whatever answers
  `/health` with `ok` on the port is "MyScribe" (`:306-312`, `:421-426`),
  and `/health` returns `{"ok": true, "version": ...}` and nothing else
  (`scribe/app.py:296-298`).
* The app's lifespan always migrates, reconciles, sweeps recordings and
  runs `finalize.sweep_speaker_passes` (`scribe/app.py:247-261`), which
  queues one LLM job per diarized, non-private recording that was never
  asked, with "no ceiling on how many it queues"
  (`scribe/stages/finalize.py:155-220`, the sentence at `:177`), against
  whatever `default_provider()` returns (`:437`).
* `default_provider()` falls through to `tasks.DEFAULT_PROVIDER`, which is
  OpenRouter (`scribe/llm/__init__.py:164-175`, `scribe/llm/tasks.py:456`).
  It has eight call sites - `finalize.py:437`, `ai_ui.py:650`, `:837`,
  `:953`, `:1016`, `:1181`, `:1202`, `library.py:994` - and
  `scribe/stages/llm_stage.py` repeats the fallback at `:94`, `:271` and
  `:345`.
* `scribe/models.json` pins two things: `mlx-community/whisper-large-v3-turbo`
  (1,613,977,880 bytes) and `pyannote/speaker-diarization-community-1`
  (32,821,421 bytes). `catalogue()` applies no platform filter
  (`scribe/models.py:89-102`), while Windows and Linux load
  `WhisperModel(model_name, ...)` from the hub cache
  (`scribe/stages/transcribe.py:583`). `present()` counts a hub-cache copy
  only when no directory is named (`:163-164`) and `ensure()` always names
  one (`:245`, `:251`), so "already here" and "must download" disagree.
  `ensure()` walks the catalogue in file order (`:248`), so the public
  1.6 GB comes before the gated 33 MB can refuse. 401 and 403 share one
  sentence, in the fetcher (`:219-224`) and in the doctor
  (`scribe/doctor.py:533-534`).
* `diarize` defaults to on (`scribe/options.py:42`). `WeightsUnavailable`
  (`scribe/stages/diarize.py:126`, raised at `:392`) is caught by nothing,
  and the fallback pipeline is built on `pyannote/segmentation-3.0`
  (`:80`), which answered HTTP 401 without a token on 2026-09-20. So
  `.env.example:4-8` and the README promise a fallback that needs the very
  token it is a fallback for.
* The doctor's `database` check and its GPU smoke both migrate the library
  they are pointed at, and the smoke writes a `smoke` row
  (`scribe/doctor.py:267-271`, `:411-422`). Settings never loads a model
  in the web process: its GPU checks are a `doctor` job
  (`scribe/web/settings.py:8-14`, `:154-163`), and a provider test is a job
  too (`scribe/web/ai_ui.py:1086-1108`).

The confirmed gaps this work exists for:

1. **No API key is ever collected.** Not in `Answers`, not as a flag, not
   in the dialog, although both let you choose OpenRouter or OpenAI. A
   reader's scratch run: `--provider openrouter` exits 0 with "saved:
   provider" and no warning. `ai_run` checks kind, transcript, pin, prompt
   and window and nothing about a key (`scribe/web/ai_ui.py:815-870`), so
   the first Summary becomes a failed job on the board.
2. **Nothing checks Ollama**, which is the preselected provider.
   `OllamaProvider.available()` is the only probe
   (`scribe/llm/ollama.py:384-409`) and it cannot tell "not installed" from
   "not running" - the distinction requirement 9 rests on. `models()`
   returns names only (`:411-419`), so an embedder counts as a model, and
   `__init__` accepts `conn` and ignores it (`:249-254`), so the Settings
   readiness line, built with `cls(conn).available()`
   (`scribe/web/ai_ui.py:1146-1148`), checks the class default and not the
   saved model. "Test now" does use the saved one: `queue_provider_test`
   resolves the model from the settings row (`:1094-1107`).
3. **`hf_token()` searches fewer places than `api_key()`.** The token
   lookup reads the settings row, then `HF_TOKEN` and `HUGGINGFACE_TOKEN`
   in `os.environ` (`scribe/stages/diarize.py:258-276`). The key lookup
   also reads the Windows registry, per variable, user hive then machine
   hive (`scribe/llm/base.py:194-222`, `:225-268`). Neither reads
   `HUGGING_FACE_HUB_TOKEN` or Hugging Face's own login file. Two more
   copies disagree further: `models.default_token()` reads the environment
   only (`scribe/models.py:307-312`), and the doctor calls
   `hf_token(None)`, so it cannot see a token saved in Settings
   (`scribe/doctor.py:482-485`).
4. **A blank variable counts as set.** `load_dotenv` applies the file with
   `os.environ.setdefault` (`scribe/env.py:65-66`). Probe of 2026-09-20,
   scratch file, `PROBE_A=""` in the process and `PROBE_A=real-value` in
   the file: after loading, the process still holds `''` - the real value
   never arrives. The other direction too: `PROBE_B=` in the file becomes
   an empty variable in the process, and the seeded copy of `.env.example`
   holds nothing but such lines and comments (`prepare_home`, launcher
   `:258-263`). The
   lookups read here strip and skip a blank; the doctor's second look does
   not strip (`scribe/doctor.py:485`).
5. **A clone user never meets `scribe.setup` at all.** Verified here with
   `git grep`: outside tests and docs the only caller is the frozen
   launcher (`:474`). `python -m scribe` has no first-run hook
   (`scribe/__main__.py:80-92`), the lifespan has none, and the README's
   "From a clone" and "Then" sections never mention it (`README.md:113-150`);
   its one appearance is a row in the command table (`:224`). The start
   scripts still point at requirement files that no longer exist
   (`scripts/start.sh:38`, `scripts/start.ps1:53-54`,
   `scripts/mac-acceptance.sh:45`), and the README's bare `uv sync`
   (`:122`) runs whatever uv is on `PATH` - here 0.5.9, against a pin of
   0.12.13 in `packaging/tools.json`.
6. `scribe.setup`, `scribe.models` and `scribe.doctor` import `scribe.paths`
   before they load `.env` (`setup.py:34` against `:170`, `models.py:37`
   against `:330`, `doctor.py:36` against `:814`), and `paths` freezes
   `DATA_DIR` at import (`scribe/paths.py:4`). The app does it the other
   way round and says why (`scribe/__main__.py:82-86`).

What was measured on this machine on 2026-09-20, values never printed:

| What | Result |
| --- | --- |
| `HF_TOKEN`, `HUGGINGFACE_TOKEN` | both in the process and in the registry's machine hive; `.env` defines a **different** value for each, which is never used |
| `OPENROUTER_TOKEN` | in the process and in the machine hive |
| `OPENAI_API_KEY` | in `<repo>/.env` only |
| `HUGGING_FACE_HUB_TOKEN`, `SCRIBE_DATA_DIR` | nowhere |
| Ollama | 0.34.0 answering; 8 models, 3 with the `completion` capability (`gemma4:12b`, `qwen3.5:4b`, `qwen3.5:9b`); every model carried a `capabilities` key on `/api/tags`. `OLLAMA_MODELS` is set in the registry's user hive and points at a folder on D: (`reg query HKCU\Environment`), so on this machine `state()` can never read *absent* (§3.3) |
| The library | `<repo>/data/myscribe.db`, 107 MB; no `setup.json`; no `%LOCALAPPDATA%\MyScribe` |
| `.venv` on disk | 5,321 MiB (`du`), dev group included |
| Ollama v0.34.2, the latest release (GitHub API) | `OllamaSetup.exe` 1,569,993,232 bytes, sha256 `8c9eb7ba…066a8b`; `Ollama.dmg` 197,873,582 bytes, sha256 `ca3c5c15…9d5d7b` |
| `HTTP_PROXY` at a dead port, no `NO_PROXY` | plain `urllib` and a default `httpx2` 2.12.0 client both fail to reach 127.0.0.1:11434; `ProxyHandler({})` and `trust_env=False` both answer at once |
| A read-only open of a cleanly closed WAL database (a scratch file, SQLite 3.45.3, the venv's Python on Windows) | `mode=ro` creates a `-shm` (32,768 bytes) and a `-wal` (0 bytes) beside it and leaves them there after close; the main file stays byte-identical. `mode=ro&immutable=1` creates nothing. `mode=ro` on a missing file raises "unable to open database file" and creates nothing |

Relied on from the design run's readers and not re-run here: the
`llm_provider` row on this machine says `openrouter`; `--status` takes
about 4.6 s and `doctor --no-gpu` 6.0 s; `.env` is created 0644 under a
0022 umask in WSL.

Four conclusions follow:

1. **The questions have to become data.** A fixed form cannot leave out a
   question whose answer was found, and a second front-end cannot reuse a
   form. Requirement 7 means the question list gets shorter per machine.
2. **The sitting has to move behind the sync.** Detection needs the app's
   own lookup order, and that needs the environment. Asking first would
   mean re-implementing the lookups in stdlib code. The cost is two waits
   with one sitting between them, and the window says so up front. One
   question cannot move: where everything goes (M2).
3. **Skipping is the default most people get**, so every skip path has to
   end somewhere safe: no provider, no cloud (R1); no token, still a
   transcript (TASK-089.08); no key, a card and not a failed job (TASK-089.10). The
   first of the three is TASK-089.07.
4. **Requirement 9 needs MyScribe's own gate.** Ollama's scripts do not
   stop at "already there": `install.sh` kills a running Ollama and removes
   `/Applications/Ollama.app` before installing (`:62-69` of the script as
   fetched on 2026-09-20), and on Linux it creates a user, enables a
   service and may install NVIDIA drivers, all under sudo.

## 1. The user's view

A release: download and install as today. The launcher window then asks
one thing before it downloads anything - where everything goes - installs
the environment ("about 3 GB; I will have a few questions after this, you
can walk away now"), comes to the front, shows what it found on this
machine, asks only what is still open in one dialog, then downloads,
proves and starts. A clone: `python install.py`, which does the same in the
terminal. Both end on a report of measurements, not on "done".

```
Found on this machine
  Hugging Face token   HF_TOKEN (environment; also Windows registry, machine)
                       note: .env defines a different value, which is not used
  OpenRouter key       OPENROUTER_TOKEN (environment; also Windows registry, machine)
  OpenAI key           OPENAI_API_KEY (.env at D:\...\MyScribe\.env)
  Ollama               0.34.0 running, 3 chat models (5 embedding models
                       ignored) - left alone
Still open
  Transcription quality   (o) Turbo   ( ) Maximum   ( ) Leave as it is
                          Skipping: nothing is written.
  ...
[ Ask me next time ]                                    [ Save and start ]
```

The questions as they now stand, in the order asked. Every one can be
skipped; "later" is where the same answer can be given afterwards. Settings
sections are named by their sidebar labels (`scribe/web/settings.py:435-442`):
the token field and the transcription defaults sit under "Transcription",
the doctor under "This machine".

| # | Question | Asked when | Default | Skipping costs | Later |
| --- | --- | --- | --- | --- | --- |
| 1 | **Where should MyScribe keep everything?** Shows the default home, the free space per volume, the total this install will download with its parts, and the disk it needs - the 10 GB the app keeps free included (§2). | Release only, before the sync, and only when no pointer file exists, no `--home` or `MYSCRIBE_HOME` is given and the default home holds no environment yet. Asked again when a pointer names a folder that is not there (§2). | Today's location | Everything - environment, Python, cache, library, models - lands on the system volume. | Quit, move the folder, put its path in the pointer file; `--setup` shows the path in force and says this. Nothing is moved for you. |
| 2 | **Do you already have a MyScribe library? (M5, U2)** When one was found: "An existing library was found at `<path>` (N recordings). Use it, start a new one, or name another folder?" A release asks even when none was found - "start new" preselected, and "I already have one: name its folder" - because a release cannot see a clone's library (§2). In a clone with none found: keep recordings in `<repo>/data`, or somewhere else? | After the sync, first. A release: every first sitting. A clone: when a `myscribe.db` exists somewhere other than the target - `<repo>/data`, the default home, or a `SCRIBE_DATA_DIR` found in any environment layer. Not asked when the target already holds a library. | Start new (clone: `<repo>/data`) | A library that was found is left untouched and the sitting says where it is. In a clone the data stays inside the git working tree, and one line says `git clean -fdx` would delete it. | `--setup`, which lists this entry for as long as the target library holds no recordings; or `SCRIBE_DATA_DIR` in `.env` (clone) |
| 3 | (macOS clone) **ffmpeg is not on PATH.** Not a question: one information line gives the command `brew install ffmpeg`, and the person runs it. MyScribe does not run brew, because ADR-017's rule allows only a pinned, sha256-verified artifact and brew's is neither (TASK-089.17). The synthesis had a y/n here; the number stays so the others do not move. | darwin, a clone, ffmpeg or ffprobe missing. | - | The report marks ffmpeg FAIL: nothing transcribes until it is on `PATH`. | `brew install ffmpeg`, then `python3 install.py --check` |
| 4 | **Hugging Face token** for "Recognise speakers". Hidden input; the conditions page opens with one click. Checked at once with one announced request. | No local pipeline and no token found anywhere (§3.2). A found token that is later refused reopens this. | Skip | Nothing is saved. Transcription is unaffected; a job that asks for speakers ends with a transcript and a note (TASK-089.08). | Settings > Transcription > Hugging Face token |
| 5 | **Who answers questions about a transcript?** Each choice shows what was detected. Cloud choices carry "sends transcript text off this machine; recordings pinned private are always refused". | No `llm_provider` row. A re-run shows the stored value and never resets it. | Ollama when it is ready, else "decide later". A cloud provider is never the Enter default. | This question writes no row, so there is no provider (R1): the AI panel says "choose a provider" and the automatic speaker-naming pass waits. One later answer still writes the row: a yes to question 7, whose text says so. | Settings > AI providers |
| 6 | **API key for `<provider>`.** Hidden input. | The answer to 5, or the stored provider, is a cloud provider and no non-blank key is found for it. | Skip | The provider is saved without a credential; Summary and Chat show a no-key card (TASK-089.10). | Settings > AI providers > Save key |
| 7 | **Ollama is not on this machine. Install it, and let it answer questions about a transcript?** Exactly what will run is shown before the question (§6). When 5 was left open the text adds "MyScribe will then use this Ollama", so a yes is also an explicit answer to 5 (§3.3). | Only in state *absent*, and only when the answer to 5 is Ollama or undecided. | No | Nothing is downloaded or run. Recordings pinned private cannot use AI until a local provider exists. | The same offer, for as long as Ollama is absent; or ollama.com/download |
| 8 | **Which model should the new Ollama get?** | Same sitting, shown only when 7 is yes. Never for an Ollama that was already there. | `qwen3.5:4b` (3.4 GB) | Ollama is installed with no model; the report gives the copyable pull command. | `--setup` while this install's own marker stands and Ollama answers, as a question whose default is No (§3.3, G6) - the one exception to "never pulled into", and never for an Ollama that was already there; or `ollama pull` |
| 8a | **Ollama's default volume is too small for this model. Keep its models with MyScribe instead? (U7, G8)** Both numbers are shown. Which folder that is, TASK-089.18 names: outside the git working tree in a clone (`git clean -fdx`, row 2) and nothing an uninstall removes (M8). On Windows a yes sets `OLLAMA_MODELS` as a variable of the user's account before the installer starts, so that the daemon it starts gets it - read here as: once the artifact has passed its sha256 and signer check, immediately before it runs, so a failed download writes nothing (§3.3 says what a failed installer leaves). The variable outlives the sitting, so it is shown with the command line (§6). On macOS and Linux it stays a sentence with the exact command. Unverified: whether the daemon the silent installer starts inherits the variable (TASK-089.18's sandbox run); until that run, nothing says the models went where they were asked to go without checking. The number is 8a so the others do not move. | Same sitting, only when 7 is yes and free space at Ollama's default model folder is short of the model chosen in 8. With room, Ollama keeps its own default and nothing is asked. Never for an Ollama that was already there. | Yes, because the other answer ends the offer | The offer ends with both numbers: nothing is installed and nothing is pulled. That reads "the offer" as all of question 7, as TASK-089.18's criterion 10 does, because an Ollama with no room for a model answers nothing; Robert's words were only "ends the offer". Overturn by installing without a model, as row 8's skip does. | The same offer, for as long as Ollama is absent |
| 9 | **Which of your Ollama models should MyScribe use?** Chat-capable models only. Writes MyScribe's own row; changes nothing in Ollama. | Provider is Ollama, it was already there and running, no `llm_model_ollama` row, the default is not pulled and another chat model is. | Skip (TASK-054: never save a model nobody chose) | MyScribe keeps asking for `qwen3.5:4b`; the AI panel names the missing model. | Settings > AI providers > model |
| 10 | **Transcription quality: Turbo or Maximum?** Real download size for this platform; without a GPU, a line that Maximum is several times slower here. | A first sitting only. Opens on the stored default. | The stored default | Nothing is written. | The transcribe dialog, or Settings > Transcription |
| 11 | **Download the speech weights now?** Names the repositories, the bytes and the free space on the volume they land on. The gated 33 MB goes first. | Something this platform and tier load is missing. | Yes | The first transcription downloads inside the job with no progress shown. The report says so. | `python -m scribe.setup --fetch-models` |
| 12 | **Is there a folder MyScribe should watch for new recordings?** | No `watch_folder` row exists. | Skip | Recordings come in through the dialog only. | Settings > Watch folders (the same row) |
| 13 | **Start MyScribe when you log in?** One line says why it matters: watch folders and feeds only work while the app runs. | First sitting, and only where there is something to start (§3.10). | No | Nothing is written. | Settings (the counterpart is built first, U3) |
| 14 | **Test transcription now with the bundled 30-second clip?** | The weights are present, or 11 is yes. Runs after the downloads. | Yes | The report says "transcription: not tested (skipped)", never "ok". | `python -m scribe.doctor`; Settings > This machine |
| 15 | **Also ask `<cloud provider>` for one word now?** A fraction of a cent. | A cloud provider with a key, found or typed. | No - anything that costs money defaults to No | "AI provider: configured, not tested". | Settings > AI providers > Test now |
| 16 | (clone) **Start MyScribe now?** | Last, a clone only. A release launcher always starts the app. | Yes | The start command is printed. | `python install.py --start` |

* **Two buttons end the sitting.** "Save and start" sends the answers and
  stamps the sitting. "Ask me next time" sends nothing and stamps nothing,
  which keeps the 2026-09-18 meaning of closing the window: next time, not
  never. A question skipped *inside* a saved sitting is recorded as
  skipped and does not come back by itself (§2, the gate).
* **A refused answer is treated like a skip.** A token the Hub answers 401
  or 403, a key OpenAI rejects: not saved, and its question reopens with
  the reason. 401 reads "token not recognised"; 403 reads "conditions not
  accepted yet" with the URL.
* **Ollama, present, in its three states (R2).** *Installed, not running*:
  one sentence - "Ollama is installed at `<path>` but not answering. Start
  it, then Check again." - and a Check again. It is never started: people
  stop Ollama on purpose to free VRAM (video memory). *Running, no
  chat-capable model*: one sentence, the copyable `ollama pull qwen3.5:4b`
  and a Check again. Never an offer to pull into it. *Ready*: one line
  ending "left alone". In all three nothing is installed, upgraded,
  started, pulled or reconfigured, and the provider question lists Ollama
  with its state ("needs a model") rather than recommending it. A state
  that cannot be read counts as present, never as absent. One exception,
  named here because the rule above reads as absolute: an Ollama that
  *this install* put on the machine and that has never been ready - the
  marker of §3.3 (M1, G6, G7) - is still offered the one model pull that
  was agreed to in the same yes. That is not an Ollama that was "already
  there". Even then nothing is started: with the marker standing and
  Ollama not answering, the sitting shows the same "Start it, then Check
  again" sentence and waits.
* **On Robert's own machine** most of this is not asked, which is the
  point of requirement 7. Not asked: 1 (a clone), 2 (the target already
  holds the library), 3, 4 (token found in the environment, with the note
  about `.env`), 6 (both keys found), 7-9 (Ollama ready, the default model
  pulled). Not asked if the row is there, as the readers reported: 5.
  Asked once, each an Enter: 10, 12 (unless a watch folder exists), 13,
  14, 15, 16; 11 depends on TASK-089.16. `ollama list` and `GET /api/version`
  must be identical before and after.

Deliberately not asked, and written down so nobody adds them later (U9):
the transcription language (the default was `nl` until 2026-09-02 and was
removed on evidence: "a language code is a bias, not a constraint",
`scribe/web/transcribe_dialog.py:63-69`); the timezone; the UI language
(there is no i18n layer to configure); and GPU versus CPU (ADR-012: one
lock with a per-platform torch source; the honest move is the sentence
"transcription on cpu, Maximum is several times slower here"). A proxy is
detected and shown, not asked (§3.2). Where an Ollama that is already there
keeps its models is its own configuration and is never asked; a new one gets
row 8a, and only when it must (U7, G8). Declined by Robert (U5): a start
script or shortcut for a clone. `python install.py --start` and the
existing `scripts/start.*` are what a clone has.

## 2. Architecture

```
  release                      clone                    headless (POSIX, a TTY)
  frozen launcher (Tk)         install.py               launcher --headless
  stdlib only, ADR-011         stdlib only              stdlib only
        │                            │                        │
        │  [location, free space]    │ [free space]           │ [location, free space]
        │  pinned tools              │ pinned tools           │ pinned tools
        │  uv sync --frozen          │ uv sync --frozen       │ uv sync --frozen
        ▼                            ▼                        ▼
  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ the ADR-011 boundary ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
        │ argv without secrets       │ the terminal itself    │ the terminal itself
        │ stdin: one JSON document   │                        │
        ▼                            ▼                        ▼
               python -m scribe.setup      (the app's environment)
                 --plan         JSON out: found[], ollama, libraries[],
                                downloads[], questions[] - never a value
                 --apply-stdin  JSON in: answers; JSON lines out: progress,
                                result {wrote, reopen, relocate}
                 --prove        the report; exit 0 only when every required
                                line is ok
                 (bare, a TTY)  the console asker over the same questions
                     │
                     ├─ scribe.credentials   where each credential was found
                     ├─ scribe.ollama_setup  state; install only when absent
                     ├─ scribe.models        what this platform and tier load
                     ├─ scribe.autostart, watching, settings rows
                     └─ scribe.doctor        read-only, and a scratch serve check
```

**One engine.** `python -m scribe.setup` detects, decides which questions
are open, validates, writes and proves. Front-ends render and never
decide: the only condition a front-end interprets is `shown_if` ("show
question X when answer Y equals Z"), which is what reveals the key field
under a cloud provider and the model choice under "install Ollama: yes".
The launcher and the app ship in one payload, so the contract carries a
number and needs no compatibility window.

**Three doors.** The frozen launcher draws one Tk dialog from `--plan`.
`install.py` hands the terminal to the engine's own console asker. The
headless launcher does the same when stdin is a terminal, and otherwise
prints one line: nothing was asked, and how to ask. The Windows binary is
frozen `--windowed` (`packaging/build_release.py:81`) and has no console;
the synthesis holds that Tk is always there on Windows (not verified
here).

**The boundary, and exactly what crosses it.** ADR-011's Must is "Keep the
launcher stdlib-only: it must work before the environment exists", and
the launcher's own docstring adds that it "cannot write a setting row
or parse `.env` for itself, and should not learn how" (`:469-472`). That
does not move. Downward: a command line that never carries a secret; the
environment `app_environment` already builds; and, for `--apply-stdin`
only, one JSON document on the child's stdin - the one place a typed
secret travels. Upward: one JSON document from `--plan`, JSON lines from
`--apply-stdin`, the report from `--prove`, and the exit code. Read as
data, never imported: the app's version (as today, `:119-126`), the
contract number, the footprint numbers and the stamp's contract field.
The launcher never sees a found credential - only the name of its source -
never logs the stdin document, and never decides whether a question is
open. One question is the launcher's own because the engine does not
exist yet when it must be asked: the location.

**The location, before the sync (M2, M3, U1, U6; TASK-089.14).** `env/`,
`python/`, `cache/`, `data/` and the models all hang off the home, so where
they go can only be asked before anything is downloaded. One question,
with the free space per volume (`shutil.disk_usage`), the total this
install will download and the disk it needs. The total is shown with its
parts: the environment, the speech weights, and "up to X more if you say
yes to Ollama" - the installer and the default model. "Needed" is the
unpacked footprint plus the floor the app already enforces: work is
refused below 10 GB free where the data lands (`DISK_FLOOR_GB`,
`scribe/doctor.py:42-44`; `tests/conftest.py:15-28` describes the url
stage refusing below it), so an install that only just fits leaves a
machine that refuses its first URL import. The numbers are read as data
from the payload: a new `scribe/footprint.json`, per-platform estimates
with the date they were measured, plus the byte counts already in
`models.json` and the Ollama pin. The floor is read as data too - copied
into `footprint.json` with a test that it equals `doctor.DISK_FLOOR_GB`, or
read as text the way `app_version` reads the version (`:119-126`); which
of the two is TASK-089.14's to settle. **Settled (TASK-089.14, 2026-09-22):
read as text**, `^DISK_FLOOR_GB\s*=\s*(\d+)$` out of the `doctor.py` that
ships, so the number stays in the file whose change should move it - the
reason TASK-089.11 gives for the contract number. A copy in
`footprint.json` would be a second number waiting to drift, which is what
TASK-043 already refused once. `footprint.json` holds what the doctor does
not know: the environment per platform, `null` where nobody has measured
it, and Ollama's own published sizes until TASK-089.18's pin file lands.

The answer is persisted in a small pointer file *next to* the default
home (`MyScribe.location` beside `%LOCALAPPDATA%\MyScribe`, and the same
beside the macOS and XDG defaults), holding one JSON object: `{"home":
"D:\\MyScribe"}`. This spec proposes a second key, `"data"`, which the
library paragraph below explains and TASK-089.19 settles before it is built;
either key may be absent: no `"home"` means
the default home, a pointer with `"data"` alone is what adoption writes
after this question was skipped, and unknown keys are ignored. A file
that is not a JSON object is treated like a pointer to a missing folder:
a sentence and a stop, never the default. `home_dir()` reads the pointer
after `MYSCRIBE_HOME` and before the per-OS default; `--home` outranks all
three in `main()` (`:709`). It is a file read with `json`, so it stays
inside ADR-011. `home_dir()` stays pure - it returns the path and which
source supplied it, and refuses nothing - because `build_parser()` calls
it for the `--home` help text (`:693`) before any argument is read: a
refusal inside it would also stop `--version`, `--smoke` and `--home X`.
The checks live in `main()`. Two existing tests call `home_dir("darwin",
{})` and `home_dir("linux", {})` against the real `Path.home()`
(`tests/test_launcher.py:57`, `:59`) and would read a developer's real
pointer, so the pointer's path is a parameter and §3.13 isolates it.
**Built (TASK-089.14, 2026-09-22):** a parameter was not enough on its own.
`build_parser()` calls `home_dir()` with no arguments at all for the
`--home` help text, so the default comes from a module-level
`pointer_path(platform, environ)` - `MyScribe.location` beside the default
home - and that is the name the autouse fixture in `tests/test_launcher.py`
replaces. `home_dir()` still returns a plain `Path`; which of the four
sources supplied it is `home_source()`, which `--setup` prints.

Refused with a sentence: a path inside the install directory (ADR-011's
Must Not), a relative path, a folder that cannot be written,
and a UNC path. The library is SQLite in WAL mode (ADR-013), and SQLite's
own documentation says "WAL does not work over a network filesystem"
(sqlite.org/wal.html, read 2026-09-20). A mapped drive letter hides the
same problem and is not detected; the question's text says so. The same
four refusals apply to `"data"`: ADR-011's Must Not names the data
directory as well as the environment. A pointer that names a folder which
is not there - an unplugged drive - stops the launcher with that sentence
and two ways on: Quit and plug it in, or answer the location question
again. It never falls back to the default home by itself: a second, empty
library is the worst way to find out. Both doors check free space before
the sync and refuse with both numbers when it cannot fit; a grep for
`disk_usage` finds it in the doctor and Settings only, never in the
launcher. Skipping keeps today's location and writes no pointer.

**The gate, and the stamp it reads (M4; TASK-089.11).** Reading the gate has to be
cheap: a 4.6 s `--plan` on every start is not acceptable. So the gate
reads two small JSON files and imports nothing. `scribe/setup_contract.json`
ships in the payload and holds one number, raised whenever a question is
added. (A constant read as text, the way `app_version` reads the version
at `:119-126`, would do as well; which of the two is TASK-089.11's to settle.)
The stamp gains the same number:

```json
{"contract": 2, "ended": 1789930000.0,
 "questions": {"hf_token": "skipped", "provider": "answered",
               "llm_key": "not_needed", "ollama_install": "answered"}}
```

The first-run sitting appears when the stamp is missing, unreadable, or
carries a lower contract number than the payload. Then `--plan
--unasked-only` lists the questions that are open on this machine *and* were
never put to this user. When that list is empty no dialog is shown, and the
door sends an empty document to `--apply-stdin` (`{"contract": 2, "answers":
{}}`), which stamps the new number and carries the known states over: one
slow start per upgrade, not one per day. `--plan` never writes, with or
without the flag; the stamp keeps one writer, as today
(`scribe/setup.py:142-154`). An explicit `--setup`, the Setup button and a
bare `python -m scribe.setup` run `--plan` without the flag and list
everything open, skipped ones included - which is how a skipped question
stays reachable without nagging. Today's stamp has no number and no ids; it
is read as contract 1: a non-empty `provider` means question 5 was answered,
a non-empty `tier` 10, `hf_token: true` 4, a non-empty `fetched` 11, and
everything else was never asked. So a 0.5.x user who pressed Save on the
preselected `ollama` radio without having Ollama - the group this work is
for - gets one sitting with the Ollama and key questions and nothing they
already answered. A stamp written by the bare-run bug holds four empty
values and therefore counts nothing as answered. The stamp holds states and
source names, never a value. `install.py` does not gate: it is an explicit
command and always plans. It plans with `--unasked-only` when a stamp
exists, so a re-run after every `git pull` does not put skipped questions
again (TASK-089.17); a bare `python -m scribe.setup` is the clone's `--setup`
and lists them.

The stamp is written in one piece - a temp file, then `os.replace` - and
only when a sitting ends. One thing is written mid-sitting, the Ollama
marker of §3.3, and it therefore lives in a small file of its own beside
the stamp (`ollama_setup.json`), not inside it. Power lost after the
marker was written and before the sitting ended leaves a marker and no
stamp, and the gate fires again, which is what §5 promises. A stamp that
carries a `contract` and no `ended` cannot come from this writer and is
treated as missing. Both cases are in the gate's test.

**The library question is asked alone and first (M5, U2; TASK-089.19).** Every
other answer is written into the library it picks, and what is open
depends on that library's settings rows. So when a library is found
elsewhere, step one asks only that; on "use it" the door runs `--plan`
again with `SCRIBE_DATA_DIR` pointing there, and step two is the normal
sitting. With nothing found - most first-time users - there is one step,
and in a release the question is its first item.

Detection has a blind side, and it is the commonest real case. The
candidates are `<repo>/data`, the default home, and a `SCRIBE_DATA_DIR`
found in any environment layer. In a release `env.REPO_DIR` is the
payload's `app/` (`scribe/env.py:24`), so a clone's library elsewhere on
the disk is never a candidate - and that is Robert's own machine:
measured 2026-09-20, `SCRIBE_DATA_DIR` is in no layer and the library sits
in `<repo>/data`. `.env` cannot carry it either, because the launcher
forces the variable (`:203`). So a first release sitting always carries
the question, found or not, with "start new" preselected and "I already
have a library: name its folder" as a choice; naming a folder makes it
step one of two, as above. `--setup` lists the same entry for as long as
the target library holds no recordings. After that, changing libraries is
a hand edit of the pointer file and `--setup` says so: two libraries are
never merged. A named folder gets the location question's refusals and
must hold a `myscribe.db`.

`--plan` opens any library read-only and never migrates it: today `main`
runs `ensure_dirs`, `connect` and `migrate` before anything else, even for
`--status` (`scribe/setup.py:171-174`). On the main path there is no library
yet: `prepare_home` creates `data/` and no database (launcher `:258-260`),
and a read-only open of a missing file is an error (§0's table). So for
`--plan` a missing database is "no rows": every question is open, and no
file and no directory is created. `--apply-stdin` is what creates and
migrates it. Read-only has to cover the directory as well as the file: a
plain `mode=ro` open of a cleanly closed WAL database leaves a `-shm` and a
`-wal` file beside it (§0's table). So the engine opens with
`mode=ro&immutable=1` when no `-wal` file stands beside the database, and
with plain `mode=ro` when one does - an app has it open, and the pair is
already there. `immutable=1` does not look at a WAL, so rows not yet
checkpointed would be invisible; with no `-wal` file there are none. That
last sentence is a reading of SQLite's documentation and was not tested.
Never through `db.connect`, which switches the file to WAL and, without a
path, renames a legacy database (`scribe/db.py:540`, `:544`).
`--apply-stdin` migrates an adopted library only after the explicit yes, and
refuses while an app is serving it (§3.9). In a release the launcher cannot
honour the choice through `.env`, because it forces `SCRIBE_DATA_DIR`
(`:203`); so the engine's result carries `relocate: {"data_dir": ...}` and
the launcher writes it into its own pointer file as `"data"`, which
`Layout.data_dir` then prefers. The pointer stays the one file a front-end
writes, but it then holds two facts, and the engine decided the second. That
is this spec's proposal and not a settled design: how a release points at an
adopted library is TASK-089.19's to settle before it is built (its criterion
8, and §8). ADR-015 fixes only the principle: a small pointer file that the
launcher can read before anything else exists. In a clone the engine writes
`SCRIBE_DATA_DIR` into `.env`. R1 is what makes adoption safe: an adopted
library with no provider chosen queues nothing at its first start.

**`install.py` shares the launcher's sequence, not its code (M11, G2).**
Decided by Robert on 2026-09-20, on a count made that day: about 100 of the
launcher's 750-odd lines are plumbing both doors need - the lock digest,
the stamp, the sync decision, running a child, the setup call. The home,
the environment and the tools differ per door, and the clone's differences
(the dev group, `.venv`, no cache override, no forced data directory) are
easier to review in `install.py`'s own short sequence than as branches in
`sync_environment` and `app_environment` (`:187-213`), the 27 lines that
decide where 3 GB lands and which library opens. So the launcher that
shipped in v0.5.0 and v0.5.1 is not rebuilt around a shared bootstrap and
gains no branch only a clone reaches. Against drift in the duplicated
plumbing, one contract test gives both doors the same lock and stamp and
demands the same sync decision. The part where drift would hurt most is
not in the launcher at all: it copies its tools out of the payload, and
fetching them and checking their sha256 is `fetch` and `extract` in
`packaging/build_payload.py`, which `install.py` shares. A shared module
under `packaging/launcher/` was weighed and set aside: it rebuilds shipped
code whose frozen path only the release smoke and a person at the screen
exercise.

## 3. Components

Tasks are named by key; the backlog holds them. Two pieces of work sit
inside a task whose title does not name them, and are said here so nobody
looks for a task that is not there: the `/health` fields of §3.9 belong to
TASK-089.17 (W2, G5), and the path an autostart entry names (§3.10) belongs
to TASK-089.21.

### 3.1 `scribe/setup.py` (TASK-089.09)

* `.env` takes effect before `DATA_DIR` is fixed, as it does for
  `scribe/__main__.py`, by the mechanism §3.4 names (TASK-089.03).
* `plan(conn, *, unasked_only=False) -> dict` and `--plan`: `contract`,
  `found[]` (`name`, `found`, `source`, `also_in`, `conflict` - never a
  value), `ollama` (state, version, binary path, chat model names, how many
  were ignored), `libraries[]` (`path`, `recordings`), `downloads[]`
  (`repo`, `bytes`, `gated`, `here`, `loads_here`) and `questions[]`. Each
  question carries `id`, `kind`, `text`, `choices`, `current`, `default`,
  `shown_if`, `if_skipped` and `answer_later`. A question is present only
  when it is open. The key question, the found-table and the Ollama
  detection do **not** wait for the models re-pin (M9): until TASK-089.16 lands,
  `downloads` reports today's catalogue with `loads_here` false for the
  MLX entry off Apple Silicon, the download question covers only what
  loads here, and one line says the Whisper weights for this platform are
  not pinned yet and arrive at the first transcription. So TASK-089.09 no
  longer depends on TASK-089.16.
* `--apply-stdin` reads one document: `{"contract": 2, "answers": {id: value
  | null}}`. `null` is a deliberate skip and is recorded; an absent id was
  not shown. Typed secrets go to the settings rows only (`hf_token`,
  `llm_key_<provider>`): the row always wins (`diarize.py:265-271`,
  `base.py:252-258`), Settings' Clear undoes it, and a key typed into `.env`
  would lose to a machine-wide variable of the same name
  (`scribe/env.py:7-9`). Today's `apply` writes the token to `.env` as well
  (`:114-117`); that stops, and it reverses more than a line of code,
  knowingly: acceptance criterion #2 of the Done task TASK-040.06 ("writes
  it to the per-user .env"), the reason given in the launcher's `begin()`
  docstring (`:652-654`: a token given there "is in `.env` before anything
  reads it"), and two tests that pin the `.env` write
  (`tests/test_setup.py:31`, `:43`). It is safe because the diarize stage
  reads `hf_token(ctx.conn)` (`scribe/stages/diarize.py:625`) and the row is
  the first place that lookup reads: a runner child needs nothing from the
  environment. A typed Hugging Face token is checked with the doctor's one
  HEAD before it is saved; a typed OpenAI key with the authenticated model
  list; a typed OpenRouter key with an authenticated request to
  `https://openrouter.ai/api/v1/key`, which costs nothing and answers 401
  for a bad key (the agent's finding in the grill, from the documentation
  and a probe that sent no key; a run with a real key is still owed).
  The result line carries `wrote`, `reopen` and `relocate`; exit codes 3, 2
  and 1 keep their meaning (`:198`). No secret flag exists, and nothing
  MyScribe builds carries a secret on a command line: the launcher stops
  passing `--hf-token` (`:475-476`; `tests/test_launcher.py:404` pins that
  it does, and is revised). The flag itself goes now, with a sentence and
  not an argparse error (G4, decided by Robert on 2026-09-20): it is still
  recognised and is refused, saying that a token on a command line can be
  read by other processes and ends up in shell history, and that it belongs
  in `HF_TOKEN` - in the environment or `.env`, where detection finds it -
  or in the document piped to `--apply-stdin`; exit 2, which `mismatch`
  also uses (`:198`). It is argparse's own usage-error code, the refusal
  comes before any work starts, and the launcher never meets it, because
  it no longer passes the flag. `README.md:224`, which documents the flag,
  is corrected. `--provider` gets `choices`.
* **Setup never writes `default_diarize` by itself (W6); the explicit flag
  stays (G3).** The synthesis had setup switch "Recognise speakers" off when
  no token was found, mark that it had, and restore it when a token was
  saved later. That rested on a wrong model of the row: `default_diarize`
  is "the last options submitted", rewritten by `save_defaults` from seven
  call sites on every upload (`ingest_ui.py:375`, `:438`, `:509`, `:888`,
  `transcribe_dialog.py:247`, `:280`, `settings.py:492`), so a provenance
  marker goes stale at the first upload and the restore could flip a choice
  somebody made on purpose. Watch folders carry their own options and never
  read the row. The behaviour is carried by TASK-089.08 alone, whose note links
  to the token field in Settings. Overturn this by restoring only while the
  row still equals what setup wrote - and accept that five enqueue paths
  bypass it anyway. Two things follow that the synthesis did not account
  for. The tier is saved today through `transcribe_dialog.save_defaults`,
  which writes language, tier and diarize in one `executemany`
  (`scribe/web/transcribe_dialog.py:97-108`; `scribe/setup.py:125-136`), so
  answering question 10 would write a diarize row nobody answered. The
  engine writes the `default_tier` row alone, and a new red-first test pins
  it: a tier answer only, and the diarize row absent before and after.
  `tests/test_setup.py:55-60` passes an explicit `diarize=False` and stays
  as the explicit-choice test (G3). And setup
  already holds an explicit diarize answer: `Answers.diarize` and
  `--diarize/--no-diarize` (`scribe/setup.py:51`, `:164-165`; a README row,
  `README.md:224`). It stays, as an explicit choice and nothing else -
  decided by Robert on 2026-09-20, after this spec had removed it by reading
  "does not write the row at all" literally, which nobody had asked for.
  Somebody who types `--no-diarize` is choosing, not guarding, and the flag
  shipped in v0.5.0 and v0.5.1. So the test for "setup does not write the
  row" covers every path except that flag. No question asks it and the Tk
  form never sets it, so the launcher's pass-through (`:481-482`,
  `tests/test_launcher.py:407`) goes when its answers move to stdin (§3.11).
* Run bare on a terminal it is the console asker over the same questions:
  Enter takes the shown default, `s` skips, secrets go through `getpass`.
  Without a terminal it never calls `getpass`, prints the found-table and
  what is open, exits 0 and writes no stamp - today it prints "saved:
  nothing" and stamps.
* The stamp is written when a sitting ends, a failed or skipped download
  included, in the shape §2 gives (TASK-089.11). Progress is one JSON line per
  whole percent when stdout is a pipe and a bar with speed and ETA on a
  terminal; the engine redacts its own output, because the launcher cannot
  import the redactor and only tees what it is handed.
* `--prove` is §7 (TASK-089.13). `write_token` becomes `write_env(name, value)`
  (TASK-089.03): utf-8-sig, `NAME = value` with spaces matched, never two lines
  for one name, written through a temp file and `os.replace`, created 0600
  on POSIX. The "four answers" docstring, `CHANGELOG.md:107-110` and the
  tests that state that number are revised knowingly, beside the ones named
  above.

### 3.2 `scribe/credentials.py`, a new file (TASK-089.04); the proxy (TASK-089.05)

One resolver, imported by `base.api_key`, `diarize.hf_token`,
`models.default_token`, `doctor.check_diarization` and
`settings.hf_token_context`; it imports nothing from `scribe.llm` or
`scribe.stages`. Per credential: the settings row; then, per variable name
in the provider's order, the process environment (snapshotted before
`.env` is applied), the `.env` file (parsed, not applied, the source naming
the path) and the Windows registry with the hive named. For Hugging Face
only, after those: `HUGGING_FACE_HUB_TOKEN` through the same three, then
the login file at `huggingface_hub.constants.HF_TOKEN_PATH`, read in
place. `resolve()` and `find_all(conn)` return `found`, `source`,
`also_in` and `conflict`, never a value in any repr. Blank or whitespace is
missing in every source; an unreadable login file reports "not readable".
A found credential is used where it is and never copied: a login-file
token can rotate, and a copy in `.env` or in a row would go stale and
outrank it.
`api_key`'s source strings and tests stay as they are.

A proxy is detection, not a question (M6, U8; TASK-089.05): `HTTP(S)_PROXY`,
`ALL_PROXY` and what `urllib.request.getproxies()` reports for the system
appear in the found-table as host and port, with any `user:password@`
stripped - that is a secret too. Every loopback probe bypasses it:
`trust_env=False` on the Ollama client (`scribe/llm/ollama.py:170`),
`ProxyHandler({})` on the launcher's and `install.py`'s `/health` probes
(`:309`). The one measurement is the last row of §0's table; it is one
machine and one OS, and a test that pins it is a criterion.

### 3.3 `scribe/ollama_setup.py`, `scribe/ollama_release.json`, both new (TASK-089.06, TASK-089.18)

TASK-089.06 is the detection - `state()` and the chat-model test. TASK-089.18 is
everything that installs: the plan, the pin and its owner, the marker, the
model offer and row 8a.

* `state()` returns `absent`, `installed_not_running`,
  `running_no_chat_model`, `ready` or `unknown`, with the binary path, the
  version and the chat model names. It reuses `OllamaProvider`'s `GET
  /api/tags`, which now returns the payload. *Absent* needs all four: no
  `ollama` through `shutil.which`, none in the known install locations, no
  answer at 127.0.0.1:11434, and no `OLLAMA_*` variable in the process,
  `.env` or the registry. Any doubt is "present". Chat-capable means
  `completion` is in `capabilities`; with the key missing it falls back to
  `POST /api/show`, then to `unknown` - never to `ready`, never to
  `absent`. `OllamaProvider(conn)` reads `llm_model_ollama`, and Settings'
  model refresh stores chat-capable models only.
* `install_plan(platform)` is data to *show*: the artifact URL, size and
  sha256, the command line, where it installs. `install()` runs only what
  was shown (§6). The pin lives under `scribe/` because the payload ships
  `scribe/` and not `packaging/` (`packaging/build_payload.py:38`).
* After its own install it polls `GET /api/version` for up to 120 s - a
  timeout reads "installed, not answering yet", not failure - then pulls
  over `POST /api/pull`, checking every NDJSON line for `error` and
  requiring a final `success`. MyScribe starts no Ollama, not even the one
  it has just installed: if the installer does not start the daemon -
  unverified for `/VERYSILENT` - the poll times out and the sentence is
  "Start Ollama, then Check again". On success it writes
  `llm_model_ollama=<tag>`, and `llm_provider=ollama` when the sitting's
  provider answer was Ollama or undecided and no row exists. For "undecided"
  that is a choice: question 7's text says a yes makes this Ollama the
  provider, which keeps the row an explicit answer (requirement 2), and the
  row names a local provider, so what R1 protects is not touched. The
  reason: somebody who agreed to about 5 GB of downloads so that questions
  get answered should not then meet "choose a provider" - that is a separate
  thing afterwards (requirement 5). Overturn it by writing
  `llm_model_ollama` only and leaving question 5 open.
* **The marker (M1, G6, G7).** `ollama_installed_by_setup` is written only
  *after* the installer finished successfully, into a file of its own beside
  the stamp (§2, the gate: the stamp is written when a sitting ends, and
  this is written in the middle of one). The installer is never offered
  while any Ollama binary exists, marker or not. The marker is cleared the
  first time Ollama is seen `ready`. The one thing it unlocks is finishing
  this install's own work: the model pull into an Ollama that MyScribe
  installed and that has never yet been ready - the one exception §1 names
  to "never pulled into". After the install's own sitting the pull is
  offered only in a sitting opened with `--setup`, as a question whose
  default is No (G6): Robert chose that on 2026-09-20 over the narrower rule
  the agent recommended - the pull in the install's sitting or not at all,
  and no marker. It unlocks no start: with the marker standing and Ollama
  `installed_not_running`, the sitting shows "Start it, then Check again"
  and the pull is offered once it answers. The marker records the version
  and the path MyScribe installed and counts only while that version still
  stands at that path; any doubt drops it, and that Ollama is then left
  alone with the copyable pull command (G7). It fails to the safe side:
  Ollama updates itself, the marker lapses, and somebody types one command;
  the path alone could not tell MyScribe's Ollama from one installed later
  at the same default path. A failed or cancelled download leaves no
  marker and no variable - row 8a's `OLLAMA_MODELS` is written only once
  the artifact has passed its checks - so the state is simply *absent*
  again. An installer that then fails would leave a variable MyScribe set
  itself, which `state()` reads as present, and row 7's offer would never
  return. Proposed, and TASK-089.18's to settle and test:
  `ollama_setup.json` records the value first, under a key that is not the
  marker; a failure removes the variable while it still holds that value;
  and `state()` disregards one equal to the record. The synthesis wrote the
  marker *before* the installer ran, which could bring the installer back
  over an Ollama the user had installed since - the one thing requirement 9
  forbids.
* **The model offer states its unit (W4).** `qwen3.5:4b` is the default,
  on the repository's one measurement: the 9B and the 12B both failed to
  start "while 12.7 GB of the 16 GB card was in use"
  (`scribe/llm/ollama.py:221-224`). `gemma4:12b` is listed only at 22 GiB
  (23,622,320,128 bytes) of CUDA memory or more, compared in bytes against
  the reported total, and not at all on Apple Silicon until somebody has
  measured it on a real Mac. Robert's "16 GB" card reports 17,179,344,896
  bytes, 15.9995 GiB, so a threshold *at* 16 would flip on the unit, and 20
  would do the same one class up if a 20 GB card reports the same way -
  nobody has measured one. 22 sits at no card's nominal size: a 16 GB and a
  20 GB card fail and a 24 GB card passes, whether the number is later read
  as GiB or as decimal GB (TASK-089.18). It is an estimate
  modelled on one measured card, and the sitting and the code label it so.
  The sizes shown are the ones this machine's Ollama reports: 3,389,983,735
  bytes for `qwen3.5:4b`, 7,556,508,396 for
  `gemma4:12b`. Tags are always explicit: a reader reported that untagged
  `gemma4` is a different, 9.6 GB model (not checked here).
  A new `accel.memory()` supplies the number (TASK-089.18).
* `.env.example` stops advertising `OLLAMA_HOST` (`:26`), which nothing
  reads.

### 3.4 `scribe/env.py` (TASK-089.03)

A blank or whitespace-only process variable no longer shadows a non-blank
value in the file; a non-blank process value still wins. A blank value in
the file is not exported at all. `load_dotenv` records which names it
applied, so the resolver can tell `.env` from the environment.
`bootstrap()` is `load_dotenv` plus `<repo>/.tools/bin` first on `PATH`
when it exists. `scribe/__main__.py` calls it before it imports the app,
which it can do because that import sits inside `main()` (`:82-86`).

Setup, models and the doctor cannot get there by adding a call. All three
import `scribe.paths` at module level (`setup.py:34`, `models.py:37`,
`doctor.py:36`), and under `python -m` a module's top-level imports run
before its `main()`. A call at module top is no answer either: the doctor
and models are also libraries (`scribe/runner.py:23` imports the doctor;
setup imports models), and they would then load `.env` as a side effect
of being imported. Moving the imports inside `main()` does not work for a
module whose other functions use them. Two mechanisms remain, neither
tried, and TASK-089.03 picks one:

* `scribe.paths` learns to recompute: a function that sets `DATA_DIR` and
  the constants derived from it again from the environment, called by the
  three `main()`s right after `env.bootstrap()`. It keeps every command
  name. It only works while no module copies a `paths` constant at import;
  a grep on 2026-09-20 for `from scribe.paths import` and for a
  module-level `NAME = paths.NAME` under `scribe/` found neither, and a
  default argument bound at import was not searched for.
* A thin entry per command, which bootstraps and only then imports the
  module. `python -m scribe.doctor` runs `doctor.py` itself, so this means
  new entry points, and the README, CLAUDE.md and the launcher's
  `--doctor` all name the old ones.

This spec leans to the first. Either way library importers are
unaffected - importing `scribe.doctor` still loads no file - and the
criterion is one red-first test per command: `SCRIBE_DATA_DIR` set only in
`.env`, and the command reports that directory.

### 3.5 No row, no provider: `scribe/llm/__init__.py` and its callers (TASK-089.07, R1, ADR-016)

`default_provider(conn)` returns no provider when the row is missing - and
when it names a provider that is no longer registered, which today also
falls through to OpenRouter (`:167-170`). The eight call sites handle
that: the AI panel and the Settings line say "choose a provider", and the
library's bulk action queues nothing and says why. The three fallbacks in
`llm_stage.py` (`:94`, `:271`, `:345`) fail a job that names no provider
with that sentence instead of sending it to OpenRouter. In
`finalize.py`, `queue_speaker_pass` queues nothing, and
**`sweep_speaker_passes` queues nothing and marks nothing as asked** (W3),
so the catch-up happens at the first start after somebody has chosen. That
sweep is the path that matters: it runs at every start over the whole back
catalogue with no ceiling (`scribe/app.py:261`), which is exactly what an
adopted library with a machine-wide OpenRouter key would have met. This is
a privacy fix (§6), it reverses a recorded decision, and the 2026-09-01
spec (`:219`) and the CHANGELOG each get a line saying so.

`ai_run` gains one more pre-enqueue check, in a task of its own (TASK-089.10,
which follows TASK-089.07 and TASK-089.06): a provider is chosen and can answer - a
key is present, or the loopback probe finds the *saved* model pulled. No
remote call is made (ADR-001). When it cannot, the panel comes back with a
card naming the fix and no job row is created. The automatic pass in
`finalize` skips with a run note in the same case.

### 3.6 `scribe/stages/diarize.py` (TASK-089.08)

The stage catches `WeightsUnavailable` and only that: empty turns, a note
on the run in words ("speakers skipped" and the three routes to fix it,
the first being the token field in Settings), an event. The job ends
`done` with a transcript and no speakers. Any other exception still fails
the job. This is what makes question 4 skippable: an install-time guard
cannot cover the watch folder, the feeds, the link door, the recorder and
retry, and the stage covers all of them. The README and `.env.example`
stop claiming a fallback that works without a token.

### 3.7 `scribe/models.py`, `models.json`, the two loaders (TASK-089.16)

Catalogue entries carry `platforms` and `tier`; pins are added for the CT2
turbo repo under the id faster-whisper requests, for
`Systran/faster-whisper-large-v3` and for the MLX large-v3. **No pin was
collected in the design run.** `status()`, `present()` and `ensure()`
agree that a hub-cache snapshot at the pinned revision is present.
`ensure()` fetches gated entries first, reports a short read as `offline`
and not `mismatch`, resumes a `.part` with `Range`, and checks free space
on the volume the files land on before the first byte. 401 and 403 get
different sentences here and in the doctor. The Bearer header goes to
gated repos only - today any token is sent to every repo (`:206`) - and is
dropped on a cross-host redirect. `transcribe.load_model` and the MLX
backend load the pinned local folder when it is complete and behave as
today otherwise: `WhisperModel` takes a directory
(`faster_whisper/transcribe.py:678`); whether `mlx-whisper` does is
verified by nobody.

### 3.8 `scribe/doctor.py` (TASK-089.12)

`check_diarization` uses the resolver, so it sees the Settings row.

`gpu-runtime` becomes information in two cases only, and ADR-012's Must is
unchanged: "`doctor.check_gpu_runtime` fails when `torch.version.cuda is
None` on a machine that should have CUDA" (ADR-012, Must). "The
accelerator resolves to `cpu`" is not the test, because it is also true
for the two failures the check exists to catch. A CPU-only torch on
Windows or Linux stays FAIL (`:335-340`): that is the wrong-index install
ADR-012 forbids, and only its hint changes - it says `pip install torch`
(`:339`) in an environment that has no pip, and becomes `uv sync`. What is
split is the next branch, "cannot reach a device" (`:341-347`), which
today is a required FAIL on any Windows or Linux machine without NVIDIA.
It reads as information - "no NVIDIA GPU on this machine; transcription on
cpu" - only when there is evidence of no NVIDIA hardware: no `nvidia-smi`
on `PATH` or in its known locations, and no NVIDIA display adapter reported
by the OS. With either of those present it stays FAIL with the driver hint:
an NVIDIA machine with a broken driver is what that branch was written for.
`CUDA_VISIBLE_DEVICES` does not soften it by itself: empty or `-1` on a
machine with a card looks exactly like a broken driver, so that case stays
FAIL and the detail names the variable - on Robert's machine
`CUDA_VISIBLE_DEVICES=-1 python -m scribe.doctor` still exits 1. The
variable cannot be TASK-089.12's red-first probe either: the test replaces the
function that says whether NVIDIA hardware is present. Any doubt is FAIL.
Which signals that function reads per OS is unverified and part of the
task.

The ffmpeg hint is per OS. `--json` prints one object per check. An
optional `ollama` line reports the state and never fails the gate.

**`--read-only`** (TASK-089.13) is what `--prove` runs. The `database` check
opens the library the way `--plan` does (§2: `immutable=1` or `mode=ro`,
never `db.connect`) and reports the schema version without migrating. A
database that is not there yet reads "no library yet; it is created at
the first start" and is not a FAIL - a fresh clone under `install.py
--check` is exactly that case, where today's check would create and
migrate one (`:267-272`). The GPU smoke skips its `smoke` row. A proof
must not migrate a library under an older running app (W1).

### 3.9 `scribe/app.py`: `/health` says what it serves (W2, G5; TASK-089.17)

The synthesis had `install.py` "refuse to sync while MyScribe answers
`/health` from this checkout". Today that cannot be known: `/health` carries
no path, and the launcher's own single-instance rule is per port and
identity-blind. Decided by Robert on 2026-09-20 (G5): `/health` gains
`app_dir` (the source tree, `env.REPO_DIR`) and `data_dir`
(`paths.DATA_DIR`). That changes `scribe/app.py:296-298` and the one test
that pins the answer's exact shape (`tests/test_app.py:48`); the launcher
reads only `ok` (`:310`) and is unaffected. `install.py` compares `app_dir`
with its own root, and the engine compares `data_dir` with its own, both
after `os.path.normcase(os.path.realpath(...))`. A match means this
checkout, or this library, is being served, and this checkout refuses the
sync; another checkout, or no answer, goes on. A `/health` that answers
without the fields is an older MyScribe: that is doubt, and doubt refuses
the sync ("stop it first, or use `--no-sync`"). It gives nothing away: a
cross-origin page can send the GET but cannot read the answer, the host
check covers the whole app against a rebound hostname
(`scribe/guard.py:110`), and a local process that can read it can read the
file system as well. The blind spot is kept and named: only the default
port and a given `--port` are asked, so an app on a port nobody mentioned
is not seen - this repository's own `--port 4299` is such a case.
TASK-089.19 (the adoption refusal) reads the same fields and follows
TASK-089.17 in the backlog.
TASK-089.13 (the proof, §7) reads them too, is built before TASK-089.17 and
needs no ordering for it: until the fields exist, every `/health` that
answers is doubt, and doubt refuses the adoption and reports "not tested".

### 3.10 `scribe/autostart.py`, a new file (TASK-089.21, TASK-089.22), and the watch-folder check (TASK-089.20) (R3)

* **Start at login** needs its Settings counterpart first (U3,
  `scribe/setup.py:21-23`): TASK-089.21 builds the switch, and TASK-089.22, the
  installer's question, follows it and calls the same function.
  `autostart.status()`, `enable()` and `disable()` write a per-user entry
  and nothing that needs an administrator. Proposed: a value under
  `HKCU\...\Run` on Windows, a LaunchAgent plist on macOS, an XDG autostart
  `.desktop` file on Linux; TASK-089.21 leaves the mechanism open until one has
  been tried. Settings shows the exact entry and a switch. Where there is
  nothing known to start, the question is not asked and Settings says why.
  None of the three entries has been tried on a real login.
* **What a release entry starts** is the launcher, with `--no-browser` -
  not the environment's Python, which would run a stale environment after
  the next update. The launcher hands its own stable path down to the app
  as `MYSCRIBE_LAUNCHER`, and which path is stable differs per OS:
  `sys.executable` on Windows; the `.app` bundle on macOS, not the binary
  inside it; and on Linux the value of `APPIMAGE`, because inside an
  AppImage the running executable sits under a temporary mount that is
  gone after exit, so an entry naming it breaks at the next login. All
  three are unverified, and the AppImage behaviour is general knowledge
  about AppImage that nobody here tested (ADR-011's Decision Outcome says only
  that Linux ships as one). The binary is windowed on every target
  (`packaging/build_release.py:81`), so a login start opens the launcher's
  Tk window; whether it shows or starts minimised is TASK-089.21's to record. A
  login start never opens the sitting. The entry carries a flag of its
  own, `--at-login`, and with it a gate that is due is left for the next
  start somebody makes by hand; the app starts as it does after "Ask me
  next time". Nobody is at the screen, and a modal dialog that holds up
  the watch folders is the opposite of what the entry is for.
* **What a clone entry starts** is the existing `scripts/start.*`,
  detached. That is a choice, not a given. R3 declined "a start script or
  shortcut for a clone", and a login entry for a clone sits close to it.
  The reason to offer it anyway: Robert's own machine is a clone with
  watch folders, which is the case the question exists for. Nothing new
  is created - the entry names a script that exists. Those scripts start
  the app today wherever a `.venv` exists; what is stale in them is the
  hint they print when there is none (`scripts/start.ps1:48-57`; §0, gap
  5), which §3.12 (TASK-089.17) corrects, so TASK-089.21 does not wait for it, and
  the backlog orders it that way. If Robert says no, the
  fallback is: release only; a clone is not asked and Settings says why
  (§8).
* **The watch folder** (U4; TASK-089.20) is written through
  `watching.add_folder` with the current default options, after the same
  four refusals the Settings route applies: three in `parse_watch_path`
  (`scribe/web/settings.py:599-640`), and the fourth, already watched, as
  the UNIQUE column's `IntegrityError` caught in the route (`:664-667`).
  The checks move behind one function both callers use; the route keeps
  turning its refusals into 4xx.

### 3.11 `packaging/launcher/myscribe_launcher.py` (TASK-089.01, TASK-089.14, TASK-089.11, TASK-089.15)

* TASK-089.01 ships first and alone, and lives under the new parent task only
  (W7): the sequencing becomes a plain function that imports no Tk and
  takes `ask` and `report` callables, in the order prepare the home,
  install tools, sync, setup, start. A test drives it on a layout with no
  environment and a fake uv, red on today's code.
* TASK-089.14: `home_dir()` reads the pointer and stays pure, the checks sit in
  `main()`, and the location question and the free-space check come before
  the sync (§2). TASK-089.11: the gate reads the stamp and the contract number.
* TASK-089.15: then `--plan`, one dialog drawn from it (the found block, then
  only the open questions, each with its own Skip and its `if_skipped`
  sentence, radios on `current`), answers through
  `run_streaming(input=...)`, a progress bar from the JSON lines, and exit
  codes 3, 2, 1 or any `reopen` as a visible error state with Retry and
  Continue without. The hard-coded provider tuple and the "1.6 GB" literal
  go. Every reported line is teed to `<home>/logs/launcher.log`. A Setup
  button and `--setup` reopen the sitting; `--headless` with a terminal runs
  the console asker.
* **Quit (M10; TASK-089.15, and TASK-089.18 for the experiment).** The launcher keeps
  the setup child's handle. Quit during MyScribe's own downloads terminates
  *that one process* - `terminate()`, not `taskkill /T`. Under the launcher
  the child has no long-lived children of its own. The downloads and the
  Ollama pull are in-process HTTP. The doctor's checks are called
  in-process, and the version probes they spawn through `_run` end by
  themselves within 15 s (`scribe/doctor.py:116-125`). MyScribe never
  runs brew (§1, line 3 of the table), so neither door has a brew child. The
  scratch serve check of §7 is not run in a release, where the launcher
  starts the app for real. Whether the clip test spawns an ffmpeg of its own
  was not checked. So `terminate()` can orphan at most a short probe, and
  the M10 test says it in those words: Quit during MyScribe's downloads, and
  Quit during `--prove`, each leave no python and no probe process behind
  once 15 s have passed. The one time the child has a long-lived child is
  while the third-party installer runs, and then Quit is refused with a
  sentence saying why (Ctrl-C in the console waits likewise). So no tree
  kill ever passes over a freshly installed Ollama. Whether the app's own
  tree kill on a later Quit (`:376-380`) can reach a daemon the installer
  started is a question about Windows process lineage that nobody has
  tested; it is a criterion of TASK-089.18 to test it before the README says Quit
  is clean.
* `--smoke` applies `{}` over stdin before `/health` and writes
  `<home>/logs/launcher-smoke.log`, which `build_release.py` prints on
  failure: the Windows smoke is mute today because the binary is windowed.

### 3.12 `install.py`, a new file, and `packaging/build_payload.py` (TASK-089.17)

Stdlib only, Python 3.9 syntax. It refuses an older Python and any
platform outside the three `[tool.uv]` environments
(`pyproject.toml:68-71`) in its first second. It fetches the pinned uv
through `build_payload.fetch` and `extract` into `<repo>/.tools/bin` and
never calls the uv on `PATH`; on Windows and Linux the pinned ffmpeg too
when none answers (`packaging/tools.json` has no downloadable macOS
ffmpeg - hence line 3 in §1). It checks free space, refuses to sync over a
running checkout (§3.9), runs `uv sync --frozen` with `UV_NO_CONFIG=1`,
seeds `.env` (0600 on POSIX), writes the lock stamp into `.venv`, hands
the terminal to the engine, runs `--prove`, and starts the app on a yes.
It sets no cache, Python or bytecode directory and exports
`SCRIBE_DATA_DIR` only for `--data-dir`. The clone keeps the dev group;
the release keeps `--no-dev`, which `tests/test_launcher.py:125` pins.
Flags: `--non-interactive`, `--answers FILE`, `--no-dev`, `--no-sync`,
`--data-dir`, `--check`, `--start`, `--port`. `--check` is the proof
report of TASK-089.13, and it no longer claims to touch nothing (W1): it
creates and removes one scratch directory and reads the library
read-only - or says "no library yet" on a fresh clone (§3.8). Git Bash
has no real terminal for a native Windows Python, so there it says so in
one line and asks nothing. `fetch()` writes its cache atomically and
deletes a cached file whose sum is wrong: today a truncated cache file
fails every later run (`:49-58`). `install.py` is not in `APP_PATHS`. The
start scripts compare the lock stamp and say "run python install.py".

### 3.13 `tests/conftest.py` (TASK-089.04, TASK-089.14)

An autouse fixture stubs the registry reader and `HF_TOKEN_PATH` (TASK-089.04).
Without it the "no token" tests turn red on the one machine whose machine
hive holds `HF_TOKEN` - this one, measured above - and stay green in CI.

The pointer file is the same class of problem (TASK-089.14). The launcher's
home tests run against the real `Path.home()`
(`tests/test_launcher.py:57`, `:59`), so once `home_dir()` reads a
pointer they would read the one on a developer's machine - green in CI,
red for whoever moved their home. `home_dir()` takes the pointer's path as
a parameter, and an autouse fixture in the launcher's tests points it at
`tmp_path`.

### 3.14 CI, docs and the ADRs (TASK-089.02, TASK-089.18, TASK-089.24, TASK-089.23)

* TASK-089.24: `ci.yml` installs through `python install.py --non-interactive` on
  all three runners; both workflows use the pinned uv instead of piping
  Astral's install script into a shell, which they do today (`ci.yml:39`,
  `:45`, `release.yml:80`, `:86`). The release smoke walks sync, setup with
  `{}` answers, then `/health`.
* **The Ollama pin has an owner (M7; TASK-089.18)**: `docs/RELEASING.md` gains a
  step - bump `scribe/ollama_release.json`, download both artifacts, hash
  them, compare with the published digests - and CI checks on every run
  that the pinned URLs still resolve and that GitHub's asset size and
  digest still match the pin. CI compares metadata; only the release step
  hashes 1.57 GB.
* **Uninstall, and the texts that name the home (M8; TASK-089.23).** The Inno
  welcome text (`packaging/windows/myscribe.iss:51`) and the README say
  what stays behind. An Ollama and a model that MyScribe installed survive
  an uninstall and belong to the user. So do row 8a's models folder and
  its `OLLAMA_MODELS` user variable, which would steer any Ollama installed
  later: the texts give the command that removes it. So does the pointer
  file, and that one bites: left behind, it sends a reinstall to the old
  folder without a word. The texts name it and say how to remove it.
  `.tools/` in a clone is documented and gitignored. Four shipped texts
  state the fixed home as fact and become wrong the day question 1 is
  answered: the welcome text (`:51`), the `.iss` header (`:5-8`), the
  README's Windows bullet (`README.md:87-89`) and ADR-011's Decision
  Outcome. The
  first three are edited to say that `%LOCALAPPDATA%\MyScribe` is the
  default and that the first start asks. ADR-011 is Accepted and is not
  edited: ADR-015 records that the home can be chosen, as an extension of
  ADR-011. The `.iss` header also still cites ADR-008 for what is now
  ADR-011 (`:1`).
* `README.md`: "From a clone" becomes the two commands; the fallback claim
  is corrected. `CHANGELOG.md`: the four-answers entry, and R1's line.
* The decision records, through `/adr-kit:adr`; the grill split one Proposed
  record in three (G1). ADR-015: the setup contract; its considered options
  include Inno wizard pages, the `/welcome` page, asking before the sync,
  and M11. ADR-016: no provider row means no provider (R1, §3.5), with no
  open question, so it can be accepted before TASK-089.07 builds under it.
  ADR-017: the rule for installing third-party software (§6) and all that
  concerns an Ollama MyScribe installs - the marker and row 8a (§3.3), the
  pin's owner and what stays behind (M7, M8 above), how the setup child is
  stopped (§3.11); delegating "only when missing" to Ollama's scripts is
  its considered option. The
  stdlib-only guard for `install.py` goes into ADR-015's *own* Enforcement
  block: a `forbid_import` rule with `path_glob` `install.py`, and the plain
  statement that the regex is a deny-list - ADR-011's seven module names
  plus the launcher module - so `import requests` would pass it; the real
  stdlib-only proof is the AST allow-list test over
  `install.py`, `build_payload.py` and the launcher (TASK-089.17). The
  synthesis had this as an edit to ADR-011's Enforcement; that was wrong.
  The guide says "Never rewrite an Accepted ADR. Create a Proposed
  successor" (`.adr-kit/ADR-guide.md:52-53`), and this repository did that
  twice to change an Enforcement block: ADR-002 to ADR-009 to ADR-013, and
  ADR-007 to ADR-014.

## 4. Data flow of one first run

A Windows machine with no Ollama, no token and a small C: drive.

1. The Inno installer finishes and offers to start the launcher
   (`myscribe.iss:42`). No pointer file and no environment: the location
   question shows C: with 9 GB free and D: with 400 GB, and "this install
   downloads about T GB: about 3 GB of environment now, W GB of speech
   weights, and up to 5 GB more if you say yes to Ollama (a 1.57 GB
   installer and a 3.4 GB model). It needs about N GB of disk, the 10 GB
   MyScribe keeps free included." T, W and N come from `footprint.json`, and
   those numbers do not exist yet (§8). C: is under the 10 GB floor before
   anything is downloaded, and the line beside it says so. The user picks
   `D:\MyScribe`; the launcher writes `%LOCALAPPDATA%\MyScribe.location` and
   builds its `Layout` on the new home.
2. Tools, then `uv sync --frozen`, with a progress headline. The window
   comes to the front.
3. `python -m scribe.setup --plan --unasked-only`, no secret on the
   command line. There is no library yet: the plan reads that as no rows
   and creates nothing. The plan: nothing found, Ollama `absent`, no
   library elsewhere, questions 2 ("start new" preselected), 4, 5, 7 (8
   behind its `shown_if`, and 8a if C: is short of the chosen model), 10-15.
4. One dialog. The user leaves 2 on "start new", skips the token, leaves
   the provider undecided, says yes to Ollama with `qwen3.5:4b` - having
   read the URL, the 1.57 GB, the sha256, the command line, and that a yes
   makes this Ollama the provider - names a watch folder, and presses Save
   and start.
5. The launcher writes one JSON document to the child's stdin. The engine
   creates and migrates the library - the first write of the run - and
   fetches what loads here, gated first - skipped, no token, said in one
   line. It downloads `OllamaSetup.exe` to a `.part`, verifies the sha256
   and the signer, and runs the command it showed; Quit is refused
   meanwhile. It writes the marker file, polls `/api/version`, pulls the
   model, writes `llm_model_ollama` and `llm_provider=ollama`, and clears
   the marker on `ready`. It adds the watch folder and stamps the sitting
   with `hf_token: skipped`.
6. `--prove` runs the read-only CPU checks, the found-table, the Ollama
   line, the one-word local probe and the clip; the app is not up, so the
   setup child is the only one on the card. The report goes to the window
   and to `D:\MyScribe\logs\launcher.log`.
7. The launcher starts the app for real - that is the serve proof in a
   release. The lifespan's sweep finds nothing. The first recording
   dropped in the watch folder is transcribed and ends `done` with a
   transcript and the note "speakers skipped".

On Robert's machine the same engine under `python install.py` asks no
credential question and no Ollama question (§1), and `--prove` takes
whichever of §7's three branches his app on 4242 puts it in: it loads a
model itself only when nothing answers.

## 5. Error handling

| Situation | Where | What the user sees |
| --- | --- | --- |
| No network during the sync | either door | one sentence, not a traceback; "Retry"; nothing stamped. A found proxy is named in the same sentence. |
| Not enough room for the sync | before the sync | both numbers and the volume; nothing is downloaded |
| No network mid-download of the weights | engine | a short read is `offline`, not `mismatch`; the `.part` stays and the next try resumes with `Range`; the sitting is stamped with the question still open |
| A wrong or revoked Hugging Face token | engine | 401: "token not recognised". Not saved; question 4 reopens. A *found* token that is refused reopens 4 with the note that a typed token goes to Settings, which outranks the refused one |
| A gated model whose conditions were never accepted | engine | 403: "conditions not accepted yet", with the URL and a button that opens it. Not saved; reopens. The gated 33 MB goes first, so this shows in seconds |
| A rejected OpenAI or OpenRouter key | engine | not written; question 6 reopens. Both checks are free (§3.1); OpenRouter's has not yet run with a real key |
| A cloud provider chosen and its key skipped | engine | saved, with the sentence that Summary and Chat show a no-key card until a key exists, and where to add one |
| Ollama absent | sitting | question 7 |
| Ollama absent, and its default volume too small for the model | sitting | question 8a; a No ends the offer with both numbers |
| Ollama installed, stopped | sitting | the sentence and Check again; never started - also not when this install's own marker stands (§3.3) |
| Ollama running, no chat model | sitting | the sentence, the pull command, Check again; never pulled into. The one exception is the marker of §3.3: an Ollama this install put there, still at the version and path it installed and never ready, is offered the pull it was asked for |
| Ollama state unreadable | sitting | "could not tell"; treated as present |
| The Ollama download 404s or fails its sha256 or signer | engine | "get it from ollama.com/download, then Check again". Never an unpinned fallback; no marker |
| The Ollama download is cancelled or the connection drops | engine | the `.part` stays for a resume; no marker, and row 8a's variable was not written yet; the state is *absent* again |
| The Ollama installer itself fails or is killed | engine | its exit code, and "get it from ollama.com/download, then Check again"; no marker. A variable row 8a set would make the state read present, so it is removed again (§3.3, a proposal) |
| Ollama installed but not answering after 120 s | engine | "installed, not answering yet", not failure, with "Start Ollama, then Check again"; MyScribe does not start it. The marker stands, so the pull is offered at the next `--setup` once Ollama answers |
| A pull that answers 200 and then an `error` line | engine | the pull failed, with Ollama's words; the marker stands |
| A full disk while writing | engine | `ENOSPC` reads "disk full" with the volume, not `offline` |
| Quit during MyScribe's downloads | launcher | the setup child is stopped; nothing keeps downloading. During the Ollama installer, Quit is refused with its reason |
| An interrupted install - power, a kill, a closed laptop | next start | no stamp means the gate fires again, and a marker file without a stamp changes nothing about that (§2); the sync stamp decides whether the sync repeats (launcher `:136-144`); `.part` files resume; nothing half-written looks finished (`models.py:200-203`) |
| A second run after a partial first | either door | the plan lists only what is still open; an answered question opens on its stored value and is never reset |
| The pointer names a folder that is not there, or is not readable as a JSON object | launcher | that sentence, and stop: Quit, or answer the location question again. Never a silent new home on C: |
| No library yet - the main path, and a fresh clone | `--plan`, `--check` | read as no rows; the report says "no library yet"; no file and no directory is created |
| A MyScribe answers `/health` and is not provably this library | `--prove` | no model is loaded; "not tested (a MyScribe is running on port N)", never ok |
| A chosen location that is refused | launcher | the reason; the question reopens |
| A watch folder outside the browse roots, inside the data directory, or already watched | engine | the Settings route's own sentence; reopens; nothing is widened (§8) |
| Adopting a library that a running app serves | engine | refused: stop that MyScribe first; nothing migrated |
| An older MyScribe answers `/health` without saying what it serves | `install.py` | doubt refuses the sync: "stop it first, or use `--no-sync`" |
| SmartScreen (the unsigned release) | before the launcher | unchanged: More info, Run anyway (`README.md:87-89`). MyScribe's own code never runs until then. Ollama's own script checks the Authenticode signature of `OllamaSetup.exe` before running it, and so does MyScribe |
| Gatekeeper | before the launcher; at the Ollama dmg | unchanged for the app (`README.md:82-86`). For Ollama the verified dmg is *opened*, the user drags it to Applications and presses Check again. Nothing on macOS is verified |
| No GPU | engine, doctor | `gpu-runtime` is information when no NVIDIA hardware is found; a CPU-only torch, a broken driver and a card hidden with `CUDA_VISIBLE_DEVICES` stay FAIL, the last with the variable named (§3.8, ADR-012); the tier question says Maximum is several times slower here; the clip test runs on the CPU and says it takes minutes |
| No terminal (CI, a pipe, Git Bash on Windows) | `install.py`, headless | one line: nothing was asked, and how to ask; the run continues unattended with every question skipped and unstamped |

## 6. Security

* **A secret is never on a command line, never printed, and never copied
  somewhere less safe.** Typed secrets travel from a masked field or
  `getpass` into one JSON document on the child's stdin and from there into
  a settings row. The launcher does not log that document. `--plan`, the
  stamp, the report and both logs hold source names and booleans. A SENTINEL
  planted in every source must appear in no repr, no JSON, no log and no
  child argv; that test is a criterion of TASK-089.04, TASK-089.09 and TASK-089.15. It
  covers every command line MyScribe builds, and the rule has no exception:
  `--hf-token` is refused with a sentence and exit 2 (§3.1, G4).
  A found credential stays where it was found. A proxy URL's userinfo is
  stripped before it is shown. `--answers FILE` is the user's own file; it
  reaches the engine over stdin.
* **Installing somebody else's software**, the rule ADR-017 records.
  MyScribe may do it only when the software is absent; the exact artifact
  and command line have been shown, and with them anything that outlives
  the sitting (row 8a's variable); the user has given an explicit yes;
  the artifact is pinned and sha256-verified, and on Windows the
  Authenticode signer is checked; a remote script is never piped into a
  shell; and on Linux the commands are shown as download, read, then run,
  and the user runs them, because they need sudo. A present Ollama is
  never installed over, upgraded, started, pulled into or reconfigured.
  Ollama's own instructions are pipes (`curl ... | sh`, `irm ... | iex`;
  README v0.34.2 `:16`, `:24`, `:32`), which is why they are not reused.
  The Windows command is the one Ollama's own script runs
  (`/VERYSILENT /NORESTART /SUPPRESSMSGBOXES`, `install.ps1:271`, which
  also checks the signature), per-user under
  `%LOCALAPPDATA%\Programs\Ollama`. A test asserts that the executed
  command equals the shown command. The pinned digest is GitHub's
  published one, a single source, until the release step hashes the
  download itself.
* **R1 is a privacy fix (ADR-016).** Today a machine with an OpenRouter key
  anywhere sends every transcript that is not pinned private to OpenRouter
  without anyone having chosen it - through the pass `finalize` queues after
  each diarized job and through the startup sweep over the whole back
  catalogue. This machine has that key machine-wide. Once every question
  is skippable, the skip path is the default most people get, and
  `scribe/setup.py:13-14` already argues the conclusion: "sending a private
  recording to a cloud model is not a default anybody should inherit".
* **The private pin is untouched** and still outranks every key: a
  recording or folder pinned private refuses every non-local provider,
  whatever the installer was told. The sitting says so beside each cloud
  choice, and says that pinned recordings cannot use AI at all until a
  local provider exists.
* `--plan` opens a library read-only, never migrates it and leaves no file
  beside it (§2); adoption migrates only after an explicit yes. `.env` is
  created 0600 on POSIX - today it is 0644 under a 0022 umask (a reader's
  WSL measurement) while the README calls it unreadable by other accounts
  (`:161-162`).
* An autostart entry is per-user, names one fixed executable with fixed
  arguments, and is shown in Settings exactly as written.

## 7. Testing

The working agreement applies to every task: the command, the output and
what it proves; a bugfix red first; "the test bites" shown by mutating a
*copy* of the repository. No test reaches the network: the Hub, GitHub and
Ollama are local servers or `MockTransport`, uv is a fake.

**The proof never starts an app on the live library, and reads it read-only
(W1; TASK-089.13).** `--prove` reads the real machine - credentials, Ollama, the
accelerator, ffmpeg, the weights. It opens the library read-only itself and
never migrates it: the doctor runs `--read-only`, and the one step that must
start an app starts it on a fresh temporary `SCRIBE_DATA_DIR`, on a free
port, with `--no-supervisor --no-browser`, and removes the directory
afterwards. Only that serve step runs on scratch. The doctor's part reads
the live library, because the models live under the data directory (§8, the
last judgment call). The only writes a proof can cause are the ones it asks
a running app to make through the app's own queue: two job rows, and then
the `doctor_last` setting and the self-test result those jobs store (next
paragraph; `scribe/web/settings.py:10-13`, `:154-163`,
`scribe/web/ai_ui.py:1100-1109`). The criterion is worded to match. With no
app running, the library's directory holds the same file names and the same
bytes before and after `--prove`. With an app running, the difference is
those rows and nothing else. "The same file names" is why the open is
`immutable=1` when no `-wal` file is there (§2; measured, §0). A copy of
the database is the fallback if that
turns out to misread a library. When `/health` already answers for this
library the line reads "app: already serving, version X" and nothing is
started. The release launcher proves serving by starting the app for real,
once. Evidence runs on Robert's machine go further and point the whole
engine at a scratch directory, never at `<repo>/data`.

**The doctor job when the app is up (W5; TASK-089.13).** ADR-001: GPU work runs
only in a runner child, one at a time (its Must). There are three
branches, and only the last loads a model in the setup child.

* `/health` answers and is provably this library (§3.9: `data_dir`
  matches). `--prove` queues the existing `doctor` job the way Settings
  does (`queue_gpu_checks`) and the provider test the way
  `queue_provider_test` does. It does not wait on the queue, because behind
  a long one the wait has no bound: the line reads "not tested yet - queued
  as doctor job N; the result appears under Settings > This machine" - never
  ok, and the exit code counts it as not tested. A wait, if one is wanted
  after all, is bounded by a stated number of seconds and ends on that same
  line (TASK-089.13).
* `/health` answers and is *not* provably this library: another
  `data_dir`, or a MyScribe too old to carry the fields. `--prove` loads
  no model and reports "not tested (a MyScribe is running on port N)",
  never ok. There is one card per machine, not one per library, so a
  smoke beside that app is the very collision W5 forbids. Nothing is
  queued either: that app's queue is not this library's. Until §3.9's
  fields exist, every `/health` that answers lands here.
* Nothing answers, and no job row in this library is `running`. The setup
  child runs the smoke itself, as `python -m scribe.doctor` does today.
  With a `running` row it reads "not tested (a job is running)".

The last branch does GPU work outside a `scribe.runner` child, and it is
inside ADR-001 for a reason that should be said and not assumed. The ADR's
Must constrains the web process and the supervisor thread - they never
import torch, and the supervisor spawns one runner at a time. A one-shot
command that loads a model in its own process is the existing precedent: the
doctor's CLI does exactly that today (`scribe/doctor.py:7-16`, `:805-808`).
What the three branches cannot see is a stated limit, the same one as §3.9:
only the default port and a given `--port` are probed. An app on another
port serving this library is not seen, and a clone user can start the app
in the middle of a proof. The `running`-row check narrows that gap and does
not close it, and the report says so in one line. Report lines: environment (the
uv version and lock sha), ffmpeg and ffprobe with where each came from, the
CPU checks, the accelerator, one line per credential with its source and any
conflict, Ollama, the AI provider's one word, transcription (device, load
and wall seconds, word count), the app, the log path. Anything skipped reads
"not tested" with where to finish it.

**What CI proves on three runners:** `python install.py --non-interactive`
from a fresh checkout to a green suite, with `git status --short` clean
afterwards; the `--plan` shape, pinned by a `main()`-level test (today no
test calls `setup.main`); an all-skipped run that writes no settings row,
leaves `.env` unchanged and stamps every id as skipped; the SENTINEL tests;
`--hf-token` refused with exit 2; every Ollama state through
`MockTransport`, including five embedders only, the missing `capabilities`
key and a refused connection with a stripped `PATH`; that nothing runs or
downloads in the three present states; the marker's rules, its binding to
version and path included; the contract-1 stamp migration; the gate
reading two files and importing nothing, with a marker file and no stamp,
and with a stamp that has a `contract` and no `ended`; a `--plan` on a
missing database and on a cleanly closed one, each leaving the directory's
file list unchanged; R1 with one test per call site and one that names
`sweep_speaker_passes`; the contract test over both doors (G2); the proxy
bypass, red first with a dead proxy in the environment; the release smoke
walking the setup path; the pin's URL and digest.

**What only a person at a real machine can prove, and who:** the Tk
dialog's pixels, once per OS (Robert, Windows; a Mac and a Linux desktop:
not available to the design run). Anything on macOS: Gatekeeper, the dmg
flow, MLX loading a local folder. The Mac is somebody else's (G9): each
milestone bundles its macOS criteria into one list - per point the command
and the expected output - for one sitting with its owner, and until then
every macOS claim reads "not run". The release notes say what CI proves on
its macOS runner - the build, the first sync, the health answer, a page,
the stop - and nothing more (TASK-089.24). The README's sentence about the
M2 (`README.md:9`) gets its date and says whose machine it was.
The absent-Ollama install: Windows Sandbox or a VM without Ollama, a Mac,
and WSL for the guided Linux path - this machine proves only that a
present Ollama is left alone. Whether `/VERYSILENT` starts the daemon,
whether that daemon inherits `OLLAMA_MODELS` (8a), and how `taskkill /T`
treats it afterwards: the same Windows Sandbox run.
The GPU smoke on the RTX 3080 and on Apple Silicon (TASK-040's open
criterion, which none of this closes). An autostart entry surviving a
real logout, per OS. A real corporate proxy. A path nobody ran is
reported as "not run" and its box stays unticked.

Written down in advance for Robert's machine, so the run can be judged:
no credential question, no Ollama question, and the four "Found on this
machine" lines carrying the facts §1 shows. Per credential: the variable
name, the source in force, every other source, and any conflict. For
Ollama: the version, the state, the chat-model count and "left alone".
The wording and the wrapping may differ; the facts may not. By §0's
measurement the Hugging Face line and the OpenRouter line both name the
registry's machine hive as a second source, and only the Hugging Face
line carries the `.env` conflict.

## 8. Open questions

For Robert:

1. **A watch folder outside the browse roots.** On Windows the default
   root is the profile's drive (`scribe/fsbrowse.py:44-53`), so a folder
   on D: is refused; and a `fsbrowse_roots` row *replaces* the default
   (`:59-63`), so adding just that folder would narrow browsing to it.
   Widening a read boundary is not done behind a y/n here. One more
   explicit question could do it.
2. **Which of the two Hugging Face tokens on this machine is valid** was
   not tested by anybody. The first real `--prove` will say.
3. **A login entry for a clone (§3.10)** sits close to the start script
   R3 declined. The fallback: release only.
4. **Did you choose the modal before the app starts?** The status
   paragraph says what TASK-040.06 does and does not record; it was one of
   three reasons the `/welcome` page lost.
5. **A yes to installing Ollama also makes it the provider when question
   5 was left open (§3.3)**, and question 7 says so. The other way: write
   the model row only.
6. **How a release points at an adopted library (§2).** The `"data"` key
   is this spec's proposal. The grill left it to TASK-089.19 - the agent's
   proposal, not Robert's decision, and he can overturn it when ADR-015
   comes up for acceptance.

Verified by nobody, and every task that leans on one says so: the real Tk
window; anything on macOS; whether `OllamaSetup.exe /VERYSILENT` starts the
daemon, and whether that daemon inherits `OLLAMA_MODELS` (8a); the Windows
signer's name; OpenRouter's key check with a real key; `mlx-whisper` loading
from a local folder; process lineage under `taskkill /T` after the Ollama
installer exits; what Ollama does with a pull whose client goes away;
whether an Ollama older than 0.34.0 returns `capabilities` on `/api/tags`;
the known install locations on macOS and Linux; the three autostart entries,
and the path each must name for a release (`APPIMAGE`, the `.app` bundle,
`sys.executable`); whether a sync under a running app fails loudly on
Windows; how "no NVIDIA device" is read per OS (§3.8); which of §3.4's two
mechanisms makes the three commands read `.env` before `scribe.paths`
freezes; whether the clip test spawns an ffmpeg of its own; and whether a
read-only open leaves the *live* library's directory byte-identical - the
one measurement (§0) is a scratch database with no other connection, and
what `immutable=1` misreads beside a WAL that holds rows was not tested. The
proxy behaviour has one measurement, on this machine (§0) - not on macOS or
Linux, and not yet pinned by a test. The Ollama install commands were
re-read on 2026-09-20 from the v0.34.2 README and from both install scripts
as served that day; re-verify them at the pin bump, every time. Two of these
are the open questions the records still carry: the silent installer and the
tree kill (ADR-017, TASK-089.18), and the app's own probes behind a proxy
(ADR-015, TASK-089.05).

Judgment calls stated for the record: the contract is a number and not a
list of ids, because a number keeps the launcher from learning anything
about questions; 22 GiB for the 12B offer is an estimate modelled on one
measured card and easy to move, and Apple Silicon gets no 12B offer until
somebody has measured one; the footprint numbers the location
question shows do not exist yet - the download is "about 3.2 GB on
Windows" (ADR-011, 2026-09-11), the unpacked `.venv` here is 5.3 GiB with
the dev group, and the uv cache and the managed Python were not measured -
so measuring a fresh home is a criterion of TASK-089.14; and `--read-only`
on the doctor rather than running it on a scratch directory, because the
models live under the data directory (`scribe/paths.py:40`) and a scratch
doctor would report weights missing that are there.
