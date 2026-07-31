# Plan 004: Independently review and validate the firewall-enabled release

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback —
> do not improvise. When done, update this plan's status row in the
> effort README.
>
> **Drift check (run first)**:
> `git diff be44890 -- docs/plans/windows-firewall-onboarding/VALIDATION.md README.md`
> If in-scope files have changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Effort**: M
- **Risk**: HIGH
- **Depends on**: `001-build-firewall-core-and-helper.md`,
  `002-integrate-server-firewall-onboarding.md`,
  `003-package-executable-and-transactional-installer.md`
- **Planned at**: revision `be44890`, 2026-07-28

## Why this matters

Firewall and installer code crosses an administrative security boundary and
can leave persistent system state. Automated tests alone cannot prove UAC,
Windows Firewall, rollback, uninstall, and two-PC behavior. This final plan
requires a separate review-only agent plus physical validation before the
branch can be called ready.

## Current state

- GitHub issue [#10](https://github.com/parm2006/DeskFlow/issues/10) owns the
  feature requirements.
- The design is
  `docs/superpowers/specs/2026-07-28-windows-firewall-installer-design.md`.
- `docs/plans/security-revamp/VALIDATION.md` is the existing two-PC acceptance
  style: exact PowerShell commands separated by server and client.
- The user explicitly requires a separate agent used solely for code review
  after implementation. That agent must use the installed code-review skill,
  make no edits, and report findings to the primary agent.
- Plans 001-003 must already have clean focused and full automated gates plus
  built executable and installer artifacts.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Tree check | `git diff --check` | no output |
| Review diff | `git diff --check be44890..HEAD` | no output |
| Port reachability | `$serverIp = "192.168.86.87"; Test-NetConnection $serverIp -Port 28903` | `TcpTestSucceeded : True` on permitted Private LAN after confirming the laptop still owns that address |

## Scope

**In scope**:

- `docs/plans/windows-firewall-onboarding/VALIDATION.md`
- `README.md` only for corrections discovered during acceptance
- Review and validation of the complete `be44890..HEAD` change

**Out of scope**:

- Silent fixes to source code during review. Findings reopen the owning plan
  and require a failing test before a fix.
- Disabling firewall, antivirus, UAC, or managed policy.
- Enabling Public-profile access.
- Publishing releases, merging, or closing issue #10 without user approval.
- Asking the review-only agent to edit, commit, or broaden scope.

## Steps

### Step 1: Write the exact physical validation guide

Create `docs/plans/windows-firewall-onboarding/VALIDATION.md` with separate
Laptop/Server and Desktop/Client commands. Include:

- synchronizing the exact branch commit;
- recording pre-test firewall rules and installed files;
- clean install consent **Yes**;
- clean install consent **No** and close behavior on a disposable test
  installation attempt;
- UAC refusal;
- rule property inspection;
- reachability of all three ports;
- normal pairing and secure-lane connection;
- base-port change and repair;
- Public-profile non-application without changing a trusted network merely for
  the test;
- uninstall cleanup;
- rollback after a safely induced helper failure when practical;
- restoring the pre-test state.

Never instruct the tester to disable a security control.

**Verify**:
`git diff --check -- docs/plans/windows-firewall-onboarding/VALIDATION.md`
→ no output.

### Step 2: Run fresh automated and artifact gates

Run the complete source gate and the checked-in release build command from
Plan 003. Record exact observed test count and artifact names in the workstream
handoff, not in this immutable plan.

**Verify**:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

→ every command exits 0.

### Step 3: Spawn the required review-only agent

After implementation and automated gates are complete, the primary agent must
spawn exactly one separate agent whose sole task is to review
`be44890..HEAD`. Instruct it to:

- use the installed code-review skill;
- inspect firewall privilege boundaries, helper parsing, COM rule scope,
  consent cancellation, installer rollback, uninstall cleanup, package
  licensing, secrets, logging, and test adequacy;
- report only actionable findings with file and line references;
- make no edits, commits, issue changes, or system changes.

Do not mark the feature ready while that agent is running.

**Verify**: receive the agent's final review report and record whether it found
Critical, Important, or no actionable issues.

### Step 4: Resolve review findings test-first

For each accepted finding, reopen the owning implementation plan, write a
failing regression test, implement the narrow fix, and rerun focused plus full
gates. If a finding changes the approved security/consent architecture, stop
and ask the user instead.

If code changes after the review, send the final diff back to the same
review-only agent for a follow-up review. Do not spawn a second reviewer unless
the first agent is unavailable.

**Verify**:
`.\venv\Scripts\python.exe -m unittest discover -s tests -q`
→ all tests pass after all accepted findings.

### Step 5: Run two-PC acceptance

Execute `VALIDATION.md` on the physical laptop and desktop. Confirm:

- **No**, consent close, and installer elevation refusal leave no completed
  installation or DeskFlow firewall rule;
- **Yes** creates one exact executable-/ports-/Private-/LocalSubnet-scoped
  rule;
- ports are reachable only under the intended Private-LAN conditions;
- pairing, control, data, and file lanes still work;
- stale port configuration repairs only after consent;
- uninstall removes the DeskFlow rule and installed files.

Record PASS/FAIL with commands and observed results in a new append-only
handoff. Do not claim unrun checks.

**Verify**: every required validation row is PASS, or the branch remains active
with explicit failed rows.

### Step 6: Final project bookkeeping

When and only when all gates pass:

- update issue #10 with verified results and remaining manual limitations;
- create a new firewall-onboarding handoff and repoint `handoffs/index.md`;
- leave issue closure, branch push/PR, merge, and release publication for
  explicit user direction.

**Verify**:
`git status --short --branch`
→ only intentional tracked work remains.

## Test plan

- Fresh full suite and release build after implementation.
- Separate review-only agent with code-review skill.
- Regression tests for every accepted reviewer finding.
- Physical two-PC installer/firewall/connection/uninstall matrix.
- No test or validation step disables UAC, firewall, antivirus, or policy.

## Done criteria

- [ ] Full source and packaging gates pass freshly
- [ ] Required independent review-only agent completed
- [ ] No unresolved Critical or Important review finding remains
- [ ] Consent **No**, close, and elevation refusal cancel installation cleanly
- [ ] Exact rule contract verified on Windows
- [ ] Normal three-lane DeskFlow operation passes on two PCs
- [ ] Port repair and uninstall cleanup pass
- [ ] GPL/source/third-party notices are present in installed artifacts
- [ ] New append-only handoff records exact results

## STOP conditions

Stop if:

- The review-only agent finds a design-level security flaw.
- Installer refusal or failure leaves files, shortcuts, uninstall metadata, or
  firewall rules.
- Any test requires disabling a security control.
- The rule applies to Public profile, Any remote address, any executable, or
  ports outside the selected three-port range.
- Packaged distribution lacks GPL source availability or required dependency
  notices.
- Physical results cannot distinguish a firewall problem from network
  isolation.
- A step fails twice after a reasonable investigation.

Write a handback with evidence. Do not weaken the security or consent contract.

## Maintenance notes

Keep the review agent independent and read-only. Future installer or firewall
changes should repeat this review and two-PC matrix because unit tests cannot
fully model UAC, local policy merge, endpoint security products, or installer
rollback on every Windows configuration.
