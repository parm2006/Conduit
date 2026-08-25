# Multi-Client and Multi-Monitor Conduit Design

**Status:** Approved design

**Date:** 2026-08-24

**Planned against:** `ce24139`
**Platform:** Windows 10 and Windows 11

## Summary

Conduit will expand from one Server and one Client to one fixed Server hub and at most two concurrent Client PCs. The Server's keyboard and mouse remain the only roaming input source. The cursor can move Server-to-Client, Client-to-Server, or directly Client-to-Client when the active monitor topology contains a shared edge. All messages still pass through the Server.

Each PC may have multiple physical monitors. Conduit will detect those monitors from Windows, preserve their native arrangement as one machine group, and route only crossings between different machine groups. Windows continues to handle movement among monitors attached to the same PC.

The cluster shares one newest clipboard item. Ordinary clipboard snapshots synchronize immediately without blocking later copies. Explorer file bytes move only when a destination pastes and are relayed through the Server.

## Goals

- Support one Server and two authenticated, concurrently connected Clients.
- Keep the Server as the network, security, input, clipboard, and file-transfer hub.
- Preserve the current three-lane security model and TCP port range `28903–28905`.
- Detect physical monitors on every PC and expose a compact Server-only topology editor.
- Allow direct logical Client-to-Client cursor transitions through Server routing.
- Make clipboard updates from any PC available on every other connected PC.
- Relay Client-to-Client Explorer file pastes without permanent Server caching.
- Isolate a Client failure so the Server and remaining Client continue.

## Non-goals

- More than two Clients.
- macOS, Linux, or internet/WAN operation.
- Clipboard history or persistent clipboard storage.
- Direct Client-to-Client sockets.
- Per-Client passwords.
- Custom machine names inside Conduit.
- Manual rearrangement of physical monitors inside a machine group.
- Multiple roaming cursors or Client-owned roaming input.

## Current constraints

The current implementation assumes exactly one peer:

- `NetworkNode` owns one socket, and attaching a new socket replaces the previous one (`app/network.py:147-211`).
- `SessionCoordinator` owns one active session and clears its tokens when another control session authenticates (`app/session.py:27-95`).
- `ConduitServer` owns one control lane, one data lane, one file lane, and one `switching_to_client` boolean (`app/server.py:28-125`).
- Layout exchange sends one width, one height, and one relative edge (`app/server.py:204-213`, `app/client.py:430-455`).
- `InputHandler` stores one width, one height, one Server edge, and one Client return edge (`app/input_handler.py:74-89`).
- The Server GUI offers four fixed edge buttons (`app/gui.py:438-464`).
- Preferences persist one Client position (`app/preferences.py:41-62`).

The feature requires explicit per-Client sessions, a topology graph, and centralized routing. Duplicating the existing singleton Server on another port triplet would preserve these limitations and complicate shared state, so this design refactors the hub instead.

## System architecture

### Shared listeners and bounded sessions

The Server retains exactly three listeners:

| Port | Lane | Responsibility |
| --- | --- | --- |
| `28903` | Control | Authentication, pairing, topology, input control, cluster commands, and transfer coordination |
| `28904` | Clipboard data | Ordinary rich clipboard snapshots |
| `28905` | File data | Explorer file payloads |

The base port remains configurable under the current three-consecutive-port rule. Supporting a second Client does not allocate more ports. Each listener accepts multiple sockets and binds each authenticated socket to a bounded `ClientSession` registry.

The existing Windows Firewall rule remains restricted to the same executable, Private networks, local subnet, and TCP `28903–28905`. Listener backlog and connection tracking change; firewall scope does not.

### ClientSession

Each connected Client has one `ClientSession` containing:

- trusted device ID;
- Windows computer name and collision-safe display name;
- control, clipboard-data, and file channels;
- session ID and lane-binding state;
- detected display inventory;
- saved topology placement and assigned color;
- readiness, disconnect, and replacement state;
- active file jobs and cancellation state.

A Client becomes ready only when all three lanes authenticate against the same session and trusted peer. A partial or mismatched bundle times out without disturbing another Client.

### Hub services

The Server separates coordination into focused services:

