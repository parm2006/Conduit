# Windows Firewall Onboarding and Installer

This effort implements GitHub issue
[#10](https://github.com/parm2006/DeskFlow/issues/10) from
[the original approved design](../../superpowers/specs/2026-07-28-windows-firewall-installer-design.md)
and the approved
[effective-conflict repair extension](../../superpowers/specs/2026-08-04-firewall-conflict-repair-design.md).
Plan 008 additionally implements the approved
[automatic installer upgrade design](../../superpowers/specs/2026-08-05-automatic-installer-upgrades-design.md).
The original plans were written at `be44890`; Plans 005-007 were written at
`75e29dc`. The effort separates the pure/firewall privilege boundary, GUI
onboarding, release packaging, conflict repair, and independent acceptance so
each change remains reviewable and the repository stays green.

Execute in the order below unless dependencies say otherwise. Each executor
must read the plan fully, honor its STOP conditions, and update its row when
done.

## Execution order and status

| Plan | Title | Effort | Depends on | Status |
|---|---|---|---|---|
| [001](001-build-firewall-core-and-helper.md) | Build firewall core and restricted helper | L | — | DONE |
| [002](002-integrate-server-firewall-onboarding.md) | Integrate Server-mode onboarding | L | 001 | DONE |
| [003](003-package-executable-and-transactional-installer.md) | Package executable and transactional NSIS installer | L | 001, 002 | DONE |
| [005](005-detect-effective-firewall-conflicts.md) | Detect effective firewall conflicts without probes | L | 001, 002 | DONE |
| [006](006-add-transactional-conflict-repair.md) | Add transactional conflict repair | L | 005 | DONE |
| [007](007-integrate-conflict-repair-ux.md) | Integrate repair into Server mode and installer | L | 005, 006 | DONE |
| [008](008-build-safe-automatic-installer-upgrades.md) | Build safe automatic installer upgrades | L | 003, 006, 007 | DONE |
| [004](004-review-and-validate-firewall-release.md) | Independently review and validate release | M | 001, 002, 003, 005, 006, 007 | IN PROGRESS |

Status values: TODO | IN PROGRESS | DONE | BLOCKED (one-line reason) |
SUPERSEDED (one-line pointer)

## Dependency notes

- **001 → 002**: the GUI must consume one tested firewall model and helper
  protocol rather than invent its own rule semantics.
- **001 → 003**: the installer calls the packaged restricted helper and
  verifies the same rule contract.
- **002 → 003**: packaged Server mode must expose the completed onboarding
  experience before installer acceptance.
- **001-003 → 004**: independent review and physical validation apply only to
  the completed code and built artifacts.
- **005 → 006**: repair uses the same tested conflict model and effective
  policy inspection rather than reimplementing rule matching.
- **005-006 → 007**: GUI and installer integration begin only after read-only
  detection and transactional mutation are independently green.
- **005-007 → 004**: final review and physical acceptance must cover the
  effective-policy extension before the release can close.

- **003, 006, and 007 to 008**: automatic replacement must preserve the
  packaged transaction and invoke the already-reviewed effective repair from a
  provably fresh executable.
- **008 to 004**: final physical acceptance must use the fresh automatic
  upgrader and repeat effective firewall and three-lane validation.

## Reconciliation log

- **2026-08-05**: Plan 008 completed test-first. Valid packaged installs now
  upgrade after consent, allowlisted partial remnants recover without
  execution, unknown content fails closed, running DeskFlow stops before old
  removal, and direct NSIS builds cannot embed stale output. The independent
  follow-up review returned APPROVE after silent-uninstall warning handling was
  fixed. Full supported release build passed with 458 tests. Next: Plan 004
  owner-run physical validation using the fresh ignored setup artifact.

- **2026-08-05**: Physical setup exposed two release gaps: valid installs
  could not upgrade automatically, and a newly named installer embedded a
  stale pre-repair executable. The user accepted no old-binary restoration
  after removal and approved allowlisted partial-remnant replacement. Plan 008
  added and started; next: installer/build TDD and fresh artifact assembly.

- **2026-08-05**: Independent review requested changes before physical
  acceptance. Follow-up work makes consented setup create only a Private rule
  while Public is active, keeps Server mode blocked there, makes uninstall
  continue with a warning when policy denies cleanup, and preserves remote
  scope so provably loopback-only rules are never disabled. Arbitrary CIDRs
  remain conservative because the app does not enumerate adapters or guess a
  future client address.

- **2026-08-05**: Plan 007 completed. Confirmed conflicts expose only Repair
  and Cancel, source Python scope is disclosed, Cancel/UAC failure cannot start
  Server mode, and fresh setup can progress from Missing to Conflict without
  deleting the correct allow rule. The consented NSIS repair is the final
  fallible install step and compiled successfully with NSIS. Full suite: 446
  tests passed. Next: Plan 004 independent review and physical acceptance.

- **2026-08-05**: Plan 007 integration uses Repair-only conflict consent,
  prevents start-without-setup for confirmed blocks, and makes the internally
  verified repair the installer's final fallible step. The installer does not
  perform a second firewall rollback outside the exact-object transaction.

- **2026-08-05**: Plan 006 completed with exact-object transactional disable,
  missing-allow creation, effective reinspection, and rollback that re-enables
  every changed block or reports `rollback_failed`. The elevated helper accepts
  only `repair --base-port <integer>` and derives the executable internally.
  Full suite: 438 tests passed. Next: 007.

- **2026-08-05**: User approved the safe Plan 006 fork. Repair will disable the
  exact enumerated conflicting COM objects and re-enable them on rollback; it
  will never delete a conflicting block by friendly name. Plans and design
  amended before privileged code changes.

- **2026-08-04**: Plan 006 stopped before mutation. `INetFwRules.Remove`
  accepts only the rule's friendly `Name`, and the Microsoft API contract does
  not document that value as a unique identity. Name-based deletion therefore
  cannot satisfy the approved exact-rule removal invariant. Safe fork awaiting
  approval: transactionally disable the exact enumerated COM rule object and
  re-enable it on rollback, rather than delete by name.

- **2026-08-04**: Plan 005 completed. Effective inspection now suppresses
  Ready/Development for overlapping executable/Private/TCP block rules,
  reports Public-only active profiles, ignores unreadable irrelevant rules,
  and exposes safe read-only GUI states without probes or mutation. Full suite:
  429 tests passed. Next: 006.

- **2026-08-04**: Plan 005 handback found that `INetFwRule` does not expose
  policy-store origin. [Memo](memo-rule-origin-limitation.md) rejects adding a
  second CIM boundary: all exact conflicts offer consented Repair, and clean
  effective reinspection remains the success authority. Plans 005-007 amended;
  next: resume 005.

- **2026-08-04**: Physical diagnosis found a local `Python` TCP block that
  overrode DeskFlow's correct allow rule. Removing only that block restored a
  controlled one-Server/one-Client three-lane session. Approved design
  extension adds static effective-policy inspection and consented repair with
  no ping or Public access. Plans 005-007 added; next: 005.

- **2026-07-28**: Initial four-plan effort created at `be44890`. Installer
  choice changed from Inno Setup to NSIS after official-license review.
  Mandatory firewall refusal and rollback requirements incorporated before
  implementation. Next: 001.
- **2026-07-28**: Plan 001 completed. Added a pure exact-rule contract,
  fakeable Windows Firewall COM boundary, allowlisted helper protocol, and
  pre-GUI helper dispatch. Full suite: 365 tests passed. Next: 002.
- **2026-07-28**: Plan 002 completed. Added Server-tab firewall state,
  explicit configure/start-without/cancel flow, UAC cancellation handling,
  development-mode warnings, and the three-lane base-port ceiling. Full
  suite: 380 tests passed. Next: 003.
- **2026-07-28**: Plan 003 source work completed through the real packaged
  executable smoke test. Added release metadata, fail-closed license notices,
  checked-in one-file spec, mandatory-consent/rollback NSIS source, build
  script, and release documentation. Full suite: 399 tests passed;
  `DeskFlow.exe` helper exits correctly. Blocked only on installing official
  NSIS so `makensis` can compile and validate the real installer.
- **2026-07-28**: Official NSIS 3.12 compiled the transactional installer
  successfully after correcting installer callback and build-directory
  assumptions. Plan 004 is now in progress for independent code review and
  owner-run physical acceptance; no installer has been executed on a user
  system during this work.

## Considered and rejected

- **Firewall disclaimer only**: rejected because users receive unexplained TCP
  timeouts when Windows blocks inbound traffic.
- **Windows prompt only**: rejected because prompts can be dismissed, disabled,
  or unavailable to non-admin users.
- **Port-only firewall rule**: rejected because any listener could use it.
- **Inno Setup**: rejected to avoid its current commercial-license expectation;
  NSIS officially permits any use.
- **Silent installer mode**: rejected because it cannot collect the required
  informed firewall consent.
- **Automatic Public-profile access or profile reclassification**: rejected as
  an unnecessary security expansion.

## Deferred

- Trusted Authenticode signing requires the owner's certificate and remains an
  optional build hook until credentials are supplied.
- GitHub release publication, PR creation, merge, and issue closure require
  explicit user direction after acceptance.
