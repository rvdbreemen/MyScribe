# Changelog

What changed in MyScribe, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the
ones in `pyproject.toml`. The repository carries no tags yet, so the dates
are those of the commits that set the version.

## [Unreleased]

Everything on `main` since 0.2.1. The fixes below came out of a
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
