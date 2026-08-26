# Two-Client Multi-Monitor Conduit

This effort expands Conduit into one fixed Server hub with at most two simultaneous Clients, automatic physical-monitor groups, a compact validated topology editor, Server-owned graph input routing, one global newest clipboard item, and on-paste Server-relayed files. The plans derive from the [accepted design](../../superpowers/specs/2026-08-24-multi-client-topology-design.md) and [accepted structure outline](001-structure-outline.md), planned on `main` at `3d76acb3daa28e5dbc5331af4da93ca427317795`. Execute in the order below. Each executor must read its plan fully, run its drift check first, honor STOP conditions, keep the repository green, and update its row when done.

## Execution order and status

| Plan | Title | Effort | Depends on | Status |
|---|---|---:|---|---|
| [002](002-land-single-client-topology.md) | Land the single-Client multi-monitor topology editor | L | — | DONE |
| [003](003-land-two-client-sessions.md) | Keep two isolated Client sessions on existing ports | L | 002 | DONE |
| [004](004-land-graph-input-routing.md) | Route the Server-owned cursor across the active display graph | L | 002, 003 | DONE |
| [005](005-land-global-clipboard.md) | Synchronize one global newest clipboard item | L | 003, 004 | DONE |
| [006](006-land-file-relay-and-cluster-commands.md) | Relay file jobs and cluster commands through the Server | L | 003–005 | DONE |
| [007](007-land-atomic-apply.md) | Activate topology through one atomic cluster Apply | L | 002–006 | DONE |
| [008](008-prove-system-and-release.md) | Prove the three-PC system and release contracts | L | 002–007, 010–011 | BLOCKED (disconnect recovery and DPI grid repair) |
| [009](009-add-emergency-cursor-return-shortcut.md) | Add Ctrl+Space, Space emergency cursor return | S | 008 | TODO |
| [010](010-stop-routing-and-reset-topology.md) | Stop routing on Client loss and rebuild topology through Reset | L | 007 | IN PROGRESS |
| [011](011-fix-dpi-grid-rendering.md) | Keep the seven-by-four topology grid fitted at every Windows DPI | M | 010 | TODO |

Status values: TODO | IN PROGRESS | DONE | BLOCKED (one-line reason) | SUPERSEDED (one-line pointer).

## Dependency notes

- **002 → 003:** session fan-out needs stable machine/display identity, draft placement, and the compact editor contract.
- **002 + 003 → 004:** input routing needs a validated active graph and stable ready-session IDs.
- **003 + 004 → 005:** clipboard broadcast needs isolated data lanes; cursor location must not control copy eligibility.
- **003–005 → 006:** file jobs need session-owned file lanes, stable destination identity, and global offer revisions.
- **002–006 → 007:** atomic Apply composes already-tested topology, input, clipboard, file, and session pause/cleanup interfaces.
- **002–007 → 008:** system, package, firewall, and physical acceptance begins only after feature behavior is complete and green.
- **008 → 009:** finish the current physical reliability gate before adding a convenience shortcut to the same input-routing path.
- **007 → 010:** disconnect recovery must preserve the atomic Apply transaction while adding a persistent safety latch and authoritative Reset.
- **010 → 011:** both repairs touch the compact editor; land the state/lifecycle repair before changing its drawing geometry.
- **010 + 011 → 008:** repeat physical acceptance only after disconnect recovery and high-DPI rendering are green.

## Reconciliation log

