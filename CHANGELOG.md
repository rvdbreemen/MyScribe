## [Unreleased]

### Changed

- A first run's questions now come after the environment is installed, not
  before it, so the first minutes look different: the download runs first and
  the dialog opens when it is done. The answers can only be applied by a python
  the sync makes, which is why they waited. Starting again with `--setup` while
  MyScribe is already running no longer opens the dialog beside a window that
  closes three seconds later - it says to quit first and start again
  (TASK-089.01).

- ADR-015 and ADR-017 are Accepted, so every decision behind the installer is
  settled: setup is one engine behind a JSON contract that the launcher, a
  clone's installer and the console only render, and third-party software is
  installed only when it is absent, shown and agreed to, with an Ollama that
  is already there left alone. ADR-015's open question was measured first
  rather than waved through, and the measurement found a defect that stands
  today (see Fixed). ADR-017's is recorded as deferred and not as answered:
  it needs a Windows machine without Ollama, and the rule it bears on is
  written the safe way whichever the answer.

- ADR-016 is Accepted: a missing `llm_provider` row selects no provider, and
  nothing is sent until somebody has chosen. Today a missing row falls through
  to OpenRouter, and the widest path there is not a button but the sweep that
  runs at every app start over the whole back catalogue with no ceiling. It
  reverses the recorded decision "Defaults: commercial providers", which was
  safe while choosing was something a person did; every setup question is
  skippable now, so the skip is what most installs get. A machine whose row
  exists behaves exactly as before. The record's tripwire only flags a
  fall-through somebody adds, never the four that stood on 2026-09-20.

- ADR-016 is built (TASK-089.07). `default_provider()` answers `""` for a
  missing row and for a stored name this version no longer registers;
  `DEFAULT_PROVIDER` is gone and the runner refuses a job whose params name
  no provider, with a sentence, instead of sending it to OpenRouter. The AI
  panel, the chat page and the Settings line preselect nothing and say where
  to choose; the bulk labels pass and the startup sweep queue nothing. The
  sweep marks nothing asked, so the recordings it skipped are queued at the
  first start after somebody chose. Jobs the fall-through queued before this
  keep `openrouter` in their parameters and are not recalled.

### Fixed

- A first run wrote down an answer nobody gave. The setup dialog opened with
  "Ollama, on this machine" and "Turbo" already filled in, so "Save and start"
  wrote `llm_provider = ollama` for somebody who only touched the button - on
  a machine that may have no Ollama at all, and that then fails with a
  provider nobody chose and is never asked again, because a row exists. Both
  questions now open on a skip of their own ("Decide later", "Leave as it is")
  and a question left there writes nothing; the heading says what picking
  decides: "Who answers questions about a transcript?". Nothing changes for
  somebody who does pick. Tk had a trap in the same corner: it draws every
  circle in a group filled while the group's answer is the empty string (its
  `-tristatevalue` default), which would have made a dialog that fills nothing
  in look like one that filled everything in, so both groups now name a
  tristate value no answer can take. The download checkbox is deliberately
  not given a skip - a checkbox has no unanswered state - so it still starts
  on yes and a first "Save and start" fetches the weights; the intro sentence
  now names the two questions that do have a skip instead of implying all
  four do (TASK-089.25).

- A first "Save and start" never started anything. On a machine with no
  environment the launcher asked the four questions, then ran
  `scribe.setup` from an environment that did not exist yet: the Popen died on
  a worker thread with `FileNotFoundError [WinError 2]`, `launch.run` was never
  reached, and the window sat on "Saving your answers..." for ever. Only "Skip
  for now" produced an app, and because a skip writes no stamp the dialog came
  back on the next start - where Save then worked. So the happy path only
  worked on the second attempt, after a skip, and every release user walked it.
  The sequence is now prepare, install, sync, ask, apply, start, in one plain
  function the window only calls, and a setup that fails is reported with its
  exit code while the app starts anyway (TASK-089.01).

- Behind an `HTTP_PROXY` variable, MyScribe cannot see what runs on this
  machine. Measured on 2026-09-21: `OllamaProvider.available()` spends its
  two seconds and reports a running Ollama as "not running at
  http://127.0.0.1:11434 (start it ...)", and the launcher's single-instance
  probe reports a MyScribe that is answering as absent, so it would start a
  second one. `NO_PROXY=127.0.0.1,localhost` restores both. TASK-089.05 owns
  the fix; the probe and its output are in the installer evidence folder.

