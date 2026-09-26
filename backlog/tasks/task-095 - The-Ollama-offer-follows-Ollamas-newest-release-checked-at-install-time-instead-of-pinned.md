---
id: TASK-095
title: >-
  The Ollama offer follows Ollama's newest release, checked at install time
  instead of pinned
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-26 17:49'
updated_date: '2026-09-26 20:28'
labels:
  - ollama
  - installer
  - security
dependencies: []
priority: medium
ordinal: 169000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert decided on 2026-09-26: MyScribe no longer pins the Ollama release it offers (scribe/ollama_release.json, v0.34.3). Asked with three options, he chose 'newest release, still checked': when setup builds the offer (Ollama absent, and the provider is Ollama or undecided), MyScribe asks GitHub for Ollama's newest non-draft, non-prerelease release, shows its URL, size and sha256 before the question, and after the download checks the file against both the GitHub API asset digest and the release's own sha256sum.txt, plus on Windows the Authenticode signer (O=Ollama Inc.). The pin and its upkeep go away: scribe/ollama_release.json as the source of truth, the check-pin step in ci.yml, step 2b in docs/RELEASING.md, and TASK-094. This changes ADR-017's 'from a pinned and verified artifact', so a Proposed successor ADR (ADR-021) records it; Robert accepts it. What stays from ADR-017: offer only when absent, shown and agreed to, never touch an Ollama that is there, the marker rules, Linux shows commands and runs nothing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first: with the release API answering a newer release than the old pin, the offer shows that release's URL, size and sha256; today it shows the pin's
- [x] #2 No network call happens unless an offer is actually built (state absent and provider ollama or undecided): a start, a plan in any other state and every present state make no request to GitHub, proven by a client that raises
- [x] #3 The download is refused unless its sha256 equals the API digest AND the entry in the release's sha256sum.txt; a mismatch with either, a missing digest, or an unreachable sha256sum.txt ends on ollama.com/download plus Check again. Windows still verifies the signer. A test per refusal
- [x] #4 When GitHub cannot be reached or rate-limits, the sitting says so in one sentence, offers the vendor page, and writes nothing; a proxy is named the way the other downloads name it
- [x] #5 The model figures and the launcher's location question no longer read an Ollama installer size from a pin: scribe/footprint.json's ollama block either comes from the release metadata at offer time or is marked an estimate, and the test that held pin and footprint equal is replaced, not deleted silently
- [x] #6 scribe/ollama_release.json is removed or reduced to what is not a pin (the vendor page, model sizes); ci.yml's check-pin step and RELEASING.md's step 2b are gone; the notes say what replaced each
- [x] #7 ADR-021 is drafted as Proposed, superseding ADR-017 in its pinned-artifact clause only, signed as the agent; acceptance is Robert's
- [ ] #8 The per-file suite is green and CI is green on all three runners
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red first, on the code as it is: the offer follows a release API that answers a newer release (v0.99.1) than the old pin; a plan in every non-offer path reaches no GitHub (real reader, raising client); one test per refusal (digests disagree, no digest, no sha256sum.txt, sha256sum.txt unreachable or without the line, asset missing, 404, connect error, rate limit 403/429, download 404, file digest vs API, file digest vs sha256sum.txt); token never printed or stored, sent to api.github.com only; footprint marked an estimate. Keep every red output.
2. scribe/ollama_release.json becomes scribe/ollama_offer.json (git mv): vendor page, model sizes and the per-platform facts that are not a release (asset name, installer flags, signer, install folder, notes). No tag, url, bytes or sha256.
3. scribe/ollama_setup.py: latest_release() asks repos/ollama/ollama/releases/latest through a release_client() seam with a short timeout, reading assets and sha256sum.txt with the reader check_pin had (moved, not copied); every failure is an InstallError with one sentence, the vendor page, Check again and the proxy clause. install_plan takes the fetched release; the plan carries the API digest and the sha256sum.txt entry as two fields and install() checks the file against both. check_pin and --check-pin go.
4. scribe/setup.py: the offer is built only in state absent with provider ollama or undecided, and not at all when a start (--unasked-only) would drop an ollama_install that was already put. A refused fetch is a sentence in the ollama note with Check again, no question, nothing written. apply fetches again and names the release it installs.
5. tests/conftest.py: an autouse stub answers a canned release (not v0.34.3, all three assets) so no test reaches GitHub; a fixture hands the real reader back.
6. footprint.json's ollama block is marked an estimate from v0.34.3; the launcher says 'about' instead of 'up to'; the pin==footprint test is replaced by one that pins the estimate rule.
7. ci.yml check-pin step, RELEASING.md step 2b and the pin section go, with a sentence on what now guarantees integrity; README, CHANGELOG [Unreleased], docs/macos-acceptance.md point 5 follow.
8. ADR-021 drafted as Proposed with adr new, signed as the agent; lint --strict and the index.
9. Mutants on a copy; per-file runs of every file importing ollama_setup, setup, footprint readers, test_launcher*.py, test_install.py; notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation notes (build agent, 2026-09-26, worktree MyScribe-wt-095, nothing committed)

