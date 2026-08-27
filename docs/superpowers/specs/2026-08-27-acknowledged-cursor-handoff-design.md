# Acknowledged Cursor Handoff and Nonblocking Input Design

**Status:** Approved direction; written specification pending final user review

**Date:** 2026-08-27

**Planned against:** `9f5dd34`

**Platform:** Windows 10 and Windows 11

## Summary

Conduit will treat cursor movement between machines as an acknowledged ownership transaction. Sending a `switch` packet will no longer prove that the destination owns the cursor. The destination Client must validate and activate the switch, then return a matching acknowledgement before the Server captures local input or forwards any input to that Client.

Conduit will also remove control-lane writes from Tk and input-hook callbacks. A bounded, per-session dispatcher will serialize keyboard and mouse messages on daemon workers. Mouse movement may coalesce while a worker is busy. Key and button events remain ordered and may never be silently dropped. A blocked Client cannot freeze the Server GUI, the router, the heartbeat teardown, or another Client's dispatcher.

## Observed failure and root cause

The physical reproduction disables a Client's Wi-Fi, then crosses into that Client's topology region before the six-second heartbeat timeout. Windows may accept the small `switch` write into its local TCP buffer even though the peer cannot receive it. `InputRouter` currently treats that local write as success, changes ownership to `RemoteClient`, and asks the GUI to show its full-screen CTk capture overlay.

Overlay motion runs on Tk's event thread. It calls `server.on_mouse_move()`, which calls the control lane's blocking `sendall()` while holding the router ownership lock. A stalled TLS write therefore blocks GUI events, cursor restoration, and lock-dependent cleanup. The full-screen `CTkToplevel` is the capture overlay, not a disconnect dialog.

Tag `v5.1.1` uses the same unacknowledged switch and blocking network primitives. Its simpler one-Client disconnect callback often recovers after heartbeat teardown closes the socket and hides the overlay, but the code does not guarantee this result. The multi-client implementation needs an explicit ownership contract.

## Goals

- Keep the Server cursor local until the selected Client confirms activation.
- Return the cursor to the Server primary center within a short, fixed deadline when the target fails to confirm.
- Stop and invalidate the failed session so it cannot retain a ghost Client slot.
- Keep Tk, pynput callbacks, router locks, and heartbeat teardown free of blocking network writes.
- Preserve key, button, scroll, and mouse ordering required by the current input contract.
- Isolate two Clients so one blocked lane cannot delay the other Client's queued input.
- Reuse the existing disconnect safety latch: a failed destination pauses all shared input until a successful topology Apply/Reset.
- Preserve clipboard, file-transfer, authentication, firewall, and topology validation behavior.

## Non-goals

- Change the six-second general heartbeat interval or timeout.
- Use frequent heartbeat packets as cursor-handoff acknowledgements.
- Add automatic graph rerouting around a failed Client.
- Repair toast behavior that the physical retest could not reproduce.
- Change the two-Client limit, connection password, ports, or TLS trust model.
- Implement Plan 009's recovery shortcut or Apply/Reset label lifecycle.

## Ownership transaction

### Transaction identity

Every inter-machine transition receives an unpredictable `handoff_id`. The identifier exists only for the life of one transition. Messages also carry the active topology version, source and destination machine/display identities, and the existing edge geometry.

The router accepts an acknowledgement only when all of these values match its current `Transitioning` state:

- handoff identifier;
- topology version;
- expected destination session;
- authenticated destination machine identity.

Late, duplicated, stale, and cross-session acknowledgements have no effect.

### Server-to-Client handoff

