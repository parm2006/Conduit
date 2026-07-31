# Windows Firewall Onboarding and Installer

This effort implements GitHub issue
[#10](https://github.com/parm2006/DeskFlow/issues/10) from
[the approved design](../../superpowers/specs/2026-07-28-windows-firewall-installer-design.md),
planned at revision `be44890`. It separates the pure/firewall privilege
boundary, GUI onboarding, release packaging, and independent acceptance so
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
| [004](004-review-and-validate-firewall-release.md) | Independently review and validate release | M | 001, 002, 003 | IN PROGRESS |

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

## Reconciliation log

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
