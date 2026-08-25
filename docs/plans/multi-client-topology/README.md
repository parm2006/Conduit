# Two-Client Multi-Monitor Conduit

This effort expands Conduit into one fixed Server hub with at most two simultaneous Clients, automatic physical-monitor groups, a compact validated topology editor, Server-owned graph input routing, one global newest clipboard item, and on-paste Server-relayed files. The plans derive from the [accepted design](../../superpowers/specs/2026-08-24-multi-client-topology-design.md) and [accepted structure outline](001-structure-outline.md), planned on `main` at `3d76acb3daa28e5dbc5331af4da93ca427317795`. Execute in the order below. Each executor must read its plan fully, run its drift check first, honor STOP conditions, keep the repository green, and update its row when done.

## Execution order and status

| Plan | Title | Effort | Depends on | Status |
|---|---|---:|---|---|
| [002](002-land-single-client-topology.md) | Land the single-Client multi-monitor topology editor | L | — | TODO |
| [003](003-land-two-client-sessions.md) | Keep two isolated Client sessions on existing ports | L | 002 | TODO |
| [004](004-land-graph-input-routing.md) | Route the Server-owned cursor across the active display graph | L | 002, 003 | TODO |
| [005](005-land-global-clipboard.md) | Synchronize one global newest clipboard item | L | 003, 004 | TODO |
| [006](006-land-file-relay-and-cluster-commands.md) | Relay file jobs and cluster commands through the Server | L | 003–005 | TODO |
| [007](007-land-atomic-apply.md) | Activate topology through one atomic cluster Apply | L | 002–006 | TODO |
| [008](008-prove-system-and-release.md) | Prove the three-PC system and release contracts | L | 002–007 | TODO |

Status values: TODO | IN PROGRESS | DONE | BLOCKED (one-line reason) | SUPERSEDED (one-line pointer).

## Dependency notes

- **002 → 003:** session fan-out needs stable machine/display identity, draft placement, and the compact editor contract.
- **002 + 003 → 004:** input routing needs a validated active graph and stable ready-session IDs.
- **003 + 004 → 005:** clipboard broadcast needs isolated data lanes; cursor location must not control copy eligibility.
- **003–005 → 006:** file jobs need session-owned file lanes, stable destination identity, and global offer revisions.
- **002–006 → 007:** atomic Apply composes already-tested topology, input, clipboard, file, and session pause/cleanup interfaces.
- **002–007 → 008:** system, package, firewall, and physical acceptance begins only after feature behavior is complete and green.

## Reconciliation log

- **2026-08-24:** Accepted the seven-slice structure at `3d76acb`; wrote Plans 002–008 for final review. Next executable plan: 002.

## Considered and rejected

- More than two active Clients — outside the bounded release and editor model.
- Direct Client-to-Client sockets — rejected because the Server remains trust, routing, clipboard, and file-relay authority.
- Extra ports or wider firewall rules — unnecessary; each of the existing three listeners retains per-session connections.
- Per-Client passwords — rejected setup complexity; shared password plus per-device pairing remains accepted.
- Clipboard FIFO/history — conflicts with the current non-blocking latest-wins contract and memory-only newest state.
- One giant implementation plan — rejected because it would mix topology, security, input, clipboard, files, Apply, and physical acceptance into an unreviewable landing.

## Deferred

- More than two Clients, WAN/internet relay, non-Windows support, custom Conduit machine names, direct Client transport, clipboard history, and manual rearrangement of monitors inside one Windows-detected machine group.