- **SessionRegistry:** enforces the two-Client limit, authenticates and binds lanes, isolates disconnects, and coordinates replacement.
- **TopologyService:** owns reported display inventories, draft placement, the persisted active graph, validation, and atomic Apply.
- **InputRouter:** tracks the one roaming cursor destination, resolves edge transitions, scales movement, and releases held input.
- **ClipboardHub:** serializes clipboard arrivals, assigns cluster revisions, stores the newest ordinary snapshot in memory, and broadcasts accepted updates.
- **FileRelay:** routes manifests, encrypted file data, status, and cancellation between a source and destination session.
- **ClusterCommands:** broadcasts background mode, reload, and shutdown commands and collects acknowledgements where required.

These services depend on `ClientSession` interfaces rather than raw singleton network objects. Low-level lane objects may retain the current one-socket send/receive mechanics once detached from listener ownership.

## Connection, identity, and replacement

The Server displays one password. Both Clients enter that password. Each new physical PC still completes the existing one-time certificate fingerprint comparison and pairing approval. Successful trust is keyed by stable peer identity, not IP address or connection order.

Conduit uses the Windows computer name for display. Duplicate names receive a Conduit-only suffix such as `.2` or `.3`; stable trusted IDs remain authoritative. Users cannot rename machines inside Conduit.

A third authenticated control candidate may wait for at most 15 seconds while the Server UI explains the two-Client limit and offers:

- replace Client 1;
- replace Client 2;
- reject the newcomer.

Only one replacement candidate may wait at a time. It does not consume an active Client slot. If the Server user does not decide within 15 seconds, Conduit rejects the candidate, closes every candidate lane, releases its authentication/session resources, and dismisses the prompt. Further third candidates are rejected while a replacement decision is already pending.

Replacement cleanly releases the old Client's input, cancels its transfers, disconnects its lanes, and removes it from active routing. The newcomer inherits the freed placement in the draft when its display shape fits; otherwise Conduit tries the normal right, left, top, bottom placement order and finally leaves it unplaced. In every case, the replacement remains unroutable until a successful Apply. No new or returning Client silently changes the active graph.

A returning trusted Client is placed at its last saved position when possible. Reconnection does not silently mutate the active graph: the restored placement appears in the draft and becomes routable after a successful Apply.

## Display discovery and identity

Each process discovers physical displays automatically at startup. The Client sends its inventory after lane readiness and whenever the Server requests an Apply rescan. The Server never counts a Conduit peer as a physical monitor.

Windows discovery must report:

- stable display target/device identity;
- full desktop rectangle, including negative coordinates;
- resolution and orientation;
- effective DPI/scaling information;
- primary-display status;
- enabled/disabled state.

The implementation should use Windows display configuration APIs for stable identity and topology, with monitor APIs for current rectangles and work areas. Browser screen data used in design mockups is not an implementation source.

Physical monitors form one immutable machine-local group. The topology editor normalizes each monitor to one equal visual cell while retaining its native adjacency. Different resolutions do not change cell size.

## Compact topology editor

The editor exists only on the Server and fits within a sub-500×500 area of the existing CustomTkinter GUI. Production UI contains no manual add button and no display-detection button. Clients appear automatically; monitor discovery is automatic.

The editor shows:

- exact 40×40 grid cells;
- one gray Server group fixed as the anchor;
- one stable color per Client;
- the first letter of the Windows computer name repeated on every monitor cell;
- **Apply** and **Cancel** inside the grid.

The Server is gray, the two active Client slots are blue and green, and a pending third candidate is purple. Purple is temporary and never represents a third active slot. The candidate's primary display shows its purple identification toast during the 15-second decision window; if selected, that toast persists through draft editing. When the replacement successfully Applies, the newcomer takes the evicted Client slot's blue or green color. Cancel or timeout dismisses the temporary toast. Initial collisions are acceptable because color and the persistent Client toast provide disambiguation.

The grid does not show IP addresses, machine names, resolutions, headers, legends, or a “Server ready” overlay. The Server uses its Windows name, such as `ParthPC`; `.208` is not user-facing product text.

When a Client joins a draft, that Client—not the Server—shows a persistent toast on its primary display. The entire toast body uses the Client's assigned color and shows its Windows name, display count, resolutions, and connection state. It remains visible throughout editing and disappears only after Apply or Cancel. The Client may disconnect through its own toast or existing Client controls.

The Server shows a separate warning toast when a physical display disconnects.

### Draft and active topology

`TopologyService` holds two versions:

- **Active topology:** the last successfully committed routing graph.
- **Draft topology:** current detected inventories and user placement.

Dragging changes only the draft. It may contain gaps or overlaps. Conduit does not turn cells red during dragging.

