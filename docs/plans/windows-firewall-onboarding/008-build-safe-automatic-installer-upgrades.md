# Plan 008: Build safe automatic installer upgrades

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> Use test-driven development: add each contract test first, run it, and
> confirm it fails for the missing behavior before editing production files.
> If anything in "STOP conditions" occurs, stop and write a handback - do not
> improvise. When done, update this plan's status row in the effort README.
>
> **Drift check (run first)**:
> `git diff a393072 -- installer/DeskFlow.nsi scripts/build_release.ps1 tests/test_release_packaging.py docs/plans/windows-firewall-onboarding/VALIDATION.md README.md`
> If in-scope files have changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: `003-package-executable-and-transactional-installer.md`,
  `006-add-transactional-conflict-repair.md`,
  `007-integrate-conflict-repair-ux.md`
- **Planned at**: revision `a393072`, 2026-08-05

## Why this matters

The current installer refuses any nonempty installation directory, forcing a
manual uninstall even for a valid older DeskFlow release and preventing safe
recovery from installer-owned remnants. The most recent failed physical setup
also proved that a stale `dist\DeskFlow.exe` can be embedded into a newly named
installer, so the consent page can appear while the packaged firewall repair
is obsolete. This plan makes upgrades automatic after consent, permits only
allowlisted partial-install cleanup, and makes the supported release command
incapable of packaging stale application output.

## Current state

- `installer/DeskFlow.nsi:47-70` rejects silent mode and then aborts whenever
  `$INSTDIR` contains anything; it has no upgrade or recovery classification.
- `installer/DeskFlow.nsi:118-151` owns transaction cleanup and failure
  reporting. Match its explicit labels, nonzero exit codes, and rollback
  ordering.
- `installer/DeskFlow.nsi:153-205` writes the new application before invoking
  `DeskFlow.exe --deskflow-firewall-helper repair --base-port 28903`; repair is
  already the final fallible step.
- `installer/DeskFlow.nsi:207-224` defines the packaged uninstaller and removes
  the firewall rule before deleting the application.
- `scripts/build_release.ps1:65-149` runs the source gates, builds the
  executable, smoke-tests helper inspection, and invokes NSIS, but it neither
  deletes stale artifacts nor supplies a release-only NSIS define.
- `tests/test_release_packaging.py:169-360` uses static installer/build-script
  contract tests. Continue this structure; tests must never elevate, install,
  or mutate the host firewall.
- `tests/test_firewall_helper.py:91-103` proves the `repair` command reaches the
  backend and returns success for a verified Ready result. Firewall mutation
  semantics are already covered elsewhere and are out of scope here.
- The approved design is
  `docs/superpowers/specs/2026-08-05-automatic-installer-upgrades-design.md`.
  It accepts that the old binary is not restored if the new installation fails
  after successful removal.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q` | all packaging tests pass |
| Full tests | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Direct NSIS rejection | `& "$env:ProgramFiles(x86)\NSIS\makensis.exe" /V2 installer\DeskFlow.nsi` | nonzero exit naming the missing release-build define |
| Supported release build | `powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1` | exits 0 and freshly creates both artifacts |
| Whitespace | `git diff --check` | no output |

## Scope

**In scope**:

- `installer/DeskFlow.nsi`
- `scripts/build_release.ps1`
- `tests/test_release_packaging.py`
- `docs/plans/windows-firewall-onboarding/VALIDATION.md`
- `README.md` only if its install/upgrade or build instructions become false
- `docs/plans/windows-firewall-onboarding/README.md` for plan status only

**Out of scope**:

- `app/firewall.py`, `app/firewall_helper.py`, and
  `app/windows_firewall.py` - effective repair is already implemented and
  independently reviewed.
- Automatic process termination, reboot-time replacement, or broad process
  enumeration.
- Executing an uninstaller unless the exact fixed-path files and registry
  metadata identify a complete packaged DeskFlow installation.
- Deleting unknown files or any subdirectory under Program Files.
- Silent installation, Public-profile access, firewall disabling, network
  probes, or arbitrary firewall commands.
- Restoring the removed old binary if a later new-install step fails; the user
  explicitly accepted this tradeoff.
- Signing, publishing, or committing `dist\` artifacts.

## Steps

### Step 1: Specify installation-state behavior test-first

Replace the obsolete preexisting-target test in
`tests/test_release_packaging.py` with focused contracts that require:

- fixed `$PROGRAMFILES64\DeskFlow` targeting and no directory chooser;
- a complete upgrade only when `DeskFlow.exe`, `Uninstall.exe`, the DeskFlow
  `InstallDir`, and the exact quoted `UninstallString` agree on that directory;
- directory enumeration before classification, with only these files accepted:
  `DeskFlow.exe`, `Uninstall.exe`, `DeskFlow Source.url`,
  `DeskFlow.installing`, `THIRD_PARTY_NOTICES.txt`, and `LICENSE`;
- any other file or every subdirectory taking the unknown-content failure path;
- an allowlisted but incomplete directory taking a partial-recovery path that
  never executes on-disk content;
- No, window close, silent mode, and UAC refusal causing no old-install
  mutation.

Run the focused tests and confirm they fail because the installer lacks the
classification paths, not due to a malformed assertion.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging.NsisInstallerContractTests -q`
-> at least one new test fails for the expected missing contract.

