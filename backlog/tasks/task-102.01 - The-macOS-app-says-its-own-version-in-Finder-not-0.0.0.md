---
id: TASK-102.01
title: 'The macOS app says its own version in Finder, not 0.0.0'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:34'
labels:
  - macos
  - installer
dependencies: []
parent_task_id: TASK-102
ordinal: 177000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CFBundleShortVersionString is 0.0.0 in the shipped bundle (0.6.0 and 0.8.0), while the window and --version say the real version; Finder's Get Info shows 0.0.0. Report section 1.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The frozen MyScribe.app's Info.plist carries scribe.__version__ as CFBundleShortVersionString and CFBundleVersion, covered by a test of the step that writes it
- [ ] #2 The bundle still verifies with codesign --verify --deep --strict after the change (the release's macOS build and smoke stay green)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: a test calls build_release.stamp_bundle_version on a temp MyScribe.app with PyInstaller's 0.0.0 Info.plist and expects CFBundleShortVersionString and CFBundleVersion = the version, other keys kept; and a test that freeze() for macos-arm64 stamps and re-signs ad hoc (run monkeypatched). Green: plistlib rewrite in freeze for macOS, then codesign --force --deep --sign - because editing Info.plist breaks PyInstaller's ad-hoc signature; the release's Sign step re-signs with a certificate when one exists.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: 3 tests, AttributeError stamp_bundle_version / sign_ad_hoc. Green: tests/test_build_release.py 5 passed. freeze() stamps Info.plist on macos-arm64 with plistlib, re-signs ad hoc and verifies with codesign --verify --deep --strict, so a bundle that does not verify fails the build. AC2 waits for a macOS build: a release rehearsal (gh workflow run release.yml) at the end of TASK-102.
<!-- SECTION:NOTES:END -->