Draft editing does not pause Conduit. The previous active topology continues to route input, clipboard updates, and file work until a valid Apply reaches its pause barrier. Cancel discards the draft, restores the active arrangement in the editor, and dismisses Client draft toasts. Leaving the editor or moving the Server-controlled cursor to another machine has the same immediate discard behavior.

New Clients are placed on the first unoccupied full edge in this order: right, left, top, bottom. Top and bottom are fallbacks when physical monitor cells consume both horizontal positions. An unplaceable Client remains connected but unroutable in the draft; the previous active topology continues.

### Validation

A valid draft satisfies all of these rules:

- no physical monitor cells overlap;
- an inter-machine connection covers one complete 40×40 cell edge on both sides;
- corner-only diagonal contact is invalid;
- partial-edge and T-junction contact is invalid;
- every Client has a graph path to the fixed Server;
- a Client may reach the Server through the other Client.

Validation occurs on Apply. Failed Apply leaves the active topology untouched and marks only invalid Client groups with a red rounded outline. The Server never turns red. Two Clients connected only to each other are both red when their component lacks a Server path.

### Apply transaction

The **Apply** label includes both rescan and refresh behavior:

1. Ask the Server and every ready Client to rescan physical displays.
2. Reconcile returned inventories with stable display IDs and the draft.
3. Validate the complete candidate graph.
4. If invalid, show Client errors and stop without changing runtime behavior.
5. If valid, release all injected input, return the roaming cursor to the Server primary-display center, and enter one cluster-wide Apply barrier. Pause input routing, clipboard broadcast delivery, and file-transfer scheduling; reject new file jobs until the barrier ends.
6. Keep Windows clipboard observation and `LatestWinsSender.submit()` non-blocking during the barrier. Preserve and continue updating the newest ordinary clipboard snapshot and revision; each source retains at most its current in-flight item and one newest pending item under the existing latest-wins rule.
7. Distribute the candidate topology to all ready Clients.
8. Require acknowledgements from every participant.
9. Atomically activate and persist the graph, then remove the barrier and resume routing, file scheduling, and clipboard delivery. Deliver the newest queued clipboard revision after the barrier.
10. If distribution or acknowledgement fails, restore the previous active graph, remove the barrier, and resume the same services against that graph.

Client draft toasts disappear after a successful commit or explicit Cancel. Validation, distribution, or acknowledgement failure keeps the draft and its toasts visible while the previous active graph resumes.

A copy made during Apply is therefore never blocked at the Windows clipboard and is not silently discarded by the topology transaction. Intermediate copies may still be intentionally coalesced by the documented latest-wins policy. Apply does not create clipboard history or a FIFO queue.

### Physical display loss

When a display disconnects, Conduit removes it from the newly detected draft and warns on the Server. It does not modify the active topology, active calculations, or active routes until Apply. The user explicitly accepts that the active cursor may enter the missing logical display region during this interval.

## Input routing

### Cursor ownership

Only the Server's physical keyboard and mouse produce roaming input. Both Clients may remain connected, but the cluster has one logical Server-controlled cursor destination. A Client's locally attached mouse and keyboard remain local and never trigger topology transitions.

The Server tracks:

- active machine and display IDs;
- logical cursor coordinates;
- the active edge mapping;
- forwarded keys and mouse buttons;
- the currently selected `ClientSession`, if any.

While the cursor is on a Server display, Windows handles movement, including movement among physical Server monitors. Conduit watches only exposed active graph edges. While the cursor is remote, the existing Server overlay captures deltas, clicks, scrolls, and keyboard events and forwards them to the selected Client.

### Cross-machine transition

When an injected cursor crosses an inter-machine edge:

1. Resolve the neighboring machine/display from the active graph.
2. Release every tracked injected key and mouse button on the departing remote Client.
3. Clear forwarded state so a held modifier or button cannot remain stuck or leak into the destination.
4. Preserve the proportional crossing position along the shared edge.
5. Compute destination scaling from source and destination resolution/DPI.
6. If the destination is remote, send an activation message containing display ID, entry edge, and ratio, then warp just inside that edge.
7. If the destination is the Server, stop remote capture and restore the Server cursor just inside its entry edge.
8. Route later Server input only to the new destination.

The current release discipline remains mandatory. Today, Client return-edge handling releases all injected keys before `switch_back` (`app/client.py:496-520`), and Server switch-back releases its tracked forwarded keys before restoring edge detection (`app/server.py:275-298`, `app/server.py:375-391`). The new router generalizes this to every Server↔Client and Client↔Client transition.