- A data directory moved through `.env` - the documented way
  (`.env.example`) - was the app's library and nobody else's.
  `python -m scribe.setup`, `scribe.models` and `scribe.doctor` import
  `scribe.paths` before they read the file, so `SCRIBE_DATA_DIR` arrived
  after `DATA_DIR` was fixed: setup wrote its settings, its stamp and the
  weights into `./data`, and the app, reading the other directory, behaved as
  if setup had never run. Run as commands, the three now read `.env` first
  and then work the paths out again (`paths.refresh()`); imported as
  libraries they still read no file (TASK-089.03).
- A blank variable no longer hides a value in `.env`. `load_dotenv` used
  `os.environ.setdefault`, so an `export HF_TOKEN=` in a shell, a compose file
  or a service unit kept the real token in the file from ever arriving. A
  process value that says something still wins. The other way round, a blank
  line in the file (`HF_TOKEN=`, as `.env.example` ships it) is no longer
  exported as an empty variable.
- Saving the Hugging Face token could leave two `HF_TOKEN` lines in `.env`,
  where the last silently wins: the writer read plain utf-8 and matched only
  the exact prefix `HF_TOKEN=`, so a file Notepad saved with a BOM, or a line
  written `HF_TOKEN = old`, was not recognised. It now recognises a line the
  way the reader does, and collapses a file that already had two. On Windows
  that includes a line spelled `hf_token`: one variable there, and the line
  the reader would have gone on filling it from.
- The README's list of architecture decisions named ADR-007 and ADR-009 as
  accepted. Both were superseded in 56d92e1 - by ADR-014 and ADR-013 - so
  the README had been pointing readers at the retired versions of the
  logging and coordination rules.
- ADR-011 is Accepted. The per-OS launcher it decides has been built and
  started by CI on all three platforms and shipped in v0.5.0 and v0.5.1,
  which left a decision in production carrying the status of a proposal. Its
  one open question - which LGPL ffmpeg to ship for macOS arm64 - was
  answered by the build script that made those artifacts.
- ADR-008 and ADR-010 are Accepted, which leaves nothing Proposed in
  `docs/adr/`. Both were already running: the feed poller is `FeedWatcher`
  in `scribe/ingest/feeds.py` with its own page and two test files, and the
  reasoning hint travels from `TaskSpec` to every provider's own wire field.
  ADR-010's two remaining questions were settled first - the ban on
  `effort: low` stands as the cautious default with its single-sample basis
  now stated in the record, and a cleaned part that came back a copy of its
  source is refused, with the reuse path fixed in the same change (TASK-088).

### Documentation

- The README lists two things it did not before: every command the app has
  outside the browser (`scribe.setup`, `scribe.models`, `scribe.export`,
  `scribe.proxies`, each verified against its own `--help`), and a table of
  what goes wrong with what to do about it - SmartScreen, Gatekeeper, a CPU
  torch where CUDA was meant, a diarization fallback, the Windows suite
  stall. It also says what the data directory holds and what of it is worth
  backing up, how a download is verified on each OS, and why an app that
  binds localhost still needs the two guards in `scribe/guard.py`.

### Added

- `<repo>/.tools/bin` is first on `PATH` for `python -m scribe`,
  `scribe.setup`, `scribe.models` and `scribe.doctor` when the directory
  exists, and `.tools/` is gitignored. It is where an installer will put the
  tools it fetches for a clone; a checkout without it keeps its `PATH`
  untouched (`env.bootstrap()`, TASK-089.03).
- `env.write_env(name, value)` sets one line in `.env` - any name, through a
  temp file and `os.replace`, a new file 0600 on POSIX, an existing one
  keeping its mode, owner and group - and `env.applied()` says which names
  `.env` supplied, never their values.

## [0.5.1] - 2026-09-19

### Documentation

- `docs/RELEASING.md`: what cutting a release does, in what order, and the
  four things that went wrong on the way to v0.5.0 - a smoke test that ran a
  directory, an Inno Setup directive in the wrong section, a Linux runner out
  of disk, and attestation that a user-owned private repository cannot have.
- The release notes for v0.5.0 on GitHub were the auto-generated stub; they
  now carry the install table, the first-open steps per OS and the reason
  there is no attestation. This changelog no longer claims the repository
  carries no tags.

## [0.5.0] - 2026-09-19

### Added

- A word corrected by hand teaches the glossary: the name typed becomes a
  term and what Whisper produced becomes a variant, so the correction pass and
  the hotword bias both improve from a person's feedback. Only names are
  learned, a term somebody weighted keeps its weight, and a spelling another
  term already answers for is left alone - the guard that keeps one correction
  from rewriting a library.