EVIDENCE = C:\Users\rvdbr\AppData\Local\Temp\claude\D--Users-Robert-Documents-GitHub-RvdB-MyScribe\d0ea7837-cd8e-4a70-958e-8536dd2e4652\scratchpad\build\095

### What changed and why
- scribe/ollama_release.json -> scribe/ollama_offer.json (git mv, staged, not committed). Keeps only what is not a release: asset name, Windows flags, signer, install folder, notes, model sizes, vendor page. No tag, url, bytes or sha256.
- scribe/ollama_setup.py: new LATEST_API (repos/ollama/ollama/releases/latest), release_client() seam (timeout 5 s connect / 10 s read, because --plan runs before the launcher's first window), latest_release() (two GETs: the API and the release's sha256sum.txt; the sha256sum reader _sums_of is check_pin's, moved not rewritten), _trusted_asset() (asset present, size, API digest, sha256sum.txt present, line present, equal - else one InstallError sentence via _ended: vendor page, Check again, credentials.proxy_note()). install_plan() builds from the fetched release and carries sha256 (API) and sha256sum (txt) separately; install() refuses unless both are present and equal before a byte is fetched, and download() checks the file against the API digest, so the file must equal both. GITHUB_TOKEN goes as a bearer to api.github.com only (not to the sha256sum.txt host), never printed, returned or stored; unauthenticated works. Rate limit = 429, or 403 with X-RateLimit-Remaining 0. check_pin, RELEASE_API, pin_client and the --check-pin command line are removed with the pin. Download refusal sentences no longer say "pin". The marker still binds version and path; the version is now the fetched tag.
- scribe/setup.py: plan() builds the offer only when state absent AND provider ollama/undecided AND (for --unasked-only) ollama_install was not already put; was_put is computed before the offer. A refused offer is a sentence appended to plan["ollama"]["note"] with check_again true - no new key, so the contract (CONTRACT 3, the key list pinned in test_setup_plan) is unchanged; render() prints it. apply asks GitHub again (two processes), turns an InstallError into a note + reopen, and the progress line names tag, URL, bytes and sha256.
- scribe/footprint.json: the ollama block is labelled "an estimate from Ollama's v0.34.3 installers"; source says why the launcher cannot know the newest size. packaging/launcher/myscribe_launcher.py: "Up to X GB" -> "About X GB" (an estimate cannot promise a ceiling).
- .github/workflows/ci.yml: the check-pin step is gone, replaced by a comment on what guarantees integrity now. docs/RELEASING.md: step 2b and "The Ollama pin" section gone, replaced by "Ollama is not a release step". README (offer sentence; "Nothing is Proposed" -> ADR-021 is Proposed), CHANGELOG [Unreleased] ### Changed, docs/macos-acceptance.md point 5 (no fixed figures; compare with the releases/latest page and sha256sum.txt, write down the tag).
- tests/conftest.py: autouse _no_github_from_a_test replaces ollama_setup.latest_release with a canned release v0.99.1 (not v0.34.3, all three assets); latest_release_unstubbed and canned_release fixtures. Measured dependency (EVIDENCE/mut-14-conftest-stub-removed-*.txt, the stub replaced by the real reader behind a client that cannot connect): 14 tests in test_setup_plan.py and 19 in test_ollama_setup.py depend on it.
- ADR-021 drafted with `adr new`, Proposed, signed "Claude (agent, session 2026-09-26)". `adr relate` added ADR-021 to ADR-017's and ADR-015's related lists and a "Related to ADR-021" history line each (decision text untouched; the same thing earlier agents did for ADR-016/015/011). Not accepted, no `adr supersede`.

### Criteria and proof
- #1 Red first: EVIDENCE/red-test_ollama_setup.txt - test_the_offer_shows_the_newest_release_s_url_size_and_sha256[win32|darwin|linux] fail with ('v0.34.3', ...) == ('v0.35.0', ...). Green: EVIDENCE/green-test_ollama_setup.txt (104 passed). Real run against the real API, EVIDENCE/real-run-github-latest.txt: newest release v0.34.4 (published 2026-09-23), both digests agree for all three assets, the plan's question text shows v0.34.4's URL, 1,571,115,536 bytes and sha256 4a651432...e210f.
- #2 No request unless an offer is built: test_a_plan_in_a_present_state_asks_github_nothing (4 states, plan and plan --unasked-only), test_a_stored_cloud_provider_asks_github_nothing, test_a_start_after_the_offer_was_put_asks_github_nothing (red before: the offer was built for a question nobody would be asked), test_an_apply_without_a_yes_asks_github_nothing - all with the real reader behind a client that raises; control test_the_one_path_that_builds_the_offer_does_reach_github. Red: EVIDENCE/red-test_setup_plan.txt; green: EVIDENCE/green-test_setup_plan.txt (181 passed). My reading of "a start": a start whose sitting already put ollama_install; a first start where the question is still open legitimately fetches, because it shows the offer.
- #3 Two digests + signer, a test per refusal: test_every_refusal_ends_on_the_vendor_page_and_check_again (11 cases, each asserting its own reason), test_a_file_that_matches_the_api_but_not_sha256sum_is_refused, test_a_file_that_matches_sha256sum_but_not_the_api_is_refused, test_a_plan_without_the_second_source_is_refused, test_the_windows_signer_is_still_checked_on_a_fetched_release; the existing download-mismatch and 404 tests still hold.
- #4 Unreachable/rate-limited: the GitHub cases above, test_a_rate_limit_is_named_as_one, test_an_unreachable_github_names_a_configured_proxy; at plan level test_an_unreachable_github_is_one_sentence_with_the_vendor_page_and_check_again_and_no_question (note, check_again, no question, render prints it, proxy named) and test_a_yes_while_github_is_unreachable_writes_nothing_and_offers_the_page (no rows, no marker, ollama_install reopened).
- #5 footprint: estimate label + "About"; the pin==footprint test is replaced by test_the_launcher_s_ollama_figure_is_labelled_an_estimate_and_its_model_is_the_offer_s (model size still held equal to the offer's) and test_the_ollama_figure_in_the_location_question_is_an_estimate_not_a_ceiling (red: EVIDENCE/red-test_launcher.txt; green: EVIDENCE/green-test_launcher.txt, 103 passed, 1 skipped = the macOS quarantine test).
- #6 test_the_pin_file_is_gone_and_what_stays_is_not_a_pin, test_ci_and_the_release_steps_no_longer_check_a_pin. What replaced each: the pin file -> the fetched release + ollama_offer.json's non-release facts; the CI check -> the two-digest check on the installing machine, proven by the fake-GitHub tests; RELEASING step 2b -> nothing to bump.
- #7 ADR-021 Proposed. `adr-lint --strict docs/adr`: all pass (EVIDENCE/adr-lint-strict.txt); acceptance gates on the directory: no finding for ADR-021 (EVIDENCE/adr-lint-acceptance-gates.txt); adr-quality 1.00; adr-index regenerated.
- #8 Per-file suite green, one process per file: test_ollama_setup 104, test_setup_plan 181, test_launcher 103+1 skip, test_credentials 35, test_doctor 56, test_dotenv_commands 9, test_env 43+8 skips (Windows symlink/file-mode skips), test_install 56, test_launcher_library 12, test_launcher_sitting 57, test_library 15, test_no_secret_anywhere 3, test_proxy 29, test_setup 15, test_setup_library 22, test_setup_prove 43, test_stage_finalize 35, test_web_ai 141, test_web_settings 55, test_uninstall_text 5 (EVIDENCE/green-*.txt). CI on the three runners: NOT RUN - nothing is pushed. Whoever pushes: the suite step on all three runners must be green; there is no Ollama step any more.

### Mutants (on EVIDENCE/mut, a copy; EVIDENCE/mut-*.txt, summary in mut-summary.txt)
13 of 13 killed in the final round: digests that disagree pass; a missing API digest passes; the token sent to the download host; install skipping the sha256sum check; a start building the offer anyway; a refused release not caught; rate limit not named; no proxy clause; the old pin's tag shown; a present state asking GitHub; the launcher saying "Up to"; footprint losing its estimate label; apply installing without asking GitHub again. In the first round mutant 02 (missing API digest) SURVIVED: it was still refused, but as "two different sha256", and the test only asked whether something was refused (EVIDENCE/mut-02-first-round-survived.txt). The refusal test now asserts each case's own reason. `grep -rn MUTANT scribe tests packaging` in the worktree finds nothing.

### ADR judge - needs Robert
ADR-017 is Accepted and its third Enforcement tripwire forbids `ollama/ollama/releases/latest` in scribe/**. The honest implementation needs exactly that endpoint, so `adr-judge` on the worktree diff reports 1 violation at scribe/ollama_setup.py (LATEST_API) (EVIDENCE/adr-judge-worktree.txt); the pre-commit hook will block the orchestrator's commit. I did not disguise the URL. Ways through, Robert's call: accept ADR-021 and run `adr supersede` for ADR-017 first, or commit with an ADR_KIT_OVERRIDE naming ADR-017 and ADR-021 as the reason. Simulated on a copy of docs/adr (ADR-021 Accepted, ADR-017 Superseded by it; EVIDENCE/adr-judge-ADR-021-accepted-on-copy.txt): the worktree diff is clean and a probe diff with one forbidden line per tripwire gives exactly 3 ADR-021 violations (pipe-to-shell, ollama serve, vendor installer URL) and none for releases/latest. `adr-judge --dry-run-enforcement ADR-021` on the real directory checks 0 ADRs, because the judge only enforces a record whose Status section says Accepted - it proves nothing while Proposed. A comment in ollama_offer.json that quoted the endpoint was reworded (ADR-017's own Enforcement note advises this for comments).
- adr-lint did not flag ADR-017's `components: scribe/ollama_release.json`, which now names a removed file. ADR-017 is Accepted and was not edited; ADR-021 names the new file. Supersession would settle it.

### Deviations and surprises
- LEAK IN MY OWN RED RUN: the first version of test_a_yes_while_github_is_unreachable_writes_nothing_and_offers_the_page fenced only Popen. On the old code (the pin) it really downloaded the pinned 1,570,198,936-byte OllamaSetup.exe from github.com, twice (two red runs, ~43 s each), into pytest temp folders. Popen was a raiser, so nothing ran. Both files were deleted afterwards. The test now also fences subprocess.run and download_client. Side effect, read-only: verify_signer on that real installer answered Valid, CN=Ollama Inc., O=Ollama Inc., L=Toronto, S=Ontario, C=CA (EVIDENCE/signer-of-v0.34.3-installer.txt) - the first reading of the signer on a real Ollama installer; recorded in ollama_offer.json and SIGNER_PATTERN's docstring.
- Existing tests changed: test_the_pin_names_a_url_size_and_sha256_per_platform -> test_the_offer_file_names_no_forbidden_form (same forbidden-form checks on the new file); test_the_shown_figures_are_the_pin_s -> test_the_shown_figures_are_the_fetched_release_s; test_the_launcher_s_footprint_figures_equal_the_pin -> replaced (see #5); the five check_pin tests and ReleaseApi are removed with check_pin, their token/reader cases re-covered by the GitHub tests; test_the_offer_is_built_when_ollama_is_absent_... compares the offer URL with the canned release instead of the pin; a_plan also sets sha256sum. No assertion was loosened.
- The plan/apply window: apply fetches again, so a release published between the question and the yes is the one installed; apply names it before downloading. Recorded as a Negative in ADR-021; closing it needs front-ends to hand the shown tag back, which is outside this task.
- The silent installer flags are v0.34.3's install.ps1; a newer installer is assumed to take them (unverified, fails safe with a non-zero exit and no marker).

### Not done, and who can do it
- CI on three runners (#8): whoever pushes.
- A real install of a fetched release on a machine without Ollama: TASK-089.18 criterion 16 (Windows Sandbox / a Mac / Robert's WSL).
- Accepting ADR-021 and `adr supersede` for ADR-017: Robert. Until then the ADR-017 tripwire blocks the commit (above).
- TASK-094 (the pin question) is left alone; this task answers it.

### Addendum (same session, after a final review)
- Other callers of the plan: only `--plan` and the terminal sitting call setup.plan(); the doctor, --prove, the web process and install.py do not (grep of scribe/, packaging/, install.py for plan(, _offer(, install_plan(, latest_release(). So criterion 2's proof covers every product path. The terminal sitting re-plans once when somebody names a library folder, which asks GitHub a second time in that sitting; accepted, it is a sitting that shows the offer.
- Nothing ran on this machine: a read-only listing of %LOCALAPPDATA%\Programs\Ollama shows no file modified on 2026-09-26; its newest files are from 2026-09-23 (EVIDENCE/ollama-install-dir-untouched.txt).
- The red evidence predates three test edits: the refusal test was later made to assert each case's own reason, and three old pin tests were replaced after the red run. The red still stands: on the old code every refusal case fails at pytest.raises (DID NOT RAISE), before any reason is read.
- CONTRACT stays 5. It rises when a question is added (setup.py's CONTRACT docstring); TASK-095 adds none. The offer dict lost `read` and gained `published` and `sha256sum`; no front-end reads `read` (grep of scribe/, packaging/, install.py, tests/).
- A refused offer's note is one sentence ("not installed on this machine, and MyScribe could not offer to install it: ..."), asserted in the test.
- A ready override for the ADR-017 tripwire, validated with `adr-judge --check-override` (EVIDENCE/adr-override-check.txt): ADR_KIT_OVERRIDE="ADR-017: TASK-095 builds Robert's decision of 2026-09-26 (ADR-021, Proposed) to offer Ollama's newest release; the latest-release endpoint is that decision". Using it, or accepting ADR-021 and superseding ADR-017 first, is Robert's call.
- The mutants were run a third time after that note change: 13 of 13 killed (EVIDENCE/mut-summary.txt).

Verified 2026-09-26 by the orchestrator in MyScribe-wt-095, fenced, one file per process: test_ollama_setup 104, test_setup_plan 181, test_launcher 103 (1 skipped), test_launcher_sitting 57, test_install 56, test_setup 15, test_setup_prove 43, test_doctor 56, test_no_secret_anywhere 3, all passed; grep MUTANT finds nothing. ADR-021 accepted by Robert and ADR-017 superseded by it the same day; adr-judge on the staged diff: 0 violations. Recorded: the build agent's first version of one test really downloaded the pinned 1.57 GB installer from github.com twice before it was fenced; nothing ran and the files are deleted.
<!-- SECTION:NOTES:END -->
