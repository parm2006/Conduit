# Disconnect Safety Stop, Reset, and DPI-Safe Grid Design

**Status:** Proposed for user review

**Date:** 2026-08-26

**Planned against:** `bbb2b6b`
**Platform:** Windows 10 and Windows 11

## Summary

Conduit will stop all shared mouse and keyboard routing whenever any ready Client disconnects while the Server remains running. The Server will release forwarded input, return the cursor to its primary-display center, foreground the Server GUI, remove the missing Client from the draft, and keep routing suspended until a fresh topology succeeds.

The topology editor's **Apply** button becomes **Reset**. Reset will rediscover the Server's physical displays, reconstruct the Server anchor even when the editor state is empty, request fresh inventories from every ready Client, rebuild the draft from authoritative live state, validate it, and run the existing atomic Apply transaction. Reconnection and Server restart will never silently reactivate a stale graph.

The 7×4 editor will also render from its actual canvas dimensions. This fixes the high-DPI laptop failure where the raw Tk canvas draws a small grid in the upper-left of a larger CustomTkinter frame and leaves blank space on the right and bottom.

## Decision override

This design supersedes one failure-isolation rule in the accepted multi-client design. A Client disconnect will no longer leave routing active between the Server and surviving Clients. Even an apparently unaffected route may depend on the missing Client as a graph bridge, and every endpoint may still hold stale edge commands. Clipboard synchronization between surviving connected machines remains active; only Deskflow-style mouse and keyboard routing stops.

## Observed failures and root causes

### Stale graph after a bridge disconnect

In the active layout `Client 1 | Client 2 | Server`, Client 2 is the only graph path between Client 1 and the Server. `ConduitServer.on_client_disconnected()` currently calls `InputRouter.destination_lost(session_id)`. That method returns the cursor only when the disconnected session currently owns it. If Client 1 owns the cursor when Client 2 disappears, the Server and Client 1 retain the old active graph, including edges through Client 2. Later edge reports target a closed session and routing fails.

### Empty editor cannot recover

The disconnect callback removes the missing Client from the draft, but Server stop/start does not reconstruct the editor state. `_begin_topology_rescan()` calls `TopologyEditor.refresh_machine(server_group)`, which updates only a machine already present in the draft. If the draft has lost the Server anchor or otherwise becomes empty, refresh returns false, the grid remains blank, and Apply has no candidate to install.

### Laptop grid occupies only the upper-left

`TopologyEditor` gives a CustomTkinter frame logical dimensions of 280×160 while placing a raw `tk.Canvas` with the same unscaled numeric dimensions. On a laptop using Windows display scaling, CustomTkinter scales its frame while Tk canvas coordinates and drawing constants remain fixed. The grid therefore occupies the upper-left and leaves blank right and bottom regions.

## Goals

- Stop shared mouse and keyboard routing after every ready Client loss while the Server remains active.
- Release held keys and mouse buttons before changing ownership.
- Return the shared cursor to the Server's current primary-display center.
- Keep routing suspended through reconnection, invalid Reset, Reset timeout, and failed atomic distribution.
- Rebuild the editor from current Server discovery and ready Client sessions even when its prior state is empty or corrupt.
- Preserve surviving and returning Client positions when possible; never silently rearrange a custom layout.
- Make the 7×4 grid fill its frame at common Windows scale factors without changing its logical shape.
- Preserve clipboard and unrelated file-transfer behavior among surviving connected endpoints.

## Non-goals

- Automatically bypass or route around a missing Client.
- Automatically move a surviving Client across a gap.
- Resume routing merely because a Client reconnects.
- Change the two-Client limit, session authentication, ports, firewall rules, or physical-display grouping.
- Add a second editor to Client mode.
- Implement the deferred Ctrl+Space, Space shortcut in this change.

## Routing suspension model

