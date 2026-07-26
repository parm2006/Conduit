# DeskFlow - Project Context

## Purpose

DeskFlow is a Windows-first, two-computer wireless KVM application. A user runs it on both PCs, starts one as the server/host, connects the other as the client, and then moves one physical mouse and keyboard across the screen boundary as though the computers were adjacent monitors. It also synchronizes ordinary clipboard content and supports remote Windows Explorer/Desktop file paste.

The intended experience is local and low-friction: paired computers communicate over the same LAN, mouse and keyboard control stay responsive, text/screenshots can replace one another using latest-wins semantics, and a file transfer begins only after the user presses `Ctrl+V` on the destination.

## System shape

- Windows 10/11 and Python 3.10+ are the supported environment. Both computers run the same CustomTkinter application in different roles.
- The server detects screen-edge crossings, captures local input while control is remote, and forwards mouse/keyboard events. The client injects those events and signals when control returns.
- Control, ordinary clipboard data, and file bytes use separate network lanes. TLS protects transport; the file lane additionally uses a one-use session token bound to the live control connection's certificate identity.
- Text, images, HTML, and RTF use a replaceable latest-wins clipboard snapshot. Files do not: a remote paste creates an immutable manifest and source snapshot, transfers bounded chunks, verifies SHA-256 hashes, and exposes Windows virtual files to Explorer.
- File data may finish network staging while Explorer is waiting on a duplicate-name prompt. DeskFlow tracks Explorer-consumed byte ranges, accepts its performed-drop notification, and treats release of an incomplete last stream as cancellation after a reopen-safe grace period.

## Architecture map

| area | key paths | responsibility |
|---|---|---|
| App roles and UI | `app/server.py`, `app/client.py`, `app/gui.py` | Connection lifecycle, role-specific routing, input overlay, status UI |
| Input control | `app/input_handler.py`, `app/input_geometry.py` | Edge switching, capture/injection, coordinate handling, emergency recovery |
| Clipboard | `app/clipboard_handler.py`, `app/latest_wins_sender.py` | Rich clipboard encoding/injection and active-plus-replaceable-pending scheduling |
| File paste orchestration | `app/file_transfer/paste_service.py`, `handshake.py`, `hotkey.py`, `publisher.py` | Manifest-on-paste handshake, physical `Ctrl+V` interception, Windows virtual-file publication |
| File transfer engine | `app/file_transfer/sender.py`, `receiver.py`, `transport.py`, `staging.py` | Authenticated bounded streaming, staging, cancellation, and end-to-end verification |
| File policy | `app/file_transfer/validation.py`, `compression.py`, `rate_control.py` | Safe paths, adaptive compression, and latency-aware bandwidth policy |
| Tests and plans | `tests/`, `docs/plans/phase-4-file-transfer/`, `docs/plans/security-revamp/` | Automated contracts, manual Windows checks, staged implementation and security decisions |

## Safety and correctness invariants

- `Ctrl+Alt+Shift+Escape` on the server must always release forwarded modifiers, disconnect remote control, and restore local input.
- Mouse/keyboard, control messages, and ordinary clipboard traffic outrank file traffic; file work must not block their lanes.
- Never serialize or transmit source absolute paths. Accept only validated relative paths and prevent traversal, reserved Windows names, case-insensitive collisions, oversized frames, and unbounded allocation.
- Publish received files only after their declared size and SHA-256 hash verify. Partial or cancelled staging must never appear as a completed destination file.
- Preserve existing text/image/HTML/RTF clipboard wire formats. File transfers must not occupy or lock the ordinary clipboard after paste begins.
- Never persist an unencrypted private key or plaintext DeskFlow staging artifact. Peer trust commits only after approval, authentication, and all session lanes bind; every secondary lane must match the control certificate and consume its own purpose-bound one-use token.
- Treat remote cut as copy. Never delete source files remotely.
- Keep the server taskbar visible and avoid UI changes that steal focus or trap mouse/keyboard control.

## Working conventions

- Run: `run.bat` on each computer.
- Test: `.\venv\Scripts\python.exe -m unittest discover -s tests -v`
- Compile check: `.\venv\Scripts\python.exe -m compileall -q app tests`
- Global hotkeys: `Ctrl+Alt+Shift+B` (Daemon Mode), `Ctrl+Alt+Shift+R` (Reload Connection), `Ctrl+Alt+Shift+Escape` (Emergency Exit).
- File-transfer baseline: [`docs/plans/phase-4-file-transfer/README.md`](../docs/plans/phase-4-file-transfer/README.md)
- Security design and plans: [`docs/plans/security-revamp/README.md`](../docs/plans/security-revamp/README.md)
- Dated implementation state: [`handoffs/index.md`](index.md)

## Known structural limitations

- Explorer can block its folder UI while consuming a virtual file because the Python COM data object does not implement `IDataObjectAsyncCapability`. Multi-destination paste UX is deferred to [GitHub issue #1](https://github.com/parm2006/DeskFlow/issues/1); selecting all desired files before pasting is the supported workflow.
- Explorer does not negotiate `IDataObjectAsyncCapability` for this clipboard-paste path on the tested Windows configuration. Outcome handling therefore uses performed-drop notifications, full-read coverage, and guarded incomplete-stream release detection rather than a native lifecycle bridge.
- File paste is Windows-specific. Internet relay, cross-platform paste, remote deletion/cut semantics, automatic execution, and arbitrary application upload targets are out of current scope.
- The accepted security boundary covers LAN transport, clipboard payloads in transit, local identity/trust material, and DeskFlow-owned partial/completed staging. Endpoint compromise, Internet relay, source deletion, remote execution, and code signing remain out of scope. Background/daemon behavior must remain separate from security acceptance; see the latest `security-revamp` handoff in [index.md](index.md).

## Resume protocol

1. Read this file.
2. Read `handoffs/index.md`.
3. Read the latest handoff for the relevant workstream.
4. Read its linked plan or issue, if any.
5. Confirm branch, commit, working tree, and current tests before editing.
