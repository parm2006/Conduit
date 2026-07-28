# Plan 002: Integrate consent-based firewall onboarding into Server mode

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff be44890 -- app/firewall_onboarding.py app/gui.py app/preferences.py tests/test_firewall_onboarding.py tests/test_gui_preferences.py`
> If in-scope files have changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: `001-build-firewall-core-and-helper.md`
- **Planned at**: revision `be44890`, 2026-07-28

## Why this matters

Users currently see only a connection timeout when Windows blocks a correctly
listening DeskFlow Server. This plan makes firewall state visible in Server
mode, provides an informed UAC-backed configuration action, and prevents a
port change from silently leaving a stale or over-broad rule.

## Current state

- `app/gui.py:246-383` builds a fixed `400x600` CustomTkinter window with
  Server and Client tabs. The Server tab has port, password, layout, and start
  controls but no firewall status.
- `app/gui.py:416-476` validates a general port, constructs
  `DeskFlowServer`, and starts it immediately.
- `app/gui.py:33-39` accepts ports through 65535, but DeskFlow needs base port
  plus two lanes; the valid base-port ceiling is 65533.
- `app/preferences.py:31-55` persists server ports through 65535 and must be
  aligned with the three-lane ceiling.
- `tests/test_gui_preferences.py:135-274` builds `DeskFlowGUI` with
  `__new__`, small widget doubles, and patched server dependencies. Match this
  style instead of opening real windows.
- Plan 001 supplies `FirewallRuleSpec`, firewall inspection states, the Windows
  backend, and the restricted helper entry point.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Coordinator tests | `.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding -q` | all onboarding state/elevation tests pass |
| GUI tests | `.\venv\Scripts\python.exe -m unittest tests.test_gui_preferences -q` | all GUI and port tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |

## Scope

**In scope**:

- `app/firewall_onboarding.py`
- `app/gui.py`
- `app/preferences.py`
- `tests/test_firewall_onboarding.py`
- `tests/test_gui_preferences.py`

**Out of scope**:

- `app/firewall.py`, `app/windows_firewall.py`, and `app/firewall_helper.py` —
  Plan 001 owns rule semantics and OS mutation.
- Build and installer files — Plan 003 owns packaged consent and rollback.
- Client connection, TLS, pairing, input, clipboard, and transfer behavior.
- Automatically changing a Windows network from Public to Private.

## Steps

### Step 1: Build the onboarding coordinator test-first

Write failing `tests/test_firewall_onboarding.py` cases for a coordinator that:

- converts inspection states into concise label text, color, action label, and
  safe explanation;
- distinguishes packaged `DeskFlow.exe` from source `python.exe`;
- requests elevation only after explicit consent;
- passes only a validated base port to the elevated runner;
- refreshes after success, UAC cancellation, helper failure, and policy denial;
- continues a pending server start only when the refreshed rule is ready;
- supports an explicit start-without-setup result without claiming readiness;
- never mutates while a port value is merely being edited.

Use injected backend, consent callback, elevation runner, and UI scheduler
interfaces. Verify RED, then add the smallest implementation in
`app/firewall_onboarding.py`.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding -q`
→ all tests pass.

### Step 2: Correct base-port validation test-first

Add failing preference and GUI tests for base ports `1`, `5000`, `65533`,
`65534`, and `65535`. Server and Client fields both represent the base of
three consecutive lanes and must reject values above 65533 with an actionable
message.

Update `app/preferences.py` and the narrow parsing call sites in `app/gui.py`.
Do not change unrelated integer validation.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_gui_preferences -q`
→ all tests pass.

### Step 3: Add Server-tab status and action controls test-first

Extend widget doubles and add failing GUI tests for:

- Ready, Setup required, Repair required, Development rule, Managed by
  administrator, and Unavailable rendering;
- Configure, Repair, and View help action text;
- no firewall control or prompt on the Client tab;
- a valid port edit scheduling inspection without immediate mutation;
- the fixed window still fitting all controls without clipping.

Add a compact Server-tab firewall row below the port field. Keep status
selectable only where the existing status panel already supports selection;
do not add a second verbose diagnostics panel.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_gui_preferences -q`
→ all tests pass.

### Step 4: Gate Server start with informed consent test-first

Add failing tests for:

- Ready state starting immediately with existing behavior unchanged;
- missing/stale state offering **Configure and start**, **Start without
  setup**, and **Cancel**;
- Configure and start waiting for helper success and matching re-inspection;
- UAC cancellation not starting the server;
- Start without setup starting while preserving a visible warning;
- development mode showing the `python.exe` scope warning;
- Public profile never being enabled or reclassified;
- repeated clicks not starting duplicate elevation or server operations.

Integrate the coordinator into `DeskFlowGUI.start_server` without moving
network or certificate logic into the coordinator. Extract a narrow
`_start_server_after_firewall(port, password)` continuation if needed to keep
the consent path testable.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding tests.test_gui_preferences -q`
→ all tests pass.

### Step 5: Run the complete gate

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

→ every command exits 0.

## Test plan

- Write each coordinator and GUI behavior test before production changes.
- Match `tests/test_gui_preferences.py:135-274` for GUI construction without a
  real window.
- Use fake backend and runner objects; no test may show UAC or change firewall
  state.
- Add regression coverage proving existing successful server-start status,
  pairing code, role persistence, and port-conflict messaging remain intact.
- **Verify**:
  `.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding tests.test_gui_preferences -q`
  → all pass.

## Done criteria

- [ ] Every firewall state has tested Server-tab copy and action
- [ ] Configure/repair requires consent and validates the refreshed rule
- [ ] UAC cancellation never starts the pending server
- [ ] Start without setup remains explicit and visibly warned
- [ ] Base ports above 65533 are rejected and not persisted
- [ ] Full suite, compile, and diff checks pass
- [ ] No files outside the in-scope list are modified

## STOP conditions

Stop if:

- Plan 001's model/helper API differs materially from this plan's current-state
  assumptions.
- The only way to test consent is to open real windows or UAC.
- CustomTkinter cannot fit the status/action controls without a user-visible
  layout decision.
- GUI integration requires changing Client/TLS/session behavior.
- A step's verification fails twice after a reasonable fix attempt.
- A new flow would silently change the Windows network profile.

Write a handback rather than inventing a broader UI or security policy.

## Maintenance notes

Future server-lane count or port-layout changes must update the shared rule
model and GUI validation together. Reviewers should scrutinize duplicate-click
handling, GUI-thread scheduling, UAC cancellation, source-mode warning text,
and preservation of existing Server start behavior.
