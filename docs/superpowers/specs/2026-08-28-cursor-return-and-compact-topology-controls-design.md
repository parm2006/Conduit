# Cursor Return and Compact Topology Controls Design

## Goal

Give the Server operator one fixed recovery shortcut and replace the topology editor's wordy action buttons with compact symbols that fit the seven-by-four grid.

The user has moved this work ahead of the remaining Plan 008 physical matrix. The change must preserve every active connection, the applied topology, clipboard state, and file job.

## Accepted behavior

### Cursor return

- Hold Ctrl, press and release Space, then press Space again within 750 milliseconds.
- Detect the chord only in the Server-owned global hotkey monitor. It must work while the shared cursor is on the Server or either Client.
- Require two distinct Space presses. Ignore key auto-repeat.
- Cancel a partial chord when Ctrl is released, another key is pressed, the interval expires, or the monitor stops.
- Release forwarded keys and mouse buttons before restoring Server ownership.
- Move the cursor to the center of the Server's current primary display.
- Preserve a paused router state during Apply. The shortcut may release and center input but must not resume routing.
- When no topology router exists, release captured input and center using the Server's stored primary-screen dimensions.
- Do not disconnect a Client, rebuild topology, send a network command, show a toast, or change clipboard/file state.

### Compact topology controls

- The right action button always displays `✓`. It performs the existing action: initial Apply before the first successful topology transaction, then Reset for the rest of that Server lifetime.
- The left action button always displays `✕`. It performs the existing Cancel action.
- The symbols do not change when the hidden Apply/Reset lifecycle changes. That lifecycle remains session-only behavior used to decide what `✓` does.
- Both buttons remain keyboard-focusable and expose their current action through concise hover text: `Apply layout`, `Reset layout`, or `Cancel changes`.
- Use text glyphs and existing fonts. Do not add image assets, packages, ports, settings, or persisted state.

## Design

### Hotkey detector

Extend `GlobalHotkeyMonitor` with an optional Server callback, an injected monotonic clock, and a 750-millisecond interval. Keep the two-tap state under the monitor's existing lock. Normalize left and right Ctrl, require a Space release between taps, clear the partial state before dispatch, and run the callback on a daemon thread like the existing global commands.

### Router recovery

Add one idempotent `InputRouter.return_to_server_primary()` operation. It uses the router's existing release and restore primitives under its re-entrant lock. Remote key/button release happens first, then local ownership and cursor restoration. A paused router remains paused.

`ConduitServer` alone wires the new callback. Its no-router fallback releases injected/captured input before centering locally. The GUI and Clients must not register another callback.

### Editor controls

Keep the existing apply and cancel commands and button positions. Narrow the buttons for their single glyphs. Store the Apply/Reset lifecycle separately from the displayed glyph so presentation cannot alter transaction behavior. A small local tooltip helper may describe the action on hover without adding a dependency; it must follow the button lifecycle and disappear on leave, click, or widget destruction.

## Error and race handling

- Dispatch at most one return callback for one completed two-tap sequence.
- A repeated recovery request is safe and leaves the cursor centered locally.
- Recovery during Apply cannot bypass the routing pause.
- A destination disappearing during recovery uses the existing best-effort release path and still restores local control.
- Invalid or failed topology transactions leave the hidden action as Apply until the first success. Later failures leave it as Reset.

## Testing

- Deterministic hotkey tests use an injected clock and cover timing boundaries, repeat suppression, releases, unrelated keys, monitor stop, left/right Ctrl, and existing chords.
- Router tests cover local, remote, paused, repeated, multi-monitor-primary, and held-input release ordering.
- Server tests cover callback ownership, no-router fallback, and connection preservation.
- Editor/GUI tests assert `✓` and `✕`, compact sizing, tooltip action text, Apply-to-Reset lifecycle, failed transaction behavior, and Server restart reset.
- Run focused suites, the full suite, compileall, and `diff --check`. Physical acceptance checks the shortcut from the Server primary/secondary and both Clients, including held input.

## Non-goals

- Configurable shortcuts, Client-side triggers, new network messages, or shortcut toasts.
- Changing topology validation, transaction semantics, or disconnect safety.
- Changing clipboard, file transfer, firewall, port, or pairing behavior.