A physical key held through a transition remains suppressed until its physical release; the destination does not receive a synthetic press without a new user press.

### Direct logical Client-to-Client movement

Clients do not connect directly. A Client detecting an injected edge crossing reports machine/display, edge, and ratio to the Server. The Server releases the outgoing Client, selects the adjacent Client through `TopologyService`, activates it, and updates cursor ownership. The cursor need not appear on a Server display between those steps.

Movement among monitors attached to one Client remains native Windows movement. Only injected Server-controlled movement may produce remote edge reports.

### Disconnect and rebuild

If the controlled Client disconnects, Conduit releases tracked input and returns the cursor to the center of the Server's primary display. Apply and cluster reload do the same. Conduit does not preserve a remote cursor location through topology rebuilding.

## Shared ordinary clipboard

### Global latest item

The Server owns one in-memory `ClipboardHub` state:

- monotonic Server revision;
- source trusted device ID;
- source clipboard sequence;
- ordinary snapshot or file-offer metadata;
- format and size metadata suitable for content-free logs.

A local copy on the Server or either Client is eligible regardless of roaming cursor location. Completed arrivals pass through one Server dispatcher; Server receive order decides simultaneous copies. The newest accepted item is broadcast to every other ready PC and becomes that PC's Windows clipboard. Clients reject stale revisions.

A newly ready Client receives the latest ordinary snapshot. The Server erases its stored snapshot when it stops. No clipboard history or disk persistence is added.

### Non-blocking latest-wins scheduling

Copying never waits for network processing. Preserve the existing `LatestWinsSender` contract (`app/latest_wins_sender.py:10-57`):

- each endpoint has one clipboard worker;
- one snapshot transmits at a time;
- an in-flight send always finishes;
- there is one pending slot;
- every new copy replaces the pending slot and returns immediately;
- after the active send, the worker sends the newest pending snapshot;
- failure does not kill the worker;
- stopping drops pending work and rejects later submissions.

If screenshot A is sending and B, C, then D arrive before it finishes, Conduit sends A and D. It intentionally skips B and C. This is the current tested behavior (`tests/test_latest_wins_sender.py:8-79`) and must not become a FIFO history.

Ordinary clipboard traffic uses the clipboard-data lane, so an active file transfer does not block new copies.

The Apply barrier pauses cluster delivery, not local capture. Copies observed during Apply continue through the same non-blocking per-source scheduler. When the barrier opens, Conduit completes any already in-flight submission and then sends only that source's newest pending snapshot. The Server's receive order still determines the cluster-wide newest item.

### Echo suppression and validation

Preserve current single-use fingerprint suppression. After Conduit injects a remote snapshot, it suppresses the next matching Windows clipboard observation once and clears the marker. A later user copy of identical content synchronizes normally (`app/clipboard_handler.py:64-126`, `app/clipboard_handler.py:253-283`).

The hub adds source ID and revision checks so broadcasts among three PCs cannot loop or overwrite a newer revision. Payload validation remains bounded. Oversized or invalid content leaves remote clipboards unchanged and warns only the source PC. Logs never include clipboard contents.

## Explorer file routing

The global clipboard stores file-offer metadata, not file bytes. A new copy may replace the latest offer while an existing transfer continues with its captured manifest and job ID.

On destination paste:

1. The destination asks the Server to resolve the latest offer.
2. The Server verifies that source and destination sessions are ready.
3. The source snapshots the selection and returns a manifest.
4. The Server relays manifest acknowledgement and encrypted file data between the source and destination file channels.
5. The destination stages, publishes, and invokes paste through the existing virtual-file path.

The Server may use bounded transient staging required by resume/backpressure behavior but creates no permanent file cache. Source or destination disconnect cancels only affected jobs and cleans their staging. A copy made during a file transfer still synchronizes immediately on the separate clipboard-data lane.

Transfer progress, cancellation, and failure toasts appear only on the source and destination PCs. The uninvolved third PC receives no transfer toast. Newer clipboard offers do not corrupt or silently replace an active transfer.

## Cluster commands

- `Ctrl+Alt+Shift+R` coordinates a cluster-wide disconnect and lane rebuild while preserving trust and the persisted active topology.
- `Ctrl+Alt+Shift+Escape` closes Conduit on the Server and both Clients.
- Background-mode changes remain synchronized cluster-wide.
- Commands broadcast to both ready sessions; one failed Client does not prevent cleanup of the other.

