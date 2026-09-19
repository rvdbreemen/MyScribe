---
id: TASK-040.03
title: 'Package the launcher per OS: Windows installer, macOS dmg, Linux AppImage'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-19 08:57'
labels:
  - packaging
dependencies:
  - TASK-029.02
parent_task_id: TASK-040
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-011: each OS needs a native artifact carrying the frozen launcher, the app source, uv.lock, a pinned and checksum-verified uv binary and an LGPL ffmpeg/ffprobe, small enough for a GitHub release asset. Builds must run unattended on GitHub runners.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One build script per OS produces the artifact from a clean checkout on its GitHub runner
- [x] #2 Each artifact is well under 2 GiB and contains the launcher, app source, uv.lock, the pinned uv and ffmpeg plus ffprobe
- [x] #3 uv and ffmpeg downloads are pinned by version and verified by SHA-256 before they are packaged
- [x] #4 The macOS build produces a working artifact locally on the M2 and it launches the app
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. packaging/tools.json pins uv 0.12.13, ffmpeg 8.1.2 (BtbN LGPL for Windows/Linux, built from source on macOS) and the AppImage tools, each by sha256. 2. build_payload.py assembles app/ from git-tracked files plus bin/ and licenses/, writing MANIFEST.json. 3. build_ffmpeg_macos.sh builds LGPL ffmpeg/ffprobe with --disable-autodetect. 4. build_release.py: payload, a hashed PyInstaller venv made by the payload's own uv, the frozen windowed onedir, then dmg (hdiutil) / Inno Setup / AppImage. 5. Verify the macOS artifact the way a user gets it: from the dmg, quarantined.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
macOS verified 2026-09-12 on the M2. Artifact: dist/MyScribe-0.1.0-macos-arm64.dmg, 55 MB, built in 40 s (payload 78 MB; the .app is 105 MB with Tcl/Tk 9.0 and the payload inside). Copied out of the mounted dmg and marked com.apple.quarantine as a download would be: macOS SIGKILLed it (exit 137) - the expected Gatekeeper answer for an unsigned ad-hoc-signed app, which is what the dmg's 'Open me first' note is for. After xattr -dr (what 'Open Anyway' does): first sync 19 s, 126 packages, uv-managed CPython 3.12.14, all from inside the app; --doctor exit 0 with 'transcription on mlx, diarization on mps', gpu-smoke 77 words; --smoke /health and / ok. The bundled tools are installed into the home as fresh files, so they carry no quarantine (a copy does: shutil.copyfile and copy2 both keep it on macOS, measured) - that matters because the self-built ffmpeg is only ad-hoc signed and Gatekeeper refuses a quarantined one. ffmpeg 8.1.2 built from the signed source tarball in 1 m 52 s, links only libSystem and three system frameworks, and does every operation the app asks of it (aac/mp4 proxy, pcm_s16le decode, volumedetect, ffprobe). Windows and Linux artifacts are built by CI (TASK-029.04); the Tk window is unverified, a visible launch.

AC2 verified on the M2 after the model decision, 2026-09-19: dist/MyScribe-0.2.1-macos-arm64.dmg, 54.5 MB - 0.053 GiB against the 2 GiB limit, so 'well under' survives the weights being a separate download rather than a bundle. MANIFEST.json: 152 files, uv 0.12.13, ffmpeg 8.1.2, and the payload carries the launcher, app/scribe, app/uv.lock, bin/uv, bin/ffmpeg and bin/ffprobe. 'models': {} - nothing shipped, by decision.

The same run exercised build_release.py --smoke, which is new: it starts the frozen launcher the build just produced against a fresh home, and it reported '/health ok, / ok' then 'the artifact started, served and stopped'.

Two bugs found by running it rather than reading it. build_ffmpeg_macos.sh cds into its own work directory, so a relative out-dir lands there and is deleted by the trap - the CI step now passes an absolute path, which it would otherwise have failed on. And build_release.py referenced APP_NAME, a name defined in the launcher and not in that module: it is FROZEN_NAME now, spelled once, because a rename that moved only one of them would produce an artifact that builds and cannot be run.

Worth flagging for AC1's release: the artifact is named 0.2.1, from scribe/__init__.py, while pyproject.toml says 0.3.1. release.yml checks the tag against __version__, so tagging v0.3.1 today would fail that check - correctly.

AC1: run 35430468828 built nothing (ci.yml runs the suite), but release.yml build job is the same code path and was exercised locally end to end on the M2 - dist/MyScribe-0.2.1-macos-arm64.dmg, 54.5 MB, smoke green. The per-OS build from a clean checkout on a runner is proven only for macOS locally; ubuntu and windows build steps remain unrun until a tag or a workflow_dispatch fires release.yml.
<!-- SECTION:NOTES:END -->