- **2026-08-26:** Physical testing exposed two release blockers: a lost bridge Client leaves the active input graph routable, and disconnect/Cancel can raise `KeyError` after color state diverges from draft cells, blanking the editor. Added Plans 010–011 from the accepted [disconnect/Reset design](../../superpowers/specs/2026-08-26-disconnect-routing-reset-design.md); Plan 008 is blocked until both land. Next: 010.
- **2026-08-24:** Accepted the seven-slice structure at `3d76acb`; wrote Plans 002–008 for final review. Next executable plan: 002.
- **2026-08-25:** Implemented Plan 002 on `multimonitor`; 620 tests, compileall, and `diff --check` pass. Physical two-PC validation remains before marking DONE.
- **2026-08-25:** Completed Plan 002 physical acceptance. The user confirmed repeated Client connections, fixed-size topology toasts, Client and Server extended-monitor discovery, grouped topology cells, cross-monitor pointer movement, clipboard/file behavior, and reconnect placement. The fresh landing gate passes with 622 tests, compileall, and `diff --check`. Plan 003 is now active.
- **2026-08-25:** Completed Plan 003 on `multimonitor`. Two authenticated Client bundles now coexist on the existing control, data, and file ports; each session owns its lanes, identity, display inventory, draft placement, color, timeout, and cleanup. A third candidate is purple and bounded to one 15-second Server decision, with explicit replace/reject behavior and color inheritance only after successful Apply. Removed the Server singleton compatibility path and migrated the two adjacent file-lane lifecycle/security fixtures that exercised it. The landing gate passes with 648 tests, compileall, `diff --check`, and no Server-side `SessionCoordinator`, `_active_session`, or `session_offer` references. Plan 004 is now active.
- **2026-08-25:** Completed Plan 004 locally on `multimonitor`. `InputRouter` now owns the one roaming Server cursor, resolves only active full-edge mappings, targets ready sessions by stable machine identity, relays Client-to-Client transitions through the Server without a Server cursor hop, scales native motion by destination resolution, rejects stale topology/session events, and returns to Server-primary center on destination loss. Client edge reports release injected keys and mouse buttons before acknowledgement; replacement and disconnect paths notify the router. Migrated adjacent topology and file-offer fixtures from the removed scalar destination flag. The landing gate passes with 660 tests, compileall, `diff --check`, and no runtime `switching_to_client`, `_active_edge_side`, or `layout_position` references. Plan 005 is now active.
- **2026-08-25:** Completed Plan 005 locally on `multimonitor`. `ClipboardHub` is the memory-only Server authority for receive-order cluster revisions, newest ordinary snapshots, file-offer metadata, ready endpoint fan-out, reconnect sequence domains, and Apply delivery barriers. Copies from any machine use independent non-blocking latest-wins senders; authenticated session identity overrides wire claims, Clients reject stale cluster revisions, and file offers share global ordering without carrying file bytes. Apply success and rollback both resume delivery and converge on the newest pending item. The landing gate passes with 80 focused tests, 678 total tests, compileall, and `diff --check`. Plan 006 is now active.
- **2026-08-25:** Completed Plan 006 locally on `multimonitor`. `ClusterFileRouter` latches immutable offer/source/destination/manifest identity for all six directed machine pairs, relays encrypted frames through the existing Server file listener without staging uninvolved Client-to-Client bytes, applies bounded frame-boundary backpressure during pause, and scopes progress/cancellation to the two involved endpoints. Reload, shutdown, and background commands target both ready sessions best-effort with input release before fan-out and local cleanup despite partial failure. Authenticated control and file callback identity now overwrites spoofable wire claims. The landing gate passes with 54 focused cluster tests, all 211 file tests, 689 total tests, compileall, and `diff --check`. Plan 007 is now active.
- **2026-08-25:** Completed Plan 007 locally on `multimonitor`. Apply now snapshots the exact ready Client set, targets per-session candidate layouts, requires exact-version prepare and commit acknowledgements from every participant, persists before Server installation, and rolls acknowledged participants back on timeout, disconnect, send, persistence, or install failure. Input is released and centered before clipboard delivery and file scheduling pause; shutdown never reopens paused services. Missing rollback acknowledgements disconnect only inconsistent sessions. Reconnected sessions remain unroutable until a fresh successful Apply, two-Client display rescans wait for both inventories, and Apply/Cancel notices target both Client-primary identification toasts. The focused gate passes with 97 tests, the full suite with 703 tests, and compileall plus `diff --check` are clean. Plan 008 is now active.
- **2026-08-25:** Completed Plan 008's automated and development-package gates locally. A real-TLS system seam binds two full three-lane Client bundles, performs atomic Apply, global clipboard ordering, Client-to-Client file-frame relay, cluster command fan-out, and third-candidate timeout. The unchanged three-listener/firewall boundary, diagnostics, documentation, packaging discovery, restricted helper, NSIS installer, and packaged startup smoke pass. The build ran 708 total tests; the final focused gate runs 105 tests. `VALIDATION.md` records sanitized evidence and makes clear the dirty-checkout artifacts are development-only. Plan 008 remains IN PROGRESS solely for the one-Server/two-physical-Client acceptance matrix.
- **2026-08-26:** Closed the final pre-physical gaps with automatic one-second display-inventory monitoring, session-scoped clipboard sequence domains, distinct Server disconnect/display warnings, bounded toast copy, and clarified physical test messages. Physical display changes update only the draft and leave active routing untouched until Apply; retired sessions cannot overwrite a reconnect's clipboard sequence domain. The real-TLS clipboard system test waits on observed Server receipt and endpoint delivery instead of assuming cross-socket scheduling order and passed 20 repeated runs. Two independent functionality/messaging audits found no remaining high-confidence blocker. The rebuilt development package passes 717 tests and a four-second packaged startup/display-monitor smoke. Plan 008 remains IN PROGRESS only for physical acceptance.
- **2026-08-26:** Queued Plan 009 as a deferred, Server-authoritative Ctrl+Space, Space recovery shortcut. It starts only after Plan 008 closes and does not change the current physical-test build.

## Considered and rejected

- More than two active Clients — outside the bounded release and editor model.
- Direct Client-to-Client sockets — rejected because the Server remains trust, routing, clipboard, and file-relay authority.
- Extra ports or wider firewall rules — unnecessary; each of the existing three listeners retains per-session connections.
- Per-Client passwords — rejected setup complexity; shared password plus per-device pairing remains accepted.
- Clipboard FIFO/history — conflicts with the current non-blocking latest-wins contract and memory-only newest state.
- One giant implementation plan — rejected because it would mix topology, security, input, clipboard, files, Apply, and physical acceptance into an unreviewable landing.

## Deferred

- More than two Clients, WAN/internet relay, non-Windows support, custom Conduit machine names, direct Client transport, clipboard history, and manual rearrangement of monitors inside one Windows-detected machine group.