The Server owns a persistent `routing_suspended` state separate from the transient `InputRouter.Paused` state used during Apply. The state records a safe category and disconnected machine/session identity for diagnostics; it stores no credentials or network addresses.

Any ready Client loss triggers suspension when the Server remains active. This includes:

- an unexpected socket or network loss;
- the Client's intentional **Disconnect** action;
- replacement of an active Client by a third candidate;
- a reload that tears down ready sessions.

Normal **Stop Server** is excluded because shutdown already releases input and closes every session.

The first loss performs the full transition. Repeated lane-disconnect callbacks and later Client losses are idempotent: they update the reason and draft but do not restart capture or clear the safety latch.

### Suspension order

The Server performs these actions under the existing routing/session serialization boundaries:

1. Mark routing suspended before accepting another edge or input event.
2. Pause the `InputRouter` regardless of which Client owns the cursor.
3. Release remote keys and mouse buttons, stop Server capture/edge detection, hide the capture overlay, and restore the Server-primary center.
4. Send a best-effort `topology_suspend` control message to every surviving ready Client.
5. Each recipient releases injected input, stops Client edge reporting, and retains its last topology only as inactive rollback/reference data.
6. Cancel file jobs involving the lost endpoint through the existing endpoint-disconnect path. Leave unrelated jobs and clipboard delivery running.
7. Foreground the Server GUI and display a direct recovery status. Existing Client disconnect handling foregrounds the disconnected Client's GUI.
8. Remove the lost machine from the draft and rebuild the visible draft around an authoritative Server anchor.

The Server status text is:

`Routing paused — <Windows name> disconnected. Reconnect it, or arrange the remaining machines and press Reset.`

An unexpected loss may still show the existing temporary warning toast. Intentional disconnects remain silent except for the persistent routing-paused status on the Server.

## Reset transaction

The editor button label changes from **Apply** to **Reset**. Cancel keeps its current meaning. Reset is one operation with five stages:

1. **Freeze:** keep routing suspended, cancel any stale display-rescan token, and reject a concurrent Reset/Apply transaction.
2. **Discover Server:** call Windows display discovery and create a fresh Server `MachineDisplayGroup`. Replace or create the draft's gray Server anchor at `(0, 0)`; do not depend on `refresh_machine()` finding an existing anchor.
3. **Reconcile Clients:** snapshot the exact set of ready sessions, discard draft machines without ready sessions, request a fresh inventory from every ready Client, and add or replace those groups after authenticated identity validation.
4. **Preserve placement:** retain a connected machine's current draft placement, then its saved session placement. Use the established right, left, top, bottom spawn order only for a machine with no usable placement. Do not close gaps automatically.
5. **Validate and apply:** validate full-edge graph connectivity and run the existing prepare/commit/finalize transaction against the exact ready-session snapshot.

If the rebuilt draft is invalid, the editor renders the invalid surviving Client groups red, routing stays suspended, and the user may drag them before pressing Reset again. The missing Client remains absent until it reconnects. If discovery, inventory collection, validation, acknowledgement, persistence, or installation fails, routing stays suspended. A failed transaction must not reinstall the previous stale graph as active routing.

Only successful persistence and installation clear `routing_suspended`. A new `InputRouter` then starts from Server-local ownership, and Clients receive the successful topology finalize message before they may report edges.

When no Clients are ready, Reset installs a Server-only topology and shows the gray Server anchor. No external edge detection starts because there is no remote destination.

## Start, reconnect, and tab behavior

Starting the Server always performs the Server-discovery and draft-reconciliation portion of Reset. This guarantees that Stop Server followed by Start Server restores the gray Server anchor without requiring an old editor object to be valid.

A reconnecting Client appears in the draft with its authenticated inventory and saved placement. It remains unroutable and its identification toast remains visible until Reset succeeds. Switching between Server and Client tabs never mutates topology state; returning to the Server tab triggers a render from the current draft so Tk widget mapping cannot leave a visually empty but logically populated editor.

