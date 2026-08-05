# Plan 005: Detect effective Windows Firewall conflicts without network probes

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff 75e29dc -- app/firewall.py app/windows_firewall.py app/firewall_onboarding.py app/gui.py tests/test_firewall.py tests/test_windows_firewall.py tests/test_firewall_onboarding.py tests/test_gui_preferences.py`
> If in-scope files have changed since this plan was written, compare the
> "Current state" facts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: `001-build-firewall-core-and-helper.md`,
  `002-integrate-server-firewall-onboarding.md`
- **Planned at**: revision `75e29dc`, 2026-08-04

## Why this matters

The GUI currently reports Ready or Development rule after validating only
DeskFlow's named allow rule. Windows gives an overlapping explicit block rule
precedence, so the label can be green while all inbound connections fail.
This plan makes inspection describe effective local policy without pinging or
contacting another machine.

## Current state

- `app/firewall.py:24-119` owns the platform-independent states, exact allow
  rule contract, and comparison. `FirewallInspection` carries only state and a
  safe reason code.
- `app/windows_firewall.py:90-128` fetches only
  `DeskFlow Server - Private LAN`; it neither enumerates block rules nor reads
  the active profile mask.
- `app/firewall_onboarding.py:40-84` maps every existing state to compact GUI
  text. Match this data-driven display pattern.
- `app/gui.py:530-560` refreshes and renders inspection without performing
  network I/O. Preserve that boundary.
- `tests/test_windows_firewall.py:11-58` provides fake COM rule, collection,
  and policy objects. Extend these fakes to support enumeration and current
  profiles rather than touching the live firewall.
- The approved contract is
  `docs/superpowers/specs/2026-08-04-firewall-conflict-repair-design.md`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused model/backend | `.\venv\Scripts\python.exe -m unittest tests.test_firewall tests.test_windows_firewall -v` | all focused tests pass |
| Focused display/GUI | `.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding tests.test_gui_preferences -v` | all focused tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |

## Scope

**In scope**:

- `app/firewall.py`
- `app/windows_firewall.py`
- `app/firewall_onboarding.py`
- `app/gui.py`
- `tests/test_firewall.py`
- `tests/test_windows_firewall.py`
- `tests/test_firewall_onboarding.py`
- `tests/test_gui_preferences.py`
- `docs/plans/windows-firewall-onboarding/README.md` for status only

**Out of scope**:

- `app/firewall_helper.py` — mutation belongs to Plan 006.
- `installer/DeskFlow.nsi` — packaged repair integration belongs to Plan 007.
- Live firewall changes or network probes.
- Public-profile allow rules or network-category changes.

## Steps

### Step 1: Define conflict and active-profile behavior in the pure model

Write failing tests first in `tests/test_firewall.py`. Add the smallest pure
types and helpers needed to represent an observed block rule, active profile
state, safe conflict count, and repairability. Required behavior:

- exact executable comparison uses the existing normalized Windows-path rule;
- TCP and protocol Any can conflict; UDP alone cannot;
- blank, `*`, `Any`, single ports, comma lists, and inclusive ranges parse
  deterministically;
- overlap with any of the three derived ports is a conflict;
- disabled, outbound, non-Private, different-executable, and disjoint-port
  rules do not conflict;
- malformed potentially relevant expressions return an indeterminate result
  that callers map to Unavailable rather than Ready;
- add distinct conflict and Public-only states plus a safe conflict count,
  without embedding private rule details in reason codes.

Keep parsing and matching platform-independent. Do not import COM, sockets, or
PowerShell.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall -v`
→ the new tests fail before production edits, then all pass after the minimal
model implementation.

### Step 2: Inspect the active policy store

Write failing backend tests first in `tests/test_windows_firewall.py`. Extend
the fakes to expose iterable rules, unique internal names, and
`CurrentProfileTypes`. Then update `WindowsFirewallBackend.inspect` so it:

1. validates the named allow rule;
2. reads active profiles without changing them;
3. enumerates enabled inbound block candidates;
4. maps COM fields into pure model objects;
5. returns conflict ahead of Ready/Development when an overlapping block
   exists;
6. returns one generic conflict result without guessing rule origin;
7. returns Public-only when no Private profile is active; and
8. maps unreadable relevant policy to safe Managed or Unavailable results.

Do not log or return full paths, GUIDs, policy dumps, or COM exception text.
Inspection must not mutate the fake or real rules collection.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_windows_firewall -v`
→ new tests fail for missing enumeration first, then all backend tests pass.

### Step 3: Render safe read-only GUI states

Write failing tests in `tests/test_firewall_onboarding.py` and
`tests/test_gui_preferences.py`. Add display entries:

- conflict: label `Connection blocked`, red, action `View help` in this
  read-only plan;
- Public-only: label `Blocked on Public network`, orange, action `View help`.

Plan 007 will change the conflict action to Repair after the helper exists.
Until then, starting Server with a conflict must not claim readiness.
The existing explicit Start without setup behavior may remain for this
intermediate commit, but it must keep a visible warning.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_onboarding tests.test_gui_preferences -v`
→ all focused tests pass with the new states covered.

### Step 4: Run the full non-mutating gate

Run the full suite and statically confirm no probe path was added. The new
inspection may use Windows Firewall COM only; it must not call `socket`,
`Test-NetConnection`, `ping`, PowerShell, or `netsh`.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
rg -n "Test-NetConnection|ping\.exe|subprocess.*ping|socket\.create_connection|netsh|New-NetFirewallRule" app/firewall.py app/windows_firewall.py app/firewall_onboarding.py
```

→ compile and tests pass, whitespace check is empty, and the final search has
no matches.

## Test plan

- Pure table-driven parsing and overlap tests in `tests/test_firewall.py`.
- Fake-COM enumeration, precedence, profiles, safe failures, and no-mutation
  tests in `tests/test_windows_firewall.py`.
- Complete display mapping and Server-start state tests in
  `tests/test_firewall_onboarding.py` and `tests/test_gui_preferences.py`.
- Full 413-test baseline plus new tests remains green.

## Done criteria

- [ ] New tests were observed failing before production changes.
- [ ] Ready/Development cannot coexist with a detected overlapping block.
- [ ] Public-only active policy never reports Ready.
- [ ] Inspection performs no mutation and no network probe.
- [ ] Focused and full commands pass.
- [ ] `git diff --check` produces no output.
- [ ] No file outside Scope is modified.

## STOP conditions

Stop if:

- The COM rules collection cannot be enumerated without elevation on the
  supported Windows versions.
- Active profile state cannot be read through the existing policy object.
- Correct remote-scope handling requires live adapter/IP enumeration.
- Conflict enumeration requires policy-origin metadata for read-only safety.
- More than safe state/count/repairability metadata must cross into the GUI.
- A focused test fails twice after a reasonable fix.

Write a handback describing the observed COM shape or design fork. Do not add
a probe, broaden the allow rule, or guess that the firewall is ready.

## Maintenance notes

Future rule-contract changes must update allow comparison and block-overlap
logic together. Keep inspection conservative: false Unavailable is preferable
to false Ready at this security boundary.
