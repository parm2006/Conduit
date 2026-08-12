# Clipboard and Explorer Reliability

This effort repairs the clipboard and Explorer failures recorded in
[the release handoff](../../handoffs/2026-08-10-1618-release-stabilization.md).
The functional v5 routing/OLE baseline is commit `8d17fdf`, pushed to
`origin/firewallfix`. The focused cancellation design is
[2026-08-11-explorer-toast-cancellation-design.md](../../superpowers/specs/2026-08-11-explorer-toast-cancellation-design.md),
planned at `85dccb9`.

Execute in the order below. Each executor must read the plan fully, honor STOP
conditions, and update its row. At a physical gate, create the named
`validate_<feature>.md` and wait for owner evidence.

## Execution order and status

| Plan | Title | Effort | Depends on | Status |
|---|---|---|---|---|
| [001](001-reproduce-and-trace-small-clipboard.md) | Reproduce and trace small clipboard paths | M | — | DONE |
| [002](002-repair-client-to-server-ordinary-clipboard.md) | Repair client-to-server ordinary clipboard | M | 001 | SUPERSEDED — physical matrix passed; no repair required |
| [003](003-authoritative-file-offers-and-native-paste.md) | Route file paste from the latest clipboard owner to the active screen | L | 001 | DONE — v5 accepted at `8d17fdf` |
| [007](007-repair-virtual-clipboard-owner-restoration.md) | Restore and retire the virtual clipboard owner safely | M | 003 | DONE — v5 accepted at `8d17fdf` |
| [005](005-unify-explorer-toast-terminal-lifecycle.md) | Give each Explorer paste one terminal result | L | 003, 007 | TODO |
| [008](008-correlate-explorer-paste-session.md) | Correlate and retire only the active Explorer paste session | L | 005 | TODO |
| [009](009-integrate-and-validate-cancellation.md) | Make toast and Explorer cancellation agree on both peers | M | 005, 008 | TODO |
| [004](004-bind-paste-to-offer-and-destination.md) | Bind a paste to one offer and destination | M | 003, 007; scheduled after 009 | TODO |
| [006](006-measure-and-repair-large-transfers.md) | Measure and repair large transfers | L | 004, 009 | TODO — last |

Status values: TODO | IN PROGRESS | DONE | BLOCKED | SUPERSEDED.

## Dependency notes

- **003 → 007**: the correct route must select the virtual owner before owner
  identity and retirement can be safe.
- **003 + 007 → 005**: the user accepted v5 routing and owner retirement before
  cancellation lifecycle work began.
- **005 → 008**: popup/session policy requires one immutable receiver terminal
  result and typed OLE outcome.
- **005 + 008 → 009**: two-peer toast integration and physical validation need
  both terminal truth and a correlated destination session.
- **009 → 004 (execution order, not data dependency)**: the owner prioritized
  the observed cancellation failure. Plan 009 binds cancellation to an already
  accepted job and latched destination HWND/path; Plan 004 later adds clipboard
  offer identity to the manifest protocol.
- **004 + 009 → 006**: capacity evidence is valid only after stale manifest and
  unfinished cancellation paths are closed. Large transfers remain last.

## Reconciliation log

- **2026-08-11**: Owner approved the focused Explorer/toast cancellation
  design. The former monolithic Plan 005 was split into terminal truth (005),
  correlated Windows session (008), and two-peer physical acceptance (009).
  Plan 004 follows 009 by owner priority; Plan 006 remains last. Next: 005.
- **2026-08-11**: v5 routing and OLE retirement accepted and saved as
  `8d17fdf`; all 514 tests and the release build passed before push. Plans 003
  and 007 are DONE.
- **2026-08-10**: Physical routing validation isolated stale server-side paste
  authority. The approved revision/source/destination model replaced the
  boolean route patches; the production OLE tuple defect became Plan 007.
- **2026-08-10**: Owner reported all eight small ordinary-clipboard rows
  passed. Plan 002 was superseded; ordinary clipboard required no repair.

## Considered and rejected

- OLE outcomes alone: rejected because Explorer may close a conflict prompt
  without a callback that ends DeskFlow's toast.
- Popup title or translated button matching: rejected because it can target an
  unrelated or localized Explorer window.
- Global Escape, broad Explorer termination, or foreground-window guessing:
  rejected because cancellation must affect only the latched paste.
- Replace Explorer with a DeskFlow copy engine and conflict dialog: rejected as
  a separate file manager and a much larger security boundary.
- Restore `e53a4d9` wholesale: rejected because the handoff records broad
  regressions. Narrow tests or evidence may be reused; its implementation is
  never merged wholesale.
- Raise transfer timeouts before measurement: rejected because it masks the
  failing phase.

## Deferred

- Plan 004 adds immutable offer identity to manifest messages after the
  accepted-job cancellation lifecycle passes.
- Plan 006 measures 100/300/500 MiB files and one folder only after Plans 004
  and 009 pass.
- FIFO expansion, saved-host behavior, pairing, installer/firewall work, and
  unrelated input crossing remain outside this effort.
