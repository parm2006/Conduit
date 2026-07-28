# Plan 001: Build the firewall rule core and restricted elevated helper

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff be44890 -- app/firewall.py app/windows_firewall.py app/firewall_helper.py run.py tests/test_firewall.py tests/test_windows_firewall.py tests/test_firewall_helper.py tests/test_runtime_logging.py`
> If in-scope files have changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: none
- **Planned at**: revision `be44890`, 2026-07-28

## Why this matters

DeskFlow currently has no way to distinguish a missing Windows Firewall rule
from a working configuration, and no safe way to install or remove its own
rule. This plan creates the security boundary used by both the GUI and the
installer: a pure rule contract, a Windows COM backend, and an elevated helper
that cannot be turned into an arbitrary firewall or command runner.

## Current state

- `run.py:1-17` imports `app.gui` at module load and always starts the GUI.
  Helper dispatch must occur before that import so packaged helper operations
  do not initialize CustomTkinter, global hotkeys, or the main window.
- `app/dpapi.py:1-87` is the existing small Windows boundary. Match its
  platform checks, narrow custom exception, and direct API wrapper style.
- `app/safe_errors.py:1-16` exposes only error class names unless an exception
  is explicitly safe for users. Firewall errors must follow this convention.
- `requirements.txt:1-5` already requires `pywin32>=311`; do not add a second
  Windows API dependency.
- There are no `app/firewall.py`, `app/windows_firewall.py`, or
  `app/firewall_helper.py` modules.
- Existing security tests use `unittest`, real domain objects, and mocks only
  at OS/network boundaries; see `tests/test_security_identity.py:24-112`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused model tests | `.\venv\Scripts\python.exe -m unittest tests.test_firewall -q` | all firewall model tests pass |
| Focused backend tests | `.\venv\Scripts\python.exe -m unittest tests.test_windows_firewall -q` | all fake-COM backend tests pass; live firewall unchanged |
| Focused helper tests | `.\venv\Scripts\python.exe -m unittest tests.test_firewall_helper tests.test_runtime_logging -q` | helper and entry-point tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |

## Scope

**In scope**:

- `app/firewall.py`
- `app/windows_firewall.py`
- `app/firewall_helper.py`
- `run.py`
- `tests/test_firewall.py`
- `tests/test_windows_firewall.py`
- `tests/test_firewall_helper.py`
- `tests/test_runtime_logging.py`

**Out of scope**:

- `app/gui.py` and `app/preferences.py` — Plan 002 owns user interaction and
  port-entry behavior.
- `DeskFlow.spec`, `installer/`, and `scripts/` — Plan 003 owns packaging.
- Live Windows Firewall state — automated tests must never mutate it.
- TLS, pairing, session, input, clipboard, and file-transfer modules.

## Steps

### Step 1: Define the rule contract test-first

Write failing `tests/test_firewall.py` cases for:

- accepted base ports `1` and `65533`;
- rejected booleans, non-integers, `0`, and values above `65533`;
- exact range derivation `<base>-<base+2>`;
- normalized, case-insensitive executable comparison on Windows;
- required inbound/allow/TCP/Private/LocalSubnet/enabled/no-edge-traversal
  properties;
- ready, missing, stale, development, managed, and unavailable states;
- every single stale-property difference.

Verify RED for missing imports or missing behavior, then add the smallest
platform-independent model in `app/firewall.py`. Keep the rule comparison pure;
it must accept observed data rather than query Windows.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall -q`
→ all tests pass.

### Step 2: Implement a fakeable Windows COM backend test-first

Write failing `tests/test_windows_firewall.py` tests around injected policy and
rule factories. Cover:

- rule enumeration by one stable internal name;
- inspection mapping COM properties into the pure model;
- creation order required by `INetFwRule`;
- replacing only the DeskFlow-owned stable rule;
- install followed by full re-inspection;
- cleanup after add or verification failure;
- idempotent remove;
- access denied/policy errors mapped to managed state;
- other COM failures mapped to unavailable without private details.

Implement `app/windows_firewall.py` with `HNetCfg.FwPolicy2` and
`HNetCfg.FWRule` through pywin32. Do not invoke PowerShell, `netsh`, registry
tools, or shell strings. Keep COM imports lazy enough that the pure model tests
remain importable without touching Windows state.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_windows_firewall -q`
→ all tests pass and no real firewall rule is created.

### Step 3: Add the restricted helper protocol test-first

Write failing `tests/test_firewall_helper.py` cases for the wished-for helper
entry point:

- `install --base-port N`, `inspect --base-port N`, and `remove` succeed with
  an injected backend;
- missing/extra switches, executable-path arguments, arbitrary commands,
  profile switches, raw port ranges, and invalid base ports fail closed;
- the rule executable is derived from the running process, never CLI input;
- stable documented exit codes distinguish success, invalid request,
  configuration failure, and policy-managed failure;
- normal output contains only state and safe reason codes.

Implement `app/firewall_helper.py` with a fixed parser and dependency-injected
backend factory. No helper operation may accept rule identity, executable,
profile, remote scope, protocol, or shell text.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_helper -q`
→ all tests pass.

### Step 4: Dispatch helper mode before GUI import

Extend `tests/test_runtime_logging.py` first so helper arguments dispatch
without importing or starting the GUI, while an ordinary launch still
configures logging and calls `run_gui`.

Refactor `run.py` to expose a testable `main(argv=None)` and import `app.gui`
only on the GUI path. Use one reserved prefix such as
`--deskflow-firewall-helper`; do not interpret general command strings.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_helper tests.test_runtime_logging -q`
→ all tests pass.

### Step 5: Run the complete gate

Run compilation, all tests, and whitespace checks. Inspect the diff for any
live-firewall calls in tests and any log/output that includes full paths.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

→ every command exits 0.

## Test plan

- Add focused model, fake-COM, helper, and entry-point tests before production
  code.
- Use `tests/test_security_identity.py` as the OS-boundary test style and
  `tests/test_runtime_logging.py` for entry-point patching.
- Assert that tests receive injected fake policies and never call the live
  `HNetCfg.FwPolicy2` factory.
- **Verify**:
  `.\venv\Scripts\python.exe -m unittest tests.test_firewall tests.test_windows_firewall tests.test_firewall_helper tests.test_runtime_logging -q`
  → all pass.

## Done criteria

- [ ] `.\venv\Scripts\python.exe -m unittest discover -s tests -q` → all pass
- [ ] `.\venv\Scripts\python.exe -m compileall -q app tests run.py` → exit 0
- [ ] `git diff --check` → no output
- [ ] Helper accepts no arbitrary path, command, profile, or range
- [ ] Fake-COM tests prove the live firewall remains untouched
- [ ] No files outside the in-scope list are modified

## STOP conditions

Stop if:

- pywin32 cannot access `HNetCfg.FwPolicy2` and `HNetCfg.FWRule` on supported
  Windows versions.
- The COM API cannot represent exact executable, port range, Private profile,
  LocalSubnet, and edge-traversal restrictions together.
- Implementing the helper appears to require accepting an arbitrary executable
  path or command.
- A test would need to mutate the live firewall.
- A step's verification fails twice after a reasonable fix attempt.
- In-scope current state no longer matches this plan.

Write a handback describing the current state, desired outcome, and evidence;
do not substitute a broader firewall mechanism.

## Maintenance notes

Reviewers should scrutinize helper parsing, executable derivation, stable rule
identity, COM property order, cleanup after partial failure, and safe error
mapping. Any new configurable lane must still fit the three-port derivation or
return to design rather than silently broadening the rule.
