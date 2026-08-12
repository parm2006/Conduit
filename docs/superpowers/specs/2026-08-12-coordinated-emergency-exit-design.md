# Coordinated Emergency Exit Design

## Goal

In DeskFlow 5.1, `Ctrl+Shift+Alt+Esc` closes DeskFlow on both machines in the
active authenticated session. This is the hotkey's only behavior. It no longer
merely disconnects a peer, restores local input, or leaves the server running.

If DeskFlow has no connected peer, the hotkey closes the local application.
DeskFlow cannot close an unreachable peer after the control connection has
already failed.

## Current behavior

The GUI, server, and client each create a global hotkey monitor. Their emergency
callbacks currently perform overlapping disconnect and input-release work. The
GUI callback restores its window instead of closing it. Concurrent callbacks
can race, and an endpoint callback can disconnect the control lane before the
GUI can notify the peer.

## Design

The GUI owns application shutdown. Server and client hotkey monitors delegate
emergency exit to the same GUI coordinator instead of independently
disconnecting.

The coordinator uses an idempotent shutdown guard. On its first invocation it:

1. releases captured or injected input through the existing endpoint shutdown
   paths;
2. sends one `shutdown_app` message over the authenticated control lane when a
   peer is connected;
3. schedules local GUI shutdown on the Tkinter thread; and
4. runs the existing `on_close()` lifecycle, which stops the server,
   disconnects all lanes, stops background monitors, and destroys the window.

Both GUIs register a control-lane callback for `shutdown_app`. The callback
enters the same guarded coordinator but does not echo another shutdown message.
It schedules GUI work with `after(0, ...)`; network and hotkey threads never
call Tkinter destruction directly.

There is no preference, confirmation dialog, server-only mode, retry loop, or
acknowledgement protocol. The feature always attempts coordinated application
exit.

## Trust boundary

`shutdown_app` travels only through the established control lane. The network
layer dispatches application messages only after TLS authentication and session
binding, so unauthenticated candidates cannot invoke GUI shutdown.

The message affects only the connected DeskFlow process. It does not terminate
other processes or shut down Windows.

## Failure behavior

Local shutdown must not wait indefinitely for the peer. DeskFlow attempts the
single control message before closing its lanes. If sending fails because the
peer is already unreachable, DeskFlow records a safe diagnostic and closes the
local application. The remote process cannot be controlled without a working
authenticated channel.

Repeated hotkey events, duplicate monitor callbacks, a simultaneous hotkey on
both machines, and a peer message racing with local shutdown all converge on
the same one-shot shutdown path.

## Verification

Automated tests will prove:

- a server-side hotkey sends `shutdown_app` and closes the local GUI;
- a client-side hotkey sends `shutdown_app` and closes the local GUI;
- receipt closes the peer without echoing the message;
- duplicate and simultaneous callbacks execute local shutdown once;
- disconnected operation closes the local GUI without waiting;
- application shutdown stops an active server before destroying the GUI; and
- existing reload and daemon hotkeys retain their behavior.

The full automated suite and release packaging checks must pass. Because this
changes a global escape hatch across two Windows machines, release validation
must use the same versioned executable on both machines and confirm initiation
from each role.

## Out of scope

- Shutting down Windows or unrelated processes.
- Closing DeskFlow instances outside the active connection.
- A configurable server-only emergency mode.
- Retrying shutdown after the authenticated connection is gone.
- Changing `Ctrl+Shift+Alt+R` or `Ctrl+Shift+Alt+B`.