1. The Server detects a valid graph edge and changes the router from `LocalServer` to `Transitioning` under the router lock.
2. The router releases logical held-input state, creates a 750 ms deadline, and dispatches `switch` from a daemon worker without holding the router lock.
3. The Server keeps edge detection active, keeps the physical cursor visible, and does not show the capture overlay.
4. The Client validates the topology version and destination geometry, configures its return edges, moves its local cursor to the entry point, marks itself active, and sends `switch_ack` with the handoff identifier and topology version.
5. The Server validates the acknowledgement, cancels the deadline, starts keyboard/overlay capture, changes the router to `RemoteClient`, and updates clipboard destination routing.

No Server input packet may target the Client before step 5.

### Client-to-Client handoff

The source Client keeps the existing rule: release every injected key and button before reporting its graph edge. The Server retains its already-active capture overlay while the target handoff is pending, but drops new movement during the transition. A valid acknowledgement activates the target Client. Failure restores `LocalServer`, hides the overlay, and centers the Server cursor.

The transition deadline bounds the suppressed-input interval to 750 ms.

### Client acknowledgement failure

If the Client applies a switch but cannot send its acknowledgement, it immediately releases injected input, marks itself inactive, and stops Client edge reporting. It must not remain a hidden owner.

## Failure handling

The following events fail the current handoff:

- the initial `switch` send returns false or raises;
- the 750 ms acknowledgement deadline expires;
- the destination session disappears or changes identity;
- the active topology changes or routing suspension begins;
- the per-session input dispatcher reports a send failure or discrete-event overflow.

Failure performs this order:

1. Invalidate the handoff identifier so late acknowledgements cannot commit it.
2. Release held logical input and restore the Server primary center without waiting for the blocked sender.
3. Set the persistent Server routing-suspension latch.
4. Stop capture and edge routing; hide the overlay.
5. Close the failed session asynchronously. Existing session teardown removes its registry slot and draft machine.
6. Suspend surviving Clients and show the existing Server reset-required status and disconnect warning.

The operation is idempotent. Heartbeat, send failure, and timeout may report the same physical loss in any order.

## Nonblocking input dispatcher

### Ownership and isolation

The Server router owns a dispatcher with one daemon worker per ready Client session. Each worker is bound to one stable session identifier and resolves the current authenticated control lane before every write. Session replacement or disconnect stops accepting new work for the old identifier.

No worker holds the router lock while calling `send_message()`. The worker reports failure through a callback after releasing its own queue lock.

### Event classes

- **Mouse movement:** keep at most one pending movement record per session. Consecutive movement deltas coalesce by addition while preserving sign and scale. New movement never grows an unbounded queue.
- **Keys and mouse buttons:** store in one bounded FIFO and preserve exact press/release order. Queue exhaustion fails the destination instead of dropping an event that could leave input held.
- **Scroll:** preserve order with discrete input. Adjacent scroll records may coalesce only when no key or button event separates them.

The initial implementation uses a 256-record discrete queue. This is a safety bound, not a user setting.

### Router bookkeeping

The router records a key or button as held when the dispatcher accepts its event. It removes the held state when the corresponding release is accepted. A failure clears logical state and relies on Client suspension/disconnect cleanup to release injected state physically.

`forward_mouse_move`, `forward_scroll`, `forward_button`, and key forwarding return after enqueueing. They never wait for a socket write.

## Threading and lock order

The implementation preserves this order:

1. Router lock: inspect or change ownership and held-input state.
2. Dispatcher queue lock: enqueue or drain one session's records.
3. Network send lock: used only by the daemon worker inside `NetworkNode.send_message()`.

Code must never acquire these locks in reverse order. Network callbacks may request router failure handling only after `send_message()` returns and the network send lock is released. Tk callbacks schedule visual updates with `after(0, ...)` and never wait for a worker.

Heartbeat teardown remains independent. It may close a blocked socket and report disconnect while a sender is stalled.

## Protocol compatibility

This branch is a coordinated development build; every test machine must run the same commit. The change adds `handoff_id` to `switch` and adds `switch_ack`.

A Client receiving `switch` without a valid handoff identifier rejects it and stays inactive. A Server ignores acknowledgements without the exact current identifier and authenticated session metadata. This fail-closed behavior prevents mixed versions from capturing the cursor incorrectly.