## Failure handling

- One Client disconnect leaves the Server and other Client operational.
- Lane mismatch or timeout fails only the candidate session.
- Failed topology validation never pauses or changes active routing.
- Failed topology distribution rolls every acknowledged participant back to the previous graph.
- Controlled-destination loss releases input and returns control to the Server.
- Clipboard failure drops only the affected item; later copies continue.
- File failure cancels only jobs involving the failed endpoint.
- A pending third Client cannot displace an existing session without the Server user's explicit choice and is fully disconnected after 15 seconds without one.
- Logs contain identifiers, revisions, formats, sizes, phases, and redacted error types—not clipboard contents, file contents, passwords, or tokens.

## Verification strategy

### Unit tests

- Session registry capacity, authentication, lane binding, mismatched lanes, single-candidate replacement gating, 15-second candidate timeout cleanup, and isolated disconnect.
- Display normalization across horizontal, vertical, negative-coordinate, rotated, and uneven-resolution Windows layouts.
- Full-edge adjacency, overlap, diagonal rejection, partial-edge rejection, graph connectivity, and auto-placement order.
- Draft/active separation, Cancel, invalid Apply, new/replacement Client draft-only placement, acknowledgement failure, atomic commit, persistence, and rollback.
- Server↔Client and Client↔Client transitions, edge ratios, DPI scaling, outer-edge selection, and same-PC monitor movement.
- Key/button release on every transition, disconnect, Apply, reload, and shutdown.
- Client local input isolation from the Server-controlled cursor.
- Three-source clipboard receive ordering, latest-wins replacement, single-use echo suppression, stale revision rejection, late-Client sync, ordinary copies during file transfer, and newest-pending clipboard delivery across both successful and rolled-back Apply barriers.
- File relay for every source/destination pair, offer replacement, active-job independence, disconnect cancellation, staging cleanup, and toast scope.
- Compact GUI rendering/state tests: no add/detect control, in-grid Apply/Cancel, fixed Server, automatic Client cells, colliding initials, stable gray/blue/green slots, temporary purple replacement candidate, color inheritance after Apply, invalid red Clients, and no user-facing IP.

### Integration tests

- Real TLS sockets with two simulated Client session bundles sharing ports `28903–28905`.
- Simultaneous lane establishment and independent heartbeats.
- Third-Client replace/reject/15-second-timeout flow, including draft-only placement before Apply.
- Topology distribution and rollback across both Clients.
- Direct logical Client-to-Client input routing through the Server.
- Cluster clipboard races and file relay while clipboard copies continue.
- Cluster-wide reload, background mode, and shutdown.
- Firewall inspection and repair proving the exact existing three-port scope.

### Manual Windows verification

- One Server and two physical Client PCs.
- Mixed resolution, DPI, orientation, and negative desktop coordinates.
- Multiple HDMI/DisplayPort monitors attached to Server and Clients.
- Direct Client-to-Client transitions without cursor appearance on the Server.
- Client local mouse use while the roaming cursor is elsewhere.
- Rapid screenshots during a large file transfer and during Apply.
- HDMI removal while the stale active topology remains routable until Apply.
- Destination disconnect with held modifiers/buttons.
- Third-Client replacement and every cluster hotkey.

## Security and firewall invariants

- Keep TLS minimums, certificate identity, interactive first pairing, and remembered trust.
- Bind every data/file lane to the authenticated control session and peer identity.
- Use one shared Server password for both Client slots. This deliberately favors simple Server setup over per-Client password isolation: disclosure increases access exposure across the cluster, but a password alone still cannot silently enroll a new PC because first-time per-device fingerprint comparison and pairing approval remain mandatory.
- Never broaden the firewall beyond the configured three consecutive TCP ports.
- Keep the managed rule executable-specific, Private-network-only, and local-subnet-only.
- Never log secrets or user clipboard/file contents.
- Validate all IDs, topology messages, revisions, manifests, sizes, and lane ownership at trust boundaries.

## Approved outcome

The approved product is a bounded three-PC Conduit cluster: one fixed Server hub, two Clients, automatic multi-monitor discovery, a compact Server-only topology editor, Server-owned roaming input, a global newest clipboard item, and on-demand Server-relayed file paste. The implementation must preserve current security, latest-wins clipboard behavior, held-input release guarantees, and firewall scope while replacing singleton peer state with isolated per-Client sessions.
