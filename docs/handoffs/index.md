# Handoff Index

New sessions: read [PROJECT.md](PROJECT.md) first for the stable product, architecture, safety, and workflow context; then use this index to find the latest dated state.

## Threads
| thread | direction | status | head file |
|---|---|---|---|
| deskflow-hardening | Historical reliability and file-transfer baseline retained on `main` | superseded | [2026-07-12-1819-phase-4-file-transfer.md](2026-07-12-1819-phase-4-file-transfer.md) |
| deskflow-restart | Rebuild selected security first, then consider background, branding, and later behavior from `main` | active | [2026-07-25-2335-background-daemon-mode-v4.3s.md](2026-07-25-2335-background-daemon-mode-v4.3s.md) |
| deskflow-file-paste | Test restored `60db8b7`, record single-file behavior, FIFO queueing dropped | active | [2026-07-25-1004-connection-diagnostics-and-clipboard-dedup.md](2026-07-25-1004-connection-diagnostics-and-clipboard-dedup.md) |

## Workstreams
| slug | thread | status | owns | latest handoff |
|---|---|---|---|---|
| reliability-cleanup | deskflow-hardening | active | Clipboard/network reliability, followed by repository cleanup and feature planning | [2026-07-11-1054-reliability-cleanup.md](2026-07-11-1054-reliability-cleanup.md) |
| phase-4-file-transfer | deskflow-hardening | superseded | Main-branch file-paste baseline retained; later branch experiments are discarded and tracked by the restart TODO | [2026-07-13-1656-restart-from-main-todo.md](2026-07-13-1656-restart-from-main-todo.md) |
| security-hardening | deskflow-hardening | superseded | Security prototype branch is discarded; rebuild requirements are tracked by the restart TODO | [2026-07-13-1656-restart-from-main-todo.md](2026-07-13-1656-restart-from-main-todo.md) |
| restart-from-main-todo | deskflow-restart | active | Decision-complete rebuild list and known failed approaches for a new branch from `main` | [2026-07-13-1656-restart-from-main-todo.md](2026-07-13-1656-restart-from-main-todo.md) |
| security-revamp | deskflow-restart | active | Secure identity/pairing, session-bound TLS lanes, encrypted staging, cancellation, security UI, and final two-PC acceptance | [2026-07-25-1004-connection-diagnostics-and-clipboard-dedup.md](2026-07-25-1004-connection-diagnostics-and-clipboard-dedup.md) |
| stable-file-paste-rebuild | deskflow-file-paste | active | Restored `5f97c81` paste baseline, retained approved fixes | [2026-07-25-1004-connection-diagnostics-and-clipboard-dedup.md](2026-07-25-1004-connection-diagnostics-and-clipboard-dedup.md) |
| reload-connection | deskflow-restart | active | Reload Connection hotkey (Ctrl+Shift+Alt+R), emergency exit, session reset & overlay cleanup | [2026-07-25-1225-reload-connection-hotkey.md](2026-07-25-1225-reload-connection-hotkey.md) |
| background-daemon-mode | deskflow-restart | active | Synchronized Background Daemon Mode (Ctrl+Shift+Alt+B), reload invisibility, pre-disconnect unhide & release 4.3s | [2026-07-25-2335-background-daemon-mode-v4.3s.md](2026-07-25-2335-background-daemon-mode-v4.3s.md) |

## Experiment ledger
| date | slice / axis value | thread | verdict | number | baseline | handoff |
|---|---|---|---|---|---|---|