- The live log of an llm job reads as a conversation: what is being asked
  while it is being asked, the reply with its token counts when it arrives,
  and the conclusion in the kind's own terms.

### Fixed

- A cancel reaches the runner, whoever spawned it. A runner left behind by an
  earlier app was watched by nobody, so nothing read the cancel flag for it:
  the board said "cancelled" while the work carried on, once for twenty-eight
  minutes with a 27B model resident. And the verdict is now the one that was
  asked for, where the supervisor's own reconcile used to race it and leave a
  cancelled job saying "interrupted".
- A local call is planned against the window the model reports, up to a memory
  ceiling that is now a setting (`llm_num_ctx`). Capping every model at the
  shipped default made choosing a bigger one do nothing at all, while the
  error message recommended exactly that.
- A completion's timeout grows with the window it was planned for. Five
  minutes was measured with a 9B model against 8192 tokens; a 27B at q8
  against 32768 has four times the prompt to read and three times the weights
  to read it with.
- `reconcile` can see a recycled pid on macOS and Linux, not only on Windows.
  The guard TASK-065 added had been firing nowhere else, because
  `process_started_at` answered only there - `/proc/<pid>/stat` and
  `ps -o lstart=` both answer it.
- Four tests asserted bit-equality on mel features, which is a claim about
  whichever FFT and matrix multiply are underneath rather than about this app.
  They were the whole of the Windows failure and half the Linux one.

## [0.4.0] - 2026-09-19

### Added

- A podcast page that yt-dlp has no extractor for is read once for the feed it
  announces, and that feed is imported instead. A Castopod profile page went
  from "Unsupported URL" to a 47-episode listing. The discovered address is the
  page author's text, so it must pass the public-host guard before anything
  fetches it.
- The model weights are a download this app makes for itself, pinned by
  revision and sha256, into the directory the stages already look in.
  `python -m scribe.models` says what is here and what it would cost;
  `--fetch` downloads it with progress. Nothing gated is redistributed: the
  pyannote pipeline comes down with the user's own token under conditions they
  accepted.
- First run asks four things - the Hugging Face token, who answers questions
  about a transcript, the model tier, and whether to fetch the weights now.
  All four are skippable, all four are in Settings afterwards, and `--setup`
  asks again.
- Settings has a field for the Hugging Face token the diarize error has always
  told people to set, and a line saying what actually answers a question, with
  which model and window, and whether that leaves this machine.
- The doctor checks that speaker separation can actually start, and says how
  much is still to download. It also reads `.env`, without which it was
  answering about a different machine than the app runs on.
- An llm job shows its work in the live log: the prompt that went out, the
  reply that came back with its token counts, and the conclusion in the kind's
  own terms - for speakers, which cluster became which name. Excerpted, so a
  dozen calls cost kilobytes.
- CI on Windows, macOS and Linux, and a release workflow that checks the tag
  against the version, builds the three artifacts, proves each one starts, and
  publishes only on a tag.

### Fixed

- "Who is speaking" could not run on a local provider at all: its answer budget
  alone exceeded the window it was planned against, so every run failed at the
  prepare stage before a model was called. A kind's answer budget is now
  clamped to what the window affords - which is what every spec already meant
  by "a cap, not a spend".
- A long recording shortens its notes to fit the combine call instead of being
  refused. When even the floor does not fit, the job's outcome is
  `MODEL_UNSUITABLE` - a verdict about the model rather than a failure - and
  the message names a model installed here that would fit.
- A local call is planned against the window the model really has, downwards
  only: the daemon answers 200 to a prompt it quietly truncated, so a plan that
  assumed more would never find out.
- Weights already in the huggingface_hub cache are no longer reported as
  missing, which would have sent a user to download 1.6 GB they had.
- `scribe.__version__` and `pyproject.toml` agree again, and so does the
  lockfile.

## [0.3.1] - 2026-09-16

### Changed

- ADR-013 and ADR-014 supersede ADR-009 and ADR-007: the claim SQL as the
  code has it, current anchors, and enforcement rules the judge can apply
  (the old globs matched no file). Accepted by Robert on 2026-09-16.

## [0.3.0] - 2026-09-16

The fixes below came out of a
whole-codebase review on 2026-09-16: six reviewers over the real code, every
finding put to an independent reviewer told to refute it, and 28 confirmed.
Each fix shipped with the test that failed before it and passed after.

