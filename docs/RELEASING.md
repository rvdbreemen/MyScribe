# Cutting a release

What happens, in what order, and what has actually gone wrong doing it. The
first real release was v0.5.0 on 2026-09-19; it took four tags, and every
failure is written down below rather than smoothed over, because each one is
a thing the next release can hit again.

## The short version

```sh
# 1. The version, in the three places that must agree
#    pyproject.toml, scribe/__init__.py, uv.lock (uv lock rewrites the third)
uv lock

# 2. The CHANGELOG entry, newest first, dated
# 2b. The Ollama pin (scribe/ollama_release.json, ADR-017): is it still the
#     release we mean to offer? If Ollama released since, bump `tag`, the
#     per-platform url/bytes/sha256 and `read`, and the three numbers in
#     scribe/footprint.json's "ollama" block (a test holds them equal). Then
#     download both installers, hash them, and compare with the API digest
#     and the release's sha256sum.txt - the check below reads the metadata
#     only and never downloads an installer:
.venv/Scripts/python -m scribe.ollama_setup --check-pin
# 3. Commit, push, let ci.yml go green on all three operating systems
# 4. Merge to main - a release should point at code that is on main
# 5. Tag and push
git tag -a v0.5.0 -m "MyScribe 0.5.0 ..."
git push origin v0.5.0
```

The tag starts `release.yml`. Nothing is published until all three builds and
their smoke tests have passed.

## What the workflow does

| Job | What it proves |
| --- | --- |
| `version` | The tag matches `scribe.__version__`. Six seconds, before anything is built: an artifact reporting a different version than its tag is a support case that outlives the release. |
| `build` (×3) | The artifact is built on its own OS **and started**: `build_release.py --smoke` runs the frozen launcher against a fresh home - first sync, `/health`, a page, quit. "The build produced a file" is not the thing a release needs to be true. |
| `publish` | SHA256SUMS, the attestation where GitHub offers one, and the GitHub Release. Tag runs only; a `workflow_dispatch` run uploads the same artifacts and publishes nothing. |

## The Ollama pin

`scribe/ollama_release.json` pins the one Ollama release MyScribe offers to
install when a machine has none (ADR-017). It has an owner: this step, and the
`--check-pin` step in `ci.yml`, which turns red the day the pinned URL stops
resolving or its size or digest no longer match the release's metadata. Red
there means the release step above, not a fix to the check.

What the check reads, so that nobody has to download 1.57 GB per CI run: the
GitHub API's `size` and `digest` fields for the tag (`releases/tags/<tag>`,
never the newest release), the release's own `sha256sum.txt` as a second
source, and one HEAD per artifact URL, whose `Content-Length` must be the
pinned byte count. The release step is where the artifacts are actually
downloaded and hashed by hand - GitHub's digest is a single source until then.

## A rehearsal without a tag

```sh
gh workflow run release.yml --ref main
```

Same build and smoke jobs, artifacts uploaded to the run, no Release created.
Worth doing when anything in `packaging/` has changed, because a tag you have
to withdraw is messier than a run that fails.

## What has gone wrong, and what it looked like

These are not hypotheticals. All four happened on the way to v0.5.0, each on a
step that had never run on a real runner before.

**The smoke test ran a directory.** PyInstaller's onedir is a folder named
after the app with the executable *inside* it. Linux said
`PermissionError: [Errno 13] Permission denied` after building a perfectly
good 121 MB AppImage. Fixed in `_frozen_binary`; the shape is pinned by a test.

**Inno Setup refused the whole script.** `Unrecognized [Setup] directive` -
`SetupAppTitle` is a `[Messages]` entry. It had been in `[Setup]` since the
script was written and no Windows build had ever run to say so.

**The Linux runner ran out of disk.** The smoke test does a real first sync,
and on Linux that is torch with its whole CUDA stack, into a runner that
arrives with about 5 GB free. Both workflows now clear the toolchains nobody
here uses before they start.

**Attestation is not available for a user-owned private repository.**
`actions/attest-build-provenance` failed the publish over three good builds
with exactly that message. It is conditional now, the way signing already was.
`SHA256SUMS` is published either way, and that is the part a user can check.

**The publish job never started (v0.6.0, 2026-09-24).** The version check and
all three builds with their smoke tests passed; `publish` failed in three
seconds with no steps and no log, because GitHub did not start it: "recent
account payments have failed or your spending limit needs to be increased".
The API still answered with that sentence as the job's annotation
(`gh api repos/<owner>/<repo>/check-runs/<job id>/annotations`); `gh run view
--log-failed` only said "log not found". The fix is in Billing & plans, not in
the workflow. The release was then published by hand from the run's own
artifacts, which is exactly what the job does and costs no Actions minutes:

```sh
gh run download <run id> -D dl && mkdir artifacts && find dl -type f -exec cp {} artifacts/ \;
cd artifacts && sha256sum * > SHA256SUMS      # the job's own step; check each .sha256 first
gh release create v0.6.0 --verify-tag --title v0.6.0 --notes-file <notes.md> *
```

Check afterwards that `gh release view` lists every artifact with the digest
the build printed, and that the tag points at the commit on main.

## Signing

Both signing steps run only when their secret exists and print a notice when
it does not, so a missing certificate cannot stop a release that is otherwise
sound. Today neither is configured:

* macOS ships ad-hoc signed. A downloaded dmg carries `com.apple.quarantine`
  and Gatekeeper will refuse it until the user chooses **Open Anyway** - the
  dmg's "Open me first" note explains that.
* The Windows installer is unsigned and SmartScreen will say so.

Set `MACOS_CERTIFICATE` or `WINDOWS_CERTIFICATE` in the repository's secrets
and the steps start doing the work instead of explaining themselves.

## If a release fails

Nothing is published unless every build passed, so a failed run leaves no
half-release behind. Fix the cause, then move the tag:

```sh
git tag -d v0.5.0 && git push origin :refs/tags/v0.5.0
git tag -a v0.5.0 -m "..." && git push origin v0.5.0
```

Moving a tag is only clean while nothing was published. Once a Release exists,
cut the next number instead.
