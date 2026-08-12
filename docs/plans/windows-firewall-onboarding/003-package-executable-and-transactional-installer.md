# Plan 003: Package DeskFlow and build a transactional NSIS installer

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff be44890 -- .gitignore DeskFlow.spec app/version.py installer/DeskFlow.nsi scripts/build_release.ps1 scripts/generate_third_party_notices.py tests/test_release_packaging.py README.md requirements.txt LICENSE`
> If in-scope files have changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: `001-build-firewall-core-and-helper.md`,
  `002-integrate-server-firewall-onboarding.md`
- **Planned at**: revision `be44890`, 2026-07-28

## Why this matters

A source launch can only bind a firewall exception to `python.exe`. Users need
a stable `DeskFlow.exe` so Windows can scope the rule to the product, plus an
installer that makes firewall consent mandatory and rolls back instead of
leaving a broken or partially configured installation. Distribution must also
carry DeskFlow's GPL terms, source location, and dependency notices.

## Current state

- `requirements.txt:1-5` includes PyInstaller; the active virtual environment
  reports PyInstaller `6.21.0`.
- `.gitignore:6-8` ignores build output, distribution output, and all
  `*.spec` files. The canonical root `DeskFlow.spec` must be explicitly
  unignored while generated specs remain ignored.
- `run.py` is the executable entry point and Plan 001 adds helper dispatch
  before GUI import.
- `app/assets/app_icon.ico` is the existing application icon.
- `README.md:1-24` identifies DeskFlow `v4.3s` but there is no machine-readable
  product/file version.
- `LICENSE` contains GPL version 3. Distributed binaries must carry the license
  and equivalent source access.
- No installer, release script, version module, or third-party notice generator
  exists.
- `makensis` is not installed at planning time. Installing build tooling is an
  external system change and requires explicit approval during execution.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Packaging tests | `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q` | all static/release tests pass |
| Notice generation | `.\venv\Scripts\python.exe scripts\generate_third_party_notices.py --output build\THIRD_PARTY_NOTICES.txt` | exit 0 and nonempty notices |
| Executable build | `.\venv\Scripts\pyinstaller.exe --clean --noconfirm DeskFlow.spec` | `dist\DeskFlow.exe` exists |
| Executable helper smoke | `.\dist\DeskFlow.exe --deskflow-firewall-helper inspect --base-port 28903` | documented safe exit code; no GUI |
| Installer build | `makensis /V3 installer\DeskFlow.nsi` | installer executable created under `dist\` |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |

## Scope

**In scope**:

- `.gitignore`
- `DeskFlow.spec`
- `app/version.py`
- `installer/DeskFlow.nsi`
- `scripts/build_release.ps1`
- `scripts/generate_third_party_notices.py`
- `tests/test_release_packaging.py`
- `README.md`
- `requirements.txt` only if build-version constraints must be made explicit
- `LICENSE` only as an input; do not rewrite its terms

**Out of scope**:

- Firewall rule semantics and helper parser — Plan 001 owns them.
- Server-mode UI — Plan 002 owns it.
- Purchasing, generating, or storing a code-signing certificate.
- Publishing a GitHub release or uploading binary artifacts.
- Changing DeskFlow's GPL-3.0 license.
- Adding an installer tool with a commercial-license expectation.

## Steps

### Step 1: Define release metadata and license inventory test-first

Write failing `tests/test_release_packaging.py` cases for:

- one canonical product label (`4.3s`) and numeric Windows file version;
- PyInstaller and NSIS license/source metadata;
- GPL `LICENSE` and public source URL inclusion;
- notice generation from the exact installed distribution metadata;
- failure when a bundled distribution lacks usable license/notice metadata;
- deterministic ordering and no private installation paths in output.

Add `app/version.py` and
`scripts/generate_third_party_notices.py`. Use `importlib.metadata` and the
installed distribution's declared files; do not guess licenses from package
names. Keep generated notices under `build/`/`dist/`, not tracked source.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q
.\venv\Scripts\python.exe scripts\generate_third_party_notices.py --output build\THIRD_PARTY_NOTICES.txt
```

→ tests pass and the notice file is nonempty.

### Step 2: Add a checked-in PyInstaller specification test-first

Add failing packaging tests for entry point, one-file/windowed mode, icon,
asset collection, version resource, helper imports, pywin32/COM hidden imports,
GPL license, and third-party notices.