### Fixed

- A rename, move or label in the library rendered the table two cells short
  per row - the upload date under Category, the duration under Labels -
  because the whole-table answer marked the row cells out of band. The
  sidebar and the flash keep that flag; the row cells no longer inherit it.
- `POST /api/media` with a path took any file under the browse roots (the
  whole drive on Windows) and the library served the bytes back. The door
  now wants an audio or video suffix before it reads anything.
- A failure after the transcript was committed - the speaker pass could not
  be queued - marked a finished, current transcript as failed and offered a
  Retry that spent a second GPU pass. The work after the commit is caught and
  recorded in the log and the job's events instead.
- A segment with no words that ran past the window's cut kept the decoder's
  end and text, so the same speech landed in two segment rows - what search,
  the chat tool and the JSON export read. Such a segment is left to the next
  window, which decodes it with its words.
- The proxy stage and the audio route caught `ProxyError` only; a full disk,
  an unwritable proxy directory or Windows refusing to replace an open file
  failed a transcription, or answered a 500 with an empty player. Both now
  treat a disk error as what it is: the original is still playable.
- A job claimed but not yet spawned (`pid` still NULL) was declared dead by
  the startup reconcile, and its own verdict then refused. Reconcile leaves
  a claimed row alone as long as it carries a start time.
- After a power cut, a job's pid could belong to an unrelated process on the
  next boot, and the row sat on `running` for ever. Reconcile now asks the
  process when it started: one that started well after the job cannot be its
  runner. Where that cannot be read, nothing changes.
- An AI card's two-second poll wiped the reader's word selection and search
  on every swap, because the transcript listener asked whether the panel
  existed rather than whether it was the thing replaced.
- Cancelling a job killed the runner but not the ffmpeg it had started, and
  left the job's scratch behind. The runner now gets its own process group,
  the cancel ends the whole tree, and the supervisor removes the scratch the
  runner can no longer remove itself.
- Every line a model read was Whisper's segment row, which nothing rewrites;
  the glossary's corrections and a reader's retypes live on the words, and
  the view and every export print words. A name the correct stage fixed was
  right everywhere the reader looked and wrong in every summary, cleaning
  and speaker pass. The model now reads the words inside each segment's
  boundaries, corrections included (ADR-003 as written).
- One runner at a time was the supervisor's habit, not SQLite's rule: a
  second app instance on the same data, or a runner that outlived a stopped
  app, claimed the next job beside the running one and put two children on
  one card. The claim is now refused inside the same statement while any job
  is running, and the loop reconciles when it finds nothing to claim, so a
  runner that died does not hold the queue until the next restart.
- A cleaned reading kept showing text cleaned from words that had since been
  corrected, and said nothing. It now records a fingerprint of the words it
  was made from; when they no longer match, the page says the words were
  edited after the reading was made and how to refresh it. The reading
  itself stays: it is a paid answer, not a cache (schema v17).
- A followed feed is polled unattended, and its author could publish an
  enclosure at `127.0.0.1:11434` or `192.168.1.1/admin` and have the app
  fetch it from inside the network. An address a feed or playlist wrote down
  may no longer point at this machine or the local network; an address a
  person pastes is still theirs to choose.
- A shutdown that landed while the supervisor was ending a cancelled runner
  forgot the loop that was still running, and a later start would have
  begun a second one beside it. Like the watch-folder thread, it now says
  so and keeps the handle.
- "Test now" on an AI provider said "Saved ✓" beside a card saying the test
  was queued; it queues a job and saves nothing. A settings form that saves
  nothing now says so, and gets neither mark.
- A playable copy the duration check refused was forgotten, so every play
  of that recording ran the whole transcode again - 80 s for 39 minutes,
  on every page load. The refusal is now kept beside the proxy and read
  instead; `python -m scribe.proxies` is the retry and forgets it first.
- A speaker's colour did not follow their name across a re-transcription.
- A cleaning refused for coming back in the wrong number of parts was
  stored with word counts over the parts that happened to line up, and with
  reasons compared across parts that were not each other's. The counts now
  cover every part on both sides, and nothing is compared across a wrong
  pairing.
- With scripting off, a save on the Settings page came back to the Defaults
  card with the saved card hidden behind it; every plain-form save now lands
  on the card it saved.
- With scripting off, renaming, moving or trashing a recording from its own
  page dropped the reader into the library; it now comes back to the page.

### Added