### Step 2: Specify safe upgrade and partial cleanup test-first

Add separate failing contracts requiring:

- the exact installed executable is renamed to one reserved sibling filename
  and immediately restored after consent but before old uninstall;
- an existing reserved filename, first rename failure, or restore failure
  aborts without launching the uninstaller;
- only the exact `$INSTDIR\Uninstall.exe` is run, silently and synchronously,
  after the lock preflight;
- old uninstall success and an empty-directory verification both precede new
  file copy;
- the partial-recovery path deletes only the allowlist plus DeskFlow shortcut
  and registry remnants, checks deletion errors, and never launches either old
  executable;
- transaction rollback never deletes preexisting content before the installer
  marks cleanup/removal complete;
- `%LOCALAPPDATA%\DeskFlow` does not appear in installer deletion logic.

Confirm RED before changing NSIS, then implement the minimum explicit NSIS
state machine. Classify without mutation in `.onInit`; perform upgrade or
partial cleanup only inside the install section after consent. Unknown content
must fail closed. Keep firewall repair as the final fallible step.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging.NsisInstallerContractTests -q`
-> all NSIS contract tests pass.

### Step 3: Make fresh release assembly mandatory test-first

Add failing `ReleaseBuildScriptTests` requiring:

- explicit removal of the two exact stale outputs before PyInstaller runs;
- existence checks after each new artifact is produced;
- the packaged firewall-helper inspection smoke test occurs before NSIS;
- NSIS receives a private `DESKFLOW_RELEASE_BUILD` compile-time define;
- the NSIS source fails compilation with an actionable `!error` when that
  define is absent.

Confirm RED. Update `scripts/build_release.ps1` using explicit `Remove-Item
-LiteralPath` calls for only the two artifact paths. Add the compile guard to
`installer/DeskFlow.nsi` and pass its define only from the supported script.
Do not add a repair smoke command because release builds must not mutate the
developer's firewall; the existing unit tests prove repair dispatch, while
the physical validation proves the packaged repair effect.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging.ReleaseBuildScriptTests -q`
-> all build-script tests pass.

### Step 4: Compile NSIS both ways and build fresh artifacts

First invoke NSIS directly without the define and confirm it fails at the
guard. Then run the supported release script, which must delete prior outputs,
rebuild and smoke-test the current executable, and compile the installer with
the define. Record timestamps and hashes showing the installer was produced
after the executable.

**Verify**:

