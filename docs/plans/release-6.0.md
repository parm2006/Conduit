# Release Conduit 6.0 from the multi-monitor branch

> **Executor instructions:** Follow each step in order. Stop on a merge,
> verification, tag, or packaging failure; do not publish partial release
> state.
>
> **Drift check:** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 9a40497 -- app/version.py installer/Conduit.nsi tests/test_release_packaging.py README.md RELEASE_NOTES_6.0.md scripts/build_release.ps1 Conduit.spec`

## Status

- **Effort:** S
- **Risk:** MED
- **Depends on:** Plan 009 automated implementation
- **Planned at:** `9a40497`, 2026-08-28
- **Design:** `docs/superpowers/specs/2026-08-28-conduit-6.0-release-design.md`

## Why this matters

The multi-client and multi-monitor work changes Conduit's product architecture
and warrants the `6.0` major release. A clean tagged build ties the executable,
installer, source, notices, and hashes to one verified commit.

## Scope

**In scope:** release metadata, its tests, release notes, branch integration,
tagging, and the existing release build.

**Out of scope:** further feature changes, signing without configured signing
credentials, and GitHub Release publication.

## Steps

### Step 1: Reconcile main without losing the accepted design

Merge `origin/main` into `multimonitor`. Resolve the shared topology design in
favor of the expanded `multimonitor` content. Verify neither side has an
unmerged path.

### Step 2: Prepare exact 6.0 metadata test-first

Change the release metadata test to require `PRODUCT_VERSION == "6.0"`,
`FILE_VERSION == (6, 0, 0, 0)`, and the `v6.0` source URL; run it and observe
the expected failure. Then update `app/version.py`, `installer/Conduit.nsi`,
and `RELEASE_NOTES_6.0.md`. Run the focused release tests to green.

### Step 3: Verify and integrate

Run compileall, all unit tests, and `git diff --check`. Commit and push
`multimonitor`, fast-forward `main`, rerun the full suite on `main`, push
`main`, and create/push annotated tag `v6.0`.

### Step 4: Build the official artifact set

Create a clean detached worktree at tag `v6.0`. Run
`scripts/build_release.ps1` with the repository virtual-environment Python and
without `-DevelopmentBuild`. Verify all six outputs and their SHA-256 entries,
then copy the artifacts to the main checkout's `dist` directory.

## Commands

| Purpose | Command | Expected |
| --- | --- | --- |
| Focused metadata | `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging.ReleaseMetadataTests -q` | pass after one intentional red run |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q` | all pass |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | exit 0 |

## Done criteria

- [ ] `origin/main`, `origin/multimonitor`, and `v6.0` identify the release history.
- [ ] Product version is exactly `6.0`; Windows file version is `6.0.0.0`.
- [ ] All automated gates pass on merged `main`.
- [ ] The official executable, installer, source, notices, manifest, and hashes exist.
- [ ] Unrelated local files remain untouched.

## STOP conditions

Stop if the merge affects files beyond the known topology design, tests fail
twice after a narrow correction, tag `v6.0` already identifies another commit,
or the official build cannot satisfy its clean-tag and licensing gates.
