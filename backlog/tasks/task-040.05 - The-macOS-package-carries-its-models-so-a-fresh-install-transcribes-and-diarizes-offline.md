---
id: TASK-040.05
title: >-
  The macOS package carries its models, so a fresh install transcribes and
  diarizes offline
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-18 20:25'
updated_date: '2026-09-18 21:46'
labels:
  - packaging
dependencies: []
references:
  - packaging/build_payload.py
  - packaging/build_release.py
  - scribe/stages/diarize.py
parent_task_id: TASK-040
priority: high
ordinal: 130000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today the 55 MB dmg installs an app that cannot work until it has downloaded 1.5 GB of Whisper and been given a Hugging Face token for a gated pyannote repo. Both were hit on a clean machine on 2026-09-18: the doctor reported 'All required checks passed' and the first transcribe job then died at the diarize stage with 401 GatedRepoError, and the whisper weights arrived only while a job was already running.

Decision (Robert, 2026-09-18): bundle both. The weights are redistributable - Systran/faster-whisper-large-v3 is MIT, the mlx-community turbo conversion derives from OpenAI's MIT Whisper, pyannote/segmentation-3.0 is MIT and pyannote/speaker-diarization-community-1 is cc-by-4.0. The pyannote repos are gated=auto on the Hub, so bundling them ships weights a user would otherwise have to request access to; that was raised and chosen deliberately. cc-by-4.0 requires attribution, so the payload's licenses/ directory carries each model's licence and credit, and MANIFEST.json records what was shipped.

pyannote already has a supported local path: diarize.local_weights_dir() (MODELS_DIR/pyannote) is tried before the Hub, which is why the failure message named it first. Whisper needs the same treatment - a place the app looks before the Hub - or a pre-populated HF cache in the per-user home.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 One command fetches every shipped model by pinned revision and verifies it by digest, and refuses to keep a file that does not match
- [x] #2 The app itself can fetch the models on demand, into the directory the stages already look in, with progress and a resumable partial
- [ ] #3 The installed package stays slim - no weights in the artifact - and says plainly what still has to be downloaded and how big it is
- [x] #4 A machine that already has the weights, or a user who fetched them by hand, is never made to download them again
- [x] #5 Nothing gated is redistributed: the pyannote fetch uses the user's own token and the conditions they accepted
- [x] #6 A fetch that cannot finish - no network, no token, a refused gate - says which of those it was and leaves no half-written model behind
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Machinery done and proven; the 1.6 GB build is the part still to run.

packaging/models.json pins both models the way tools.json pins binaries: full commit revision, and a sha256 per file. Large files carry the Hub's own LFS digest, small ones were hashed when the file was written. mlx-community/whisper-large-v3-turbo @ a4aaeec0 (1614 MB, 2 files); pyannote/speaker-diarization-community-1 @ 3533c8cf (33 MB, 5 files).

The pleasant surprise: speaker-diarization-community-1 is self-contained. It ships config.yaml plus its own segmentation/, embedding/ and plda/ checkpoints, and the config names them as $model/segmentation, $model/embedding, $model/plda - relative to the pipeline directory, not Hub ids. So diarization bundles in 33 MB, not hundreds, and needs no rewriting to work offline.

packaging/fetch_models.py: streams each file from a pinned revision (never main) to a .part and moves it into place whole, verifies every digest, and raises Mismatch rather than leave a file where the build could package it. A verified file present is not re-downloaded; a bad digest is re-fetched once before it is failed, because the likeliest cause is an interrupted run. pyannote lands in models/pyannote, which is diarize.local_weights_dir() - AC5 falls out of that: a user's own newer copy in that directory is what the stage finds, and --only builds one repo.

build_payload.py --with-models fetches and verifies into the payload, writes the credits and records each model's repo and revision in MANIFEST.json.

Evidence. Real fetch of the pyannote repo: 5 files, 32 MB, verified in 14.4 s. Then the decisive one for AC2's diarization half - with HF_TOKEN and HUGGINGFACE_TOKEN unset and HF_HUB_OFFLINE=1, diarize.open_pipeline(bundle) returned a SpeakerDiarization in 2.7 s. No token, no network, loaded. 7 new tests cover the pin rules with no network at all; suite 2418 passed.

Left for AC2 and AC4: fetch the 1.6 GB whisper half, build the dmg, and test transcribe+diarize from the mounted dmg on a machine with no token. Size will be about 1.70 GB, which fits GitHub's 2 GiB asset limit but breaks TASK-040.03 AC2's 'well under 2 GiB' - that criterion needs rewording or a second slim artifact.

Decision changed (Robert, 2026-09-18): the package ships WITHOUT weights; they are downloaded separately. The artifact stays at about 55 MB, the 1.70 GB size question disappears, and nothing gated is redistributed at all - each user's own token fetches pyannote under conditions they accepted themselves. The bundling ACs above were replaced to match.

The machinery survives unchanged, which was always the hard part: models.json's pins, the digest rule, the .part-then-move write, the resume, and models/pyannote being exactly diarize.local_weights_dir(). It moves from the build machine into the app so the app can do the fetching; build_payload keeps --with-models as an opt-in for an offline or enterprise build rather than the default.

scribe/models.py is the separate download, and models.json moved beside it so the pins ship with the app rather than with the build machine. packaging/fetch_models.py is now a thin wrapper over it - duplicating the pins is how a build machine and an application come to disagree about what a model is.

python -m scribe.models lists what is here and what it would cost; --fetch downloads what is missing with per-byte progress, --only takes one repo, --dest takes a directory. Exit codes separate the three failures a caller has to act on differently: 3 no/refused token, 2 a digest that did not match, 1 everything else.

AC6: a file is written to a .part and moved into place only once whole, and one whose digest does not match is deleted rather than kept - on a machine about to go offline the copy here is the only copy there will be, and a wrong one is worse than a missing one because nothing looks for it again. A gated model with no token is refused before the hub is asked at all.

Two things running it caught that the tests had not. The progress bar went backwards mid-file (84.4% then 83.2%): each chunk was being added to the file's baseline instead of accumulating within it, which reads as a download restarting. Fixed, with a test that asserts the sequence is sorted. And a TRANSCRIBE constant I had written speculatively spelled large-v3-turbo in a second .py file, which is exactly what ADR-004's grep test forbids - it was unused, and is gone; the name stays in stages/transcribe.py alone and models.json pins it as data the way tools.json pins a binary version.

Verified live: status says have/MISSING correctly against two different directories, a real 33 MB pyannote fetch into data/models/pyannote completed with monotonic progress and wrote LICENCE-AND-CREDIT.txt beside the weights, and diarize.open_pipeline loaded that directory offline with no token. Suite 2421 passed; 11 tests here, none touching the network.

Left: AC3 - the installed package saying plainly what still has to be downloaded. That belongs in the first-run screen (TASK-040.06) and in the doctor's card.
<!-- SECTION:NOTES:END -->
