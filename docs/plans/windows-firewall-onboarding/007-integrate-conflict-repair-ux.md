# Plan 007: Integrate conflict repair into Server mode and the installer

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff 75e29dc -- app/firewall_onboarding.py app/gui.py installer/DeskFlow.nsi tests/test_firewall_onboarding.py tests/test_gui_preferences.py tests/test_release_packaging.py docs/TWO-PC-CHECK.md`
> Changes from completed Plans 005 and 006 are expected. Reconcile their live
> interfaces before proceeding; unrelated drift is a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: `005-detect-effective-firewall-conflicts.md`,
  `006-add-transactional-conflict-repair.md`
- **Planned at**: revision `75e29dc`, 2026-08-04

## Why this matters

The backend repair is useful only when the GUI explains the conflict, collects
informed consent, and starts Server mode after verified success. Packaged
installation must use the same effective-policy result so an old DeskFlow
block cannot make installation silently unusable.

## Current state

- `app/firewall_onboarding.py:95-221` coordinates one install operation through
  an injected elevation runner and requires successful reinspection before
  continuation.
- `app/firewall_onboarding.py:227-273` constructs a fixed elevated install
  command with `subprocess.list2cmdline` and Windows `runas`.
- `app/gui.py:563-702` offers configure/start-without/cancel for non-ready
  states. A confirmed conflict must instead offer Repair and start or Cancel.
- `installer/DeskFlow.nsi:181-187` invokes install and then inspect. Preserve
  mandatory installer consent and rollback ordering.
- `tests/test_gui_preferences.py:287-367` is the Server-start choice exemplar;
  extend it rather than introducing live GUI or firewall dependencies.
- `tests/test_release_packaging.py:185-321` statically guards installer consent,
  helper calls, rollback, and forbidden broad firewall behavior.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused onboarding/GUI | `.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding tests.test_gui_preferences -v` | all pass |
| Packaging tests | `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -v` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |

## Scope

**In scope**:

- `app/firewall_onboarding.py`
- `app/gui.py`
- `installer/DeskFlow.nsi`
- `tests/test_firewall_onboarding.py`
- `tests/test_gui_preferences.py`
- `tests/test_release_packaging.py`
- `docs/TWO-PC-CHECK.md`
- `docs/plans/windows-firewall-onboarding/004-review-and-validate-firewall-release.md`
- `docs/plans/windows-firewall-onboarding/README.md` for status/dependencies

**Out of scope**:

- Block matching and backend transaction internals completed in Plans 005-006.
- Public access, profile reclassification, ping, or reachability probes.
- SmartScreen reputation and Authenticode signing.
- Mouse, clipboard, or file-transfer changes.

## Steps

### Step 1: Add a distinct elevated repair runner

Write failing coordinator tests first. Add a fixed repair elevation path that
differs from install only by the allowlisted helper operation. Keep the base
port as the only variable. The coordinator must choose install for
Missing/Stale and repair for a conflict.

UAC cancellation, helper failure, managed results, and success-with-failed-
reinspection must retain the existing no-start behavior. `on_ready` runs only
after Ready or Development reinspection.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding -v`
→ new routing/cancellation tests fail first, then all pass.

### Step 2: Implement the approved conflict consent UX

Write failing GUI tests first. For a conflict:

- render `Firewall: Connection blocked` in red with action `Repair`;
- show the approved explanation and exact executable/port/Private/LocalSubnet
  scope;
- in source mode, add the shared-Python warning;
- offer only Repair and start or Cancel;
- disable the relevant button while elevation runs;
- on Cancel or UAC decline, do not start Server mode;
- after verified success, start with the originally latched port/password;
- do not offer Start without setup for a confirmed block.

Every detected conflict offers the same consented Repair attempt. If Windows
policy refuses removal or effective reinspection still finds the conflict,
show administrator help and do not start. Public-only remains View help with
no automatic repair. Existing Missing/Stale behavior remains unchanged.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding tests.test_gui_preferences -v`
→ all focused tests pass.

### Step 3: Use effective repair during consented installation

Write failing static packaging tests first. Change the installer's consented
firewall setup call to the fixed repair operation so a local block targeting
the installed `DeskFlow.exe` cannot override the new allow rule. Preserve:

- firewall consent before file copy;
- No/close/silent/UAC refusal cancellation;
- helper verification before completed install metadata;
- rollback on helper or verification failure;
- removal of only DeskFlow-owned state on uninstall;
- no Public, Any-remote, port-only, shell, PowerShell, or `netsh` behavior.

The backend repair transaction owns restoration of pre-existing blocks if its
operation fails. The NSIS rollback continues to remove the partial DeskFlow
allow rule and installation files.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -v`
→ packaging tests pass and explicitly expect `repair --base-port 28903`.

### Step 4: Update owner-run diagnostics and final validation

Update `docs/TWO-PC-CHECK.md` with a local, non-mutating firewall-state check
and the controlled one-Server/one-Client three-port test. Do not include a ping
requirement. Amend Plan 004 so final acceptance depends on Plans 005-007 and
includes:

- create or identify a disposable local conflicting block for the exact test
  executable;
- confirm Connection blocked appears without a network probe;
- Cancel and UAC decline preserve the block and do not start;
- Repair removes the exact conflict, preserves unrelated rules, and creates
  the restricted allow;
- all three ports and the full secure session work afterward;
- Public profile remains blocked without reclassification.

Physical validation commands belong in the validation guide or handoff, not
as hardcoded personal IPs in application code or automated tests.

**Verify**:
`git diff --check -- docs/TWO-PC-CHECK.md docs/plans/windows-firewall-onboarding/004-review-and-validate-firewall-release.md`
→ no output.

### Step 5: Run full security and regression gates

Review the complete `75e29dc..HEAD` implementation against the approved
design. Confirm there is no probe and every mutation requires explicit
consent plus UAC.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

→ all commands exit 0. Then Plan 004 owns independent review and physical
acceptance before merge.

## Test plan

- Coordinator operation selection and reinspection gating.
- GUI conflict, source warning, Cancel, UAC decline, failure, and success.
- Static NSIS consent/repair/rollback/uninstall assertions.
- Full suite and controlled physical acceptance through Plan 004.

## Done criteria

- [ ] Conflict shows Repair; Public-only does not.
- [ ] Confirmed conflict never offers Start without setup.
- [ ] Source Python warning appears before mutation.
- [ ] Cancel and UAC decline change nothing and do not start.
- [ ] Successful reinspection is required before start.
- [ ] Installer uses consented transactional repair.
- [ ] No ping, network probe, Public access, or profile change exists.
- [ ] Focused and full tests pass; whitespace check is clean.
- [ ] Only in-scope files changed.

## STOP conditions

Stop if:

- The GUI would need policy-origin metadata to decide whether repair succeeded.
- The installer would remove a block before explicit firewall consent.
- Installer rollback conflicts with the backend's block restoration.
- Source-mode repair would occur without the shared-Python warning.
- Any path starts Server mode after Cancel, UAC decline, helper failure, or
  failed reinspection.
- A step requires a ping, Public exception, or network-category change.
- A focused test fails twice after a reasonable fix.

Write a handback describing the integration fork. Do not weaken consent,
rollback, or network scope.

## Maintenance notes

Keep GUI and installer repair semantics aligned with the same helper operation.
Any future helper or consent change requires repeating Plan 004's independent
review and physical Windows matrix.