## Diagnostics and UI

Diagnostics log:

- handoff identifier prefix;
- source and destination machine identity;
- topology version;
- start, acknowledgement, timeout, cancellation, and failure category;
- dispatcher session prefix, queue overflow, and send failure.

Logs omit IP addresses from topology UI messages, passwords, tokens, clipboard content, file content, and private filenames.

The Server shows no modal dialog during handoff. A failed destination uses the existing disconnect-warning toast and persistent reset-required status after the failed session closes. The full-screen capture overlay appears only after acknowledgement.

## Test strategy

### Transaction tests

- A blocking `switch` lane cannot block `handle_edge()`, the router lock, or the test's simulated GUI thread.
- A Server-to-Client transition stays `Transitioning` and never starts capture before acknowledgement.
- A valid acknowledgement commits `RemoteClient` exactly once.
- Wrong-session, wrong-topology, duplicate, and late acknowledgements are ignored.
- A 750 ms timeout restores the Server center, hides capture, latches routing off, and requests closure of the failed session.
- Client-to-Client timeout restores the Server instead of returning to either stale Client.
- Pause, topology replacement, and disconnect invalidate pending handoffs.

### Client protocol tests

- The Client sends `switch_ack` only after validating topology, configuring edges, activating, and moving to the requested entry point.
- Missing or invalid handoff identifiers leave the Client inactive.
- A failed acknowledgement send releases injected input and deactivates the Client.

### Dispatcher tests

- A blocked session worker does not block enqueueing, router state reads, GUI callbacks, heartbeat teardown, or another session worker.
- Mouse movements coalesce and the pending structure remains bounded.
- Key and button events retain FIFO order.
- Discrete queue overflow invokes failure and drops no accepted release silently.
- Send failure invokes failure once and rejects later work for that session.
- Session removal and application stop terminate acceptance without joining a blocked daemon worker.

### Regression and system tests

- The full two-Client real-TLS system test completes acknowledged Server-to-Client, Client-to-Client, and Client-to-Server transitions.
- Existing topology Apply, disconnect suspension, clipboard, file, global-hotkey, and security tests remain green.
- The security test continues to prohibit production traceback logging.

### Physical Windows tests

1. Arrange dual-monitor Client 2 at `(0,1)` and `(0,0)`, Client 1 at `(1,0)`, and Server at `(2,0)`.
2. Disable Client 1 Wi-Fi while the cursor is on the Server. Enter Client 1's edge before heartbeat timeout.
3. Confirm the cursor remains visible on the Server, returns to Server center within one second, and never shows the capture overlay for the dead Client.
4. Confirm the Server remains interactive, the global emergency keybind works, the session slot clears, and the reset-required warning appears.
5. Repeat after Client 1 already owns the cursor. Move continuously while disabling Wi-Fi; the GUI must remain responsive until heartbeat/send failure returns the cursor.
6. Repeat with Client 2 as the failed bridge while Client 1 owns the cursor.
7. Reconnect the failed Client, verify it can reclaim its slot, and confirm routing stays paused until a successful Reset.
8. Repeat 20 rapid crossings on live Clients to detect acknowledgement ordering or stale-timeout races.

## Acceptance criteria

- The Server never commits remote ownership from a local socket-write result alone.
- The capture overlay appears only after the destination acknowledges the current handoff.
- A dead or silent destination returns control to the Server within one second without waiting for the six-second heartbeat.
- No input or UI callback performs blocking network I/O.
- One blocked Client cannot freeze the other Client, the router, heartbeat teardown, the GUI, or global keybind handling.
- Failed destinations lose their session slot and require a successful topology Reset before routing resumes.
- Keys and buttons preserve order and cannot remain logically held after failure.
- All focused, system, full-suite, compile, security, and physical gates pass.
