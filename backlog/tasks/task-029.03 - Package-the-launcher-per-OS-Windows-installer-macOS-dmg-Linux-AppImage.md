---
id: TASK-029.03
title: 'Package the launcher per OS: Windows installer, macOS dmg, Linux AppImage'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-11 22:25'
labels:
  - packaging
dependencies:
  - TASK-029.02
parent_task_id: TASK-029
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-008: each OS needs a native artifact carrying the frozen launcher, the app source, uv.lock, a pinned and checksum-verified uv binary and an LGPL ffmpeg/ffprobe, small enough for a GitHub release asset. Builds must run unattended on GitHub runners.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One build script per OS produces the artifact from a clean checkout on its GitHub runner
- [ ] #2 Each artifact is well under 2 GiB and contains the launcher, app source, uv.lock, the pinned uv and ffmpeg plus ffprobe
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
<!-- SECTION:NOTES:END -->