- Per provider, the model is picked from the list that provider last
  returned, grouped by vendor for OpenRouter, with a box for an id the list
  does not have. A saved model the list has never heard of stays pinned as
  its own option, so a Save that touches nothing else cannot replace it.
- A VBR MP3 seeks by estimate in every browser and lands seconds away from
  where the clock says. The pipeline now makes an AAC copy at ingest for
  media that does not seek exactly; `python -m scribe.proxies` makes them
  for a library that predates it. Measured on the same recording: a seek that
  landed +3.7 s late in Firefox now lands within 30 ms in both browsers.
- The recording view: the AI menu above the words with tabs and one panel,
  the player carrying the shape of the recording, follow-along sampled sixty
  times a second instead of four, and the library table as the page rather
  than a box one screen tall.
- The DOM test harness understands descendant selectors, tag names with a
  digit and `classList.toggle`, which is what it takes to drive the
  transcript half of `app.js` rather than read its source text.
- The MP3 header helper writes CRC-protected frames, so the seek rule's
  CRC term is covered: dropping it now fails two tests instead of none.
- A golden run under `SCRIBE_UPDATE_GOLDENS=1` is red for every golden that
  moved, with its diff; a green run always means the goldens matched.

### Changed

- `duration_after_vad` counts each look-ahead's speech once. The stored
  value for a 64-minute recording went from 4030.9 s to 3852.2 s; historical
  runs are left as they are, and the MLX backend records nothing rather than
  a number it cannot measure.
- A cleaning that the gate refused, or that was never checked, says so under
  the transcript instead of showing nothing.

## [0.2.1] - 2026-09-14

### Fixed

- The whale icon outlived its own rename: the two tier icons are named once.

## [0.2.0] - 2026-09-14

### Added

- Every label on a library row, labelling a whole selection at once, and new
  tier icons. The row says where a file lives and what it is about, and
  updates itself when a job changes its labels or its folder.
- A floor under downloads at the doctor's own measure of free disk, a
  question per new feed about how to go on, and a queue you can steer: move a
  job, change its priority.
- A posted feed length is capped; a refused download no longer costs a
  whole back catalogue; a feed can be followed without ticking anything.

### Changed

- ADR-012 accepted after the RTX 3080 run: one `pyproject.toml` and one
  universal `uv.lock` with a per-platform torch source replace the three
  requirement files. ADR-006 is superseded by it.
- ADR-009 accepted: SQLite in WAL mode is the only coordination between web,
  supervisor and runner, restated with import rules that match the imports.
  ADR-002 is superseded by it.
- The macOS branch and the feed-episode import are merged into `main`.

## Before 0.2.0 - 2026-09-06 to 2026-09-13

The first commit, on 2026-09-06, is the app as a whole: local transcription
with speakers, corrections, exports and an AI panel, one web process on
`127.0.0.1:4242`, a runner child per job, SQLite for everything. What was
added in the week after, in the order it landed:

- Linux and macOS alongside Windows; on Apple Silicon, transcription on MLX
  and diarization on MPS, accepted on an M2.
- A pasted link can be a podcast feed, a YouTube channel or a playlist: the
  episodes are listed with a filter, each ticked one becomes its own job, a
  subscription is a row that is polled on a due-date, and an episode keeps
  its name and provenance through the download.
- Labels as a facet in the library, labelling by hand, and a bulk pass that
  skips private recordings rather than refusing the batch.
- The speaker pass runs itself after every transcription, applies what it is
  sure of and says why, never writes over a name a person typed, and asks
  again after a re-transcription; names follow their voice across one.
- A cleaned reading beside the words, one click away, behind a gate that can
  refuse it.
- Each transcription window hears 30 s past its cut so a cut is not an
  ending; a word both decodes wrote at a seam is written once; a look-ahead
  never raises the window's log-mel floor; a second opinion where the decode
  failed by its own measure.
- The AI panel asks the model not to reason where the endpoint enforces the
  cap, and every row says whether it listened.
- The params door takes a transcribe job's keys and nothing else, and a
  retry sieves every stored request, model included.
- torch 2.10.0 and lightning 2.6.6 close the advisories that were reachable
  here; the one that needs torch 2.13 is blocked by CTranslate2's CUDA 12
  dependency and stays open by choice.
- A launcher per OS that prepares a home, syncs the locked environment when
  it changed and runs the app; a macOS dmg that installs and runs;
  `scripts/start.ps1` and `scripts/start.sh` with a detached mode.
- The settings page and the doctor's accel check no longer import torch into
  the web process.
