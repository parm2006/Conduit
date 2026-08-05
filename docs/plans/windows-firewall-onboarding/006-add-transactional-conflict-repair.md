# Plan 006: Add narrowly scoped transactional firewall conflict repair

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff 75e29dc -- app/firewall.py app/windows_firewall.py app/firewall_helper.py tests/test_firewall.py tests/test_windows_firewall.py tests/test_firewall_helper.py`
> If Plan 005 has landed, its changes are expected. Reconcile the current
> state against Plan 005's completed model before proceeding. Any unrelated
> drift is a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: `005-detect-effective-firewall-conflicts.md`
- **Planned at**: revision `75e29dc`, 2026-08-04

## Why this matters

Detection alone still leaves users copying administrative PowerShell. This
plan adds a fixed elevated repair operation that removes only verified local
conflicts, installs the restricted DeskFlow allow rule, and restores the old
blocks if the operation cannot finish.

## Current state

- `app/windows_firewall.py:130-168` installs the allow rule and removes partial
  DeskFlow state on failure, but it does not snapshot or repair other rules.
- `app/firewall_helper.py:31-84` allowlists install, inspect, and remove. It
  derives the executable image internally and never accepts a caller-supplied
  program path. Preserve this privilege boundary.
- `tests/test_windows_firewall.py:27-58` records fake adds/removals and is the
  mutation-test exemplar.
- `tests/test_firewall_helper.py:117-145` rejects arbitrary helper arguments;
  extend this deny-list coverage for repair.
- The approved transaction is defined in
  `docs/superpowers/specs/2026-08-04-firewall-conflict-repair-design.md`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused backend | `.\venv\Scripts\python.exe -m unittest tests.test_firewall tests.test_windows_firewall -v` | all pass |
| Focused helper | `.\venv\Scripts\python.exe -m unittest tests.test_firewall_helper tests.test_runtime_logging -v` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |

## Scope

**In scope**:

- `app/firewall.py`
- `app/windows_firewall.py`
- `app/firewall_helper.py`
- `tests/test_firewall.py`
- `tests/test_windows_firewall.py`
- `tests/test_firewall_helper.py`
- `tests/test_runtime_logging.py`
- `docs/plans/windows-firewall-onboarding/README.md` for status only

**Out of scope**:

- `app/gui.py` and `app/firewall_onboarding.py` — Plan 007 owns consent and
  GUI orchestration.
- `installer/DeskFlow.nsi` — Plan 007 owns installer integration.
- Automatic repair of managed rules.
- Firewall disablement, Public access, shell commands, or arbitrary helper
  parameters.

## Steps

### Step 1: Define lossless local-rule snapshots

Write failing pure and fake-COM tests before production changes. Add an
immutable snapshot shape containing every COM property required to recreate a
removed conflict under the same unique identity and policy semantics. Include
at least name, description, grouping, enabled, direction, action, protocol,
ports, application, profiles, remote addresses, edge traversal, and any other
field the live rule requires for equivalent restoration.

The snapshot is internal mutation data. It must not be serialized, logged, or
returned to the GUI.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_windows_firewall -v`
→ snapshot round-trip tests fail before implementation, then pass while
preserving every tested field.

### Step 2: Implement transactional backend repair

Write one failing test per transaction boundary, then implement a backend
method with the intent `repair(spec) -> FirewallInspection`:

1. reinspect/collect current exact conflicts from the live policy object;
2. snapshot all collected conflicts;
3. prepare rollback before mutation;
4. remove them by unique internal identity;
5. install or replace the exact DeskFlow allow rule;
6. reinspect effective policy;
7. return success only for Ready or Development with zero conflicts;
8. on failure, remove partial DeskFlow state and restore every removed block.

Tests must cover a removal rejected by policy, failure during the second of
multiple removals, during allow creation, during verification, and during
restoration.
Unrelated rules must remain byte-for-byte equivalent in the fake model.

If rollback is incomplete, return a distinct safe Unavailable reason and do
not mask it as the original failure.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_windows_firewall -v`
→ every transaction and rollback test passes.

### Step 3: Add the fixed helper operation

Write failing helper tests first. Extend `_parse_request` to accept exactly:

```text
repair --base-port <integer>
```

It must derive the executable through `current_process_executable`, construct
`FirewallRuleSpec`, call only `backend.repair(spec)`, emit the existing safe
state/reason protocol, and use existing stable exit-code semantics.

Reject caller-supplied rule names, paths, profiles, addresses, protocols,
commands, or port ranges. Preserve pre-GUI reserved dispatch in `run.py`; no
new top-level command surface is needed.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_firewall_helper tests.test_runtime_logging -v`
→ all helper and dispatch tests pass.

### Step 4: Review the privilege boundary and run full gates

Inspect the final diff for input-to-mutation paths. The only caller-controlled
repair value may be a validated integer base port. Executable, rule matching,
profiles, protocol, and remote scope must be derived internally.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
rg -n "shell=True|os\.system|netsh|Disable-NetFirewall|Public" app/firewall.py app/windows_firewall.py app/firewall_helper.py
```

→ compile and tests pass, whitespace check is empty, and the search reveals no
new unsafe mutation path. Existing descriptive `Public` constants are allowed;
review each match rather than suppressing the check.

## Test plan

- Snapshot fidelity and collection tests.
- Successful single/multiple conflict repair.
- Policy-rejected removal and rollback.
- Rollback at every mutation boundary, including incomplete rollback.
- Exact helper allowlist and rejection matrix.
- Full suite after focused red-green cycles.

## Done criteria

- [ ] Every production behavior began with an observed failing test.
- [ ] Repair cannot accept a caller-supplied executable or rule identity.
- [ ] Only exact, local, overlapping conflicts are removed.
- [ ] Policy-rejected and unrelated rules are preserved.
- [ ] Every failed transaction restores prior block state or reports explicit
  rollback failure.
- [ ] Focused and full gates pass.
- [ ] No file outside Scope is modified.

## STOP conditions

Stop if:

- The COM API does not expose enough properties to restore a removed rule
  faithfully.
- Unique rule identity cannot safely distinguish duplicate display names.
- Windows reports successful removal while effective reinspection still shows
  a block and the transaction cannot restore prior local mutations.
- Rollback requires a shell, exported policy file, registry edit, or firewall
  disablement.
- The helper would need caller-supplied path or rule data.
- A transaction test fails twice after a reasonable fix.

Write a handback describing the missing COM property or transaction fork. Do
not weaken rollback or broaden the helper protocol.

## Maintenance notes

Treat changes to matching, snapshot, removal, and restoration as one security
boundary. Review them together whenever Windows Firewall support changes.
