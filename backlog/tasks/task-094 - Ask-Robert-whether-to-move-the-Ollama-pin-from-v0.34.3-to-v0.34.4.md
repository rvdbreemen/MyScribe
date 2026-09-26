---
id: TASK-094
title: Ask Robert whether to move the Ollama pin from v0.34.3 to v0.34.4
status: Done
assignee: []
created_date: '2026-09-24 15:52'
updated_date: '2026-09-26 17:50'
labels:
  - ollama
  - release
dependencies: []
priority: low
ordinal: 168000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ollama released v0.34.4 on 2026-09-23. MyScribe 0.6.0 ships with the pin in scribe/ollama_release.json at v0.34.3, which still matched its release on 2026-09-24 (check-pin green) and was verified from two sources on 2026-09-23. On 2026-09-24 Robert answered 'not now, prompt me later with this question'. So the next step is to ask him again, not to bump by itself. If he says yes, follow docs/RELEASING.md step 2b: bump tag, per-platform url/bytes/sha256 and read in scribe/ollama_release.json and the three numbers in scribe/footprint.json's ollama block, download both installers (about 1.8 GB), hash them, compare with the GitHub API digest and the release's sha256sum.txt, and run python -m scribe.ollama_setup --check-pin.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Robert has been asked again and his answer is recorded in this task
- [x] #2 If he chose to bump: both installers were downloaded and hashed, the two sources agree, check-pin is green, and the per-file suite and CI are green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: asked again. Robert's answer: stop pinning the Ollama offer altogether; he chose 'newest release, still checked at install time'. That work is TASK-095, and this question no longer exists.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Superseded by Robert's decision of 2026-09-26 to stop pinning the Ollama offer (TASK-095); no pin bump was made.
<!-- SECTION:FINAL_SUMMARY:END -->