Add root `DeskFlow.spec` and unignore only that file in `.gitignore`. Build with
the virtual environment's PyInstaller. The executable must use a stable
installed path at runtime and helper mode must not open the GUI.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q
.\venv\Scripts\pyinstaller.exe --clean --noconfirm DeskFlow.spec
Test-Path .\dist\DeskFlow.exe
```

→ tests pass and `Test-Path` returns `True`.

### Step 3: Build mandatory-consent NSIS behavior test-first

Add failing packaging tests that inspect the NSIS script contract:

- `RequestExecutionLevel admin`;
- interactive firewall consent occurs before file copy;
- **No** or closing consent aborts setup;
- silent installation aborts because it cannot obtain informed consent;
- installed helper creates and then verifies the rule;
- any nonzero helper/verification result removes the partial rule and rolls
  back files, shortcuts, and uninstall metadata;
- uninstall invokes helper removal before deleting `DeskFlow.exe`;
- executable, GPL license, source link, and third-party notices are installed;
- no Public-profile, firewall-disable, `netsh`, PowerShell, or arbitrary
  command behavior exists in the installer.

Add `installer/DeskFlow.nsi` without third-party plugins unless their licenses
are added to the notice inventory. Use only documented NSIS behavior and the
restricted packaged helper.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q`
→ all tests pass.

### Step 4: Add the release build entry point

Write failing tests for `scripts/build_release.ps1` command ordering and
failure propagation. Implement a script that:

1. runs compile, full tests, and `git diff --check`;
2. generates and validates third-party notices;
3. builds `DeskFlow.exe`;
4. runs helper inspect smoke without opening the GUI;
5. finds `makensis.exe` from an explicit parameter or known installed command;
6. builds the installer;
7. optionally calls a caller-supplied signing command without storing secrets;
8. prints final artifact paths and tool versions.

Do not auto-download NSIS. If it is absent, stop with an actionable message.
The executor may request approval to install official NSIS separately.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q`
→ all tests pass.

### Step 5: Build and smoke-test real artifacts

Request explicit approval before installing NSIS if `makensis` remains
unavailable. Install only from the official NSIS/winget package. Then run the
release script and inspect its produced files.

Do not install the generated DeskFlow setup yet; Plan 004 owns live system and
two-PC acceptance.

**Verify**:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
Test-Path .\dist\DeskFlow.exe
Get-ChildItem .\dist\*Setup*.exe
```

→ build exits 0, packaged executable exists, and one installer exists.

### Step 6: Document the supported release path

Update `README.md` with source-build, packaged-build, installer consent,
Private/LocalSubnet scope, GPL/source availability, unsigned-development-build
warning, and uninstall behavior. Do not present a disclaimer as a substitute
for installer setup.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q`
→ documentation contract tests pass.

### Step 7: Run the complete gate

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

→ every command exits 0.

## Test plan

- All notice, spec, NSIS, and build-script tests must fail before their source
  artifacts are added.
- Tests may inspect build definitions but must not install software, elevate,
  or mutate the firewall.
- Real executable and installer builds are required after focused tests pass.
- Confirm helper inspect does not open a GUI and does not modify firewall
  state.
- **Verify**:
  `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q`
  → all pass.

## Done criteria

- [ ] `dist\DeskFlow.exe` builds from checked-in `DeskFlow.spec`
- [ ] Packaged helper inspect runs without a GUI
- [ ] NSIS installer builds and contains mandatory consent/rollback behavior
- [ ] **No**, close, elevation refusal, silent mode, or rule failure cannot
  leave a completed installation
- [ ] GPL license, source URL, and generated third-party notices ship
- [ ] No signing secret or certificate is committed
- [ ] Full suite, compile, and diff checks pass
- [ ] No files outside the in-scope list are modified

## STOP conditions

Stop if:

- PyInstaller cannot package helper mode with a stable executable path.
- NSIS would require an unreviewed plugin or incompatible license.
- A dependency's license/notice metadata is missing and cannot be obtained from
  its official distribution.
- The installer cannot reliably abort/roll back after consent or helper
  failure.
- Building requires disabling antivirus, firewall, UAC, or another security
  control.
- A code-signing certificate or secret would need to enter the repository.
- A step's verification fails twice after a reasonable fix attempt.

Write a handback; do not weaken consent, rollback, or license requirements.

## Maintenance notes

Every dependency, version, port-layout, executable-name, or installation-path
change can stale the package, notice inventory, or firewall rule. Review these
together. Keep build tools unmodified, record their versions, retain license
inputs, and never convert missing metadata into a guessed notice.