```powershell
& "$env:ProgramFiles(x86)\NSIS\makensis.exe" /V2 installer\DeskFlow.nsi
# Expected: nonzero exit naming DESKFLOW_RELEASE_BUILD.

powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
Get-FileHash .\dist\DeskFlow.exe, .\dist\DeskFlow-4.3s-Setup.exe
Get-Item .\dist\DeskFlow.exe, .\dist\DeskFlow-4.3s-Setup.exe |
    Select-Object Name, Length, LastWriteTime
```

-> direct build fails safely; supported build exits 0; both files are nonempty;
the installer timestamp is later than the executable timestamp.

### Step 5: Update physical validation for upgrade and effective firewall repair

Extend `docs/plans/windows-firewall-onboarding/VALIDATION.md` with separate
server/client instructions covering:

- fresh installation and valid same-version upgrade;
- a safe allowlisted partial-remnant scenario;
- installer refusal for a planted unknown file and a planted subdirectory;
- refusal while the exact installed DeskFlow executable is running;
- consent No/close preserving the old installation;
- identity/preferences remaining under `%LOCALAPPDATA%\DeskFlow`;
- post-install inspection of the effective DeskFlow rule and conflicting block
  state;
- a three-port `Test-NetConnection` from the second PC followed by an actual
  DeskFlow control/data/file session.

Do not use ping and do not disable firewall, antivirus, UAC, or network
profiles. Automated work can prepare the guide and artifacts; the owner runs
the two-PC rows.

**Verify**:
`git diff --check -- docs/plans/windows-firewall-onboarding/VALIDATION.md`
-> no output.

### Step 6: Run the complete source gate

Run compile, focused packaging tests, the full suite, and whitespace checks.
Review the diff for accidental generated artifacts or unrelated edits.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
git status --short
```

-> every command exits 0; only in-scope tracked files are modified; `dist\`
artifacts remain ignored.

## Test plan

- Every changed installer/build behavior begins with a focused contract test
  observed failing for the intended reason.
- Static tests cover state classification, consent ordering, exact-path lock
  preflight, old uninstall ordering, allowlisted partial cleanup, unknown
  content refusal, rollback boundaries, and fresh-build enforcement.
- Real NSIS compilation proves the script syntax and its direct-build guard.
- The full release script proves a fresh current executable is packaged after
  the helper smoke test.
- Physical two-PC validation is the acceptance authority for UAC, upgrade,
  effective firewall repair, port reachability, and all three DeskFlow lanes.

## Done criteria

- [ ] Focused tests were observed RED before production edits and then pass
- [ ] Valid existing packaged DeskFlow installs upgrade automatically after consent
- [ ] Allowlisted partial remnants are replaced without being executed
- [ ] Unknown files and every subdirectory remain untouched and block setup
- [ ] Running/locked exact DeskFlow executable stops before uninstall
- [ ] Consent refusal preserves the old installation
- [ ] Direct NSIS compilation without the private define fails
- [ ] Supported release build freshly produces and smoke-tests both artifacts
- [ ] Installer still invokes verified firewall repair as its final fallible step
- [ ] Full suite and `git diff --check` pass
- [ ] Physical validation guide covers effective repair and real two-PC lanes

## STOP conditions

Stop if:

- NSIS cannot distinguish files from directories without adding an unreviewed
  third-party plugin or executing disk content.
- The installed registry metadata does not provide an exact, safely comparable
  uninstall identity.
- The old NSIS uninstaller cannot be waited on or its completion cannot be
  distinguished from failure.
- Any approach requires killing processes, deleting unknown content, weakening
  consent, enabling Public access, or probing the network.
- Firewall repair would need a semantic change outside the already-reviewed
  helper/backend contract.
- Real NSIS compilation or a verification step fails twice after a reasonable
  correction.

On stopping, write a handback with the current state, desired outcome, and
evidence. Do not choose a weaker safety branch silently.

## Maintenance notes

All future packaged releases must use `scripts\build_release.ps1`; direct NSIS
assembly is intentionally unsupported. Keep the partial-file allowlist aligned
with every file written and removed by the installer. Installer upgrades and
firewall configuration remain Windows integration behavior, so a real two-PC
Private-LAN validation is required before release even when static and unit
tests pass.