## DPI-safe 7×4 rendering

The editor retains seven columns, four rows, square cells, and its current logical 280×160 footprint in the 400×650 application window. The raw Tk canvas fills the CustomTkinter frame. Rendering derives pixel metrics from the canvas's current mapped width and height instead of fixed 40-pixel drawing coordinates:

- canvas width is divided into seven columns;
- canvas height is divided into four rows;
- the uniform scale of the 7:4 parent keeps the cells square;
- line, cell, label, drag, and hit-test coordinates use the same measured metrics;
- a canvas `<Configure>` event schedules one render when DPI or mapping changes;
- rendering tolerates the initial 1×1 unmapped size and avoids recursive configure loops.

The Reset and Cancel buttons remain overlaid in the upper-right of the grid. Their CustomTkinter sizing continues to follow widget scaling. Tests cover 100%, 125%, 150%, and 200% equivalent canvas dimensions and require the last grid lines to meet the right and bottom edges within one rounding pixel.

## Error handling and diagnostics

- Suspension is safe and idempotent even if a session disconnect callback fires for multiple lanes.
- A Client that cannot receive `topology_suspend` is treated as disconnected; the Server does not resume for the remaining subset.
- Reset timeout identifies the missing Windows machine name in status text without displaying an IP.
- Diagnostics log suspension reason, topology version, session prefix, ready participant count, Reset phase, and success/failure category.
- Diagnostics omit passwords, clipboard contents, file contents, private filenames, tokens, certificates, and user-facing development IPs.

## Test strategy

### Automated regression

- Reproduce `Client 1 | Client 2 | Server`, make Client 2 disappear while Client 1 owns the cursor, and assert release-before-center plus a persistent paused router.
- Repeat for intentional Disconnect, unexpected loss, replacement, and reload; exclude Stop Server.
- Assert every surviving Client receives `topology_suspend` and releases injected input.
- Assert clipboard delivery remains active and only involved file jobs cancel.
- Start from an empty draft, discover the Server, and assert the gray anchor is recreated.
- Stop/start the Server with an empty or stale draft and assert reconstruction.
- Reconnect a Client and assert routing remains suspended until successful Reset.
- Assert invalid, timed-out, and failed Reset never resume or restore the stale graph.
- Assert successful Reset clears suspension only after persistence and exact-participant finalize.
- Assert placement preservation and no automatic gap closure.
- Assert canvas metrics fill 7×4 at representative DPI dimensions and drag hit-testing uses the same metrics.

### Physical verification

1. Arrange `Client 1 | Client 2 | Server`, move the cursor to Client 1, and disconnect Client 2.
2. Confirm input releases, the cursor returns to the Server center, the Server GUI foregrounds, and every boundary stops.
3. Confirm the draft shows the Server and Client 1 with a visible invalid gap rather than an empty grid.
4. Move Client 1 against the Server and press Reset; confirm routing resumes only after success.
5. Repeat by reconnecting Client 2 at its saved position and pressing Reset.
6. Stop/start the Server after a disconnect and confirm the gray Server anchor returns.
7. Repeat the grid test on laptop displays at 125%, 150%, and 200% scaling. The 7×4 grid must fill the frame with no blank right or bottom area.

## Acceptance criteria

- Any ready Client loss while the Server runs stops all shared mouse and keyboard routing.
- No disconnect path leaves a stale graph usable.
- Held input releases before cursor ownership returns to the Server.
- The Server GUI foregrounds with clear recovery instructions.
- Reset reconstructs the gray Server anchor from an empty editor state.
- Reset uses current ready sessions and display inventories, preserves positions, validates, and atomically applies.
- Only a successful Reset resumes routing.
- Stop/start and tab switching cannot leave the grid visually or logically empty.
- The 7×4 grid fills its frame at supported Windows scaling values.
- Clipboard and unrelated file jobs among surviving endpoints continue.
