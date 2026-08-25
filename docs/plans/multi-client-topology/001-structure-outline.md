---
type: structure-outline
repo: Conduit
branch: main
sha: e2c191cb6591ec8cc666058f393ca95932edb279
status: accepted
source_design_discussion: ../../superpowers/specs/2026-08-24-multi-client-topology-design.md
---

# Structure Outline: Two-Client Multi-Monitor Conduit

## Review Status

- **Status:** Accepted on 2026-08-24
- **Review needed:** None; the seven independently landable phase boundaries are accepted.
- **Next artifact:** Executor-safe implementation plans, one plan per landable phase.

## Desired End State

- One Windows Server PC is the fixed Conduit hub and sole owner of the roaming mouse and keyboard.
- Up to two Windows Client PCs remain connected simultaneously through the existing three TCP ports.
- Each PC contributes its actual Windows-detected physical monitor arrangement as one immutable machine group.
- The Server-only compact editor uses equal 40×40 monitor cells, automatic Client arrival, draft/active separation, full-edge graph validation, persistent colored Client toasts, and atomic Apply/Cancel behavior.
- Input can traverse Server→Client, Client→Server, and logical Client→Client edges through the Server without requiring the cursor to appear on an intermediate machine.
- The newest ordinary clipboard item synchronizes among all three PCs without blocking copy operations. Explorer file bytes move only on paste and relay through the Server.
- Existing TLS identity, interactive pairing, one shared Server password, held-input release, latest-wins scheduling, three-port firewall scope, and privacy-safe diagnostics remain intact.

## Planning Sources and Design Pressure

- Accepted behavior: [multi-client topology design](../../superpowers/specs/2026-08-24-multi-client-topology-design.md).
- Detailed product answers: `handoffs/2026-08-24-2158-multi-client-topology-grilling.md` (ignored durable handoff).
- Current singleton evidence: `app/server.py:28-123`, `app/network.py:147-300`, `app/session.py:27-95`, and `app/file_transfer/transport.py:88-164` each own one peer or one lane generation.
- Current input evidence: `app/server.py:243-391`, `app/client.py:430-520`, and `app/input_handler.py:61-294` encode one relative edge and one remote destination.
- Current clipboard evidence: `app/latest_wins_sender.py:11-57`, `app/server.py:472-571`, and `app/client.py:522-604` provide the non-blocking one-active/one-pending contract that must be preserved.
- Current verification convention: `CONTRIBUTING.md` requires compileall, complete `unittest` discovery, and `git diff --check`.
- Coding-standards pressure: use explicit lifecycle states (`state.md`), deep ownership modules rather than pass-through wrappers (`modules.md`), translate lane/display messages at process boundaries (`boundaries.md`), give every listener/worker/barrier a visible lifetime (`effects.md`), and test caller-visible behavior through real seams (`verification.md`).

Every phase must leave the repository green and independently reviewable. After each focused phase command, its executor must also run the shared landing gate:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check
```

## Implementation Overview

- [ ] Phase 1: Replace the one-edge control with a real single-Client multi-monitor topology slice.
- [ ] Phase 2: Keep two authenticated Client lane bundles alive and bound to isolated sessions.
- [ ] Phase 3: Route the Server-owned cursor across the active three-PC display graph.
- [ ] Phase 4: Introduce the Server-owned global clipboard hub and Apply delivery barrier.
- [ ] Phase 5: Relay Explorer file jobs and cluster commands across isolated Client sessions.
- [ ] Phase 6: Finish atomic Apply, replacement, reconnection, and display-loss workflows.
- [ ] Phase 7: Prove firewall, packaging, upgrade, and physical three-PC behavior.

## Phase 1: Single-Client Multi-Monitor Topology Slice

Replace the four edge buttons for the existing one-Client runtime with the approved compact grid. This phase establishes the topology model, real Windows display discovery, persistence, and draft/active workflow while retaining the current one-Client session limit. Landing the editor first gives the later multi-session work a real topology contract instead of growing new booleans around `layout_position`.

### File Changes

- `app/display_topology.py` (new) — own refined machine/display identities, immutable physical groups, draft and validated topology types, full-edge adjacency, connectivity, overlap validation, right/left/top/bottom auto-placement, edge mappings, and coordinate-ratio calculations.
- `app/windows_displays.py` (new) — translate native Windows display-configuration and monitor API results into `display_topology` values; report stable target identity, full rectangles including negative coordinates, rotation, primary state, and effective DPI without treating Conduit peers as displays.
- `app/topology_editor.py` (new) — own the compact 40×40-cell Server editor, fixed gray Server group, movable whole Client group, red validation outline after Apply only, and in-grid **Apply**/**Cancel** controls.
- `app/topology_toast.py` (new) — own persistent full-color Client identification toasts placed on the Client primary display; keep this separate from transient file-transfer toast policy.
- `app/preferences.py` — replace scalar `client_position` persistence with a versioned active-topology record keyed by trusted machine and display identities. Treat the old scalar only as a one-time migration input and do not retain dual internal models.
- `app/gui.py` — embed only the compact editor in the Server view, remove the production edge selector, perform automatic display discovery at startup, and keep Clients free of editor controls.
- `app/server.py`, `app/client.py` — exchange one machine display inventory, distribute the validated one-Client active graph, and preserve the previous active graph until Apply succeeds.
- `app/input_handler.py` — consume the validated current edge mapping without learning raw Windows display DTOs.
- `tests/test_display_topology.py`, `tests/test_windows_displays.py`, `tests/test_topology_editor.py`, `tests/test_topology_toast.py` (new) — cover the new domain and UI seams.
- `tests/test_gui_preferences.py`, `tests/test_input_geometry.py` — extend current persistence and Windows work-area/toast positioning coverage.

Proposed internal lifecycle shape:

```python
DraftTopology -> validate() -> InvalidTopology | ValidatedTopology
ValidatedTopology -> apply(participants) -> ActiveTopology | previous ActiveTopology
```

Raw native rectangles and wire dictionaries must be parsed into refined values at their boundaries. Only `ValidatedTopology` may be installed in input routing.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_display_topology tests.test_windows_displays tests.test_topology_editor tests.test_topology_toast tests.test_gui_preferences tests.test_input_geometry -q` — proves monitor normalization, validation, draft/active separation, persistence migration, compact rendering state, and toast placement.
- [ ] Shared landing gate — proves the existing one-Client runtime and release contracts remain green.

#### Evals / Regression Checks

- [ ] Uneven resolutions still render as equal cells while their native rectangles and scale factors remain available to routing.
- [ ] Invalid drafts never mutate the active graph, and moving the controlled cursor away cancels the edit.
- [ ] No IP address, manual add/detect control, legend, or invented display appears in the production editor.

#### Manual

- [ ] On a Server or Client with two physical monitors, compare the discovered group shape, primary display, orientation, and resolutions with Windows Display Settings.

## Phase 2: Two Isolated Client Sessions on the Existing Ports

Refactor listener ownership so the Server can retain two complete control/clipboard-data/file lane bundles at once. One shared password remains the first authentication factor; per-device certificate pairing remains mandatory. A third authenticated candidate enters an explicit bounded replacement lifecycle instead of replacing an arbitrary socket.

### File Changes

- `app/session.py` — replace singleton `_active_session` state with a bounded `SessionRegistry` and explicit candidate/partial/ready/replacing/closed states. Bind one-use lane tokens to session ID, purpose, trusted peer identity, and the existing address constraint.
- `app/network.py` — separate listening/acceptance lifetime from a peer connection lifetime. A listener owns many candidate workers; each accepted connection owns its socket generation, heartbeat, callbacks, and cleanup. Do not broaden `NetworkNode` into a shallow compatibility wrapper around the same singleton.
- `app/file_transfer/transport.py` — retain one file listener but own file connections per authenticated `ClientSession`; prevent one disconnect or stale receive loop from closing another session.
- `app/server.py` — compose a map of at most two ready `ClientSession` objects instead of `control_network`, `data_network`, and `file_network` singletons.
- `app/client.py` — remain a one-Server endpoint while attaching Windows machine identity and display inventory to its session readiness flow.
- `app/gui.py`, `app/topology_editor.py`, `app/topology_toast.py` — add automatic draft cells for ready Clients and the 15-second replace/reject prompt. Use blue and green for the two active slots and purple only for the pending candidate; successful Apply promotes the replacement to the evicted slot color.
- `tests/test_security_session.py`, `tests/test_security_network.py`, `tests/test_security_full_session.py`, `tests/test_file_transfer_network_identity.py` — generalize security tests to two simultaneous bundles and isolated failure.
- `tests/test_client_session_registry.py` (new) — cover capacity, partial-lane cleanup, 15-second timeout, single pending candidate, explicit replacement, reject, and per-session teardown.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_client_session_registry tests.test_security_session tests.test_security_network tests.test_security_full_session tests.test_file_transfer_network_identity -q` — proves two complete session bundles can coexist without weakening authentication or lane ownership.
- [ ] Shared landing gate — proves one-Client connections and existing security behavior remain supported.

#### Evals / Regression Checks

- [ ] A wrong password, stalled TLS handshake, partial lane bundle, third-candidate timeout, or one Client disconnect cannot disturb either ready session.
- [ ] Only one third candidate waits, it consumes no active slot, and all of its lanes/resources close after 15 seconds without a decision.
- [ ] The Server still listens only on base port, base+1, and base+2; no direct Client-to-Client socket exists.

## Phase 3: Graph-Based Server-Owned Input Routing

Move destination selection and edge transitions out of single-peer branches in `ConduitServer` into an `InputRouter` that consumes only the committed topology and ready-session interfaces. Same-machine physical monitor movement stays native Windows behavior; only inter-machine full edges create Conduit transitions.

### File Changes

- `app/input_router.py` (new) — own logical cursor location, active machine/display, destination session, edge selection, resolution/DPI scaling, transition serialization, and release-before-switch policy.
- `app/display_topology.py` — expose immutable active graph queries and full-edge coordinate mappings required by the router.
- `app/server.py` — delegate input state and routing to `InputRouter`; send Client1→Client2 events through the hub without injecting them on the Server.
- `app/client.py` — accept targeted graph layout/configuration, report return-edge coordinates with machine/display identity, release injected keys/buttons before every normal transition, and keep local Client input independent.
- `app/input_handler.py` — focus on physical capture and injection. Remove the one-screen/one-edge authority after callers have moved to `InputRouter`.
- `tests/test_input_router.py` (new) — table-test Server↔Client and Client↔Client paths, outer-edge selection, graph paths, scaling, and transition ordering.
- `tests/test_input_geometry.py`, `tests/test_emergency_release.py`, `tests/test_input_numpad_forwarding.py`, `tests/test_input_delete_forwarding.py` — preserve current input contracts through the new public seam.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_input_router tests.test_input_geometry tests.test_emergency_release tests.test_input_numpad_forwarding tests.test_input_delete_forwarding -q` — proves graph transitions, scaling, local-input isolation, and release discipline.
- [ ] Shared landing gate — proves the router refactor preserves unrelated behavior.

#### Evals / Regression Checks

- [ ] Every inter-machine transition releases tracked keys and buttons before changing destination, including disconnect, Apply, reload, and shutdown paths.
- [ ] Client1→Client2 logically crosses one shared edge through the Server hub without requiring a cursor appearance on the Server.
- [ ] Losing the controlled Client releases input and returns the cursor to the center of the Server primary display.

#### Manual

- [ ] On mixed-DPI machines, compare perceived cursor speed across several edge ratios and verify local Client mouse movement does not move the roaming cursor.

## Phase 4: Global Latest Clipboard Hub

Introduce one Server-owned in-memory clipboard authority while preserving each endpoint's existing non-blocking latest-wins sender and single-use injection echo suppression. This slice makes ordinary copies from any PC available on both others and defines clipboard behavior at the Apply barrier.

### File Changes

- `app/clipboard_hub.py` (new) — own Server receive serialization, monotonic cluster revisions, source identity/sequence validation, newest ordinary snapshot, late-Client synchronization, delivery pause/resume, and privacy-safe metadata.
- `app/server.py` — route local and per-session clipboard events through `ClipboardHub` rather than one `data_network` peer.
- `app/client.py` — tag source sequence/session identity, reject stale cluster revisions, and keep local copy capture independent of roaming cursor ownership.
- `app/clipboard_handler.py` — preserve current supported formats and one-use echo suppression while exposing capture/injection through the hub protocol.
- `app/latest_wins_sender.py` — preserve the current one-active/one-newest-pending contract; change only if a small explicit barrier hook is needed without blocking `submit()`.
- `app/file_transfer/paste_coordinator.py` — keep ordinary clipboard snapshots and file-offer metadata distinguishable under the global revision model.
- `tests/test_clipboard_hub.py` (new) — cover three-source ordering, stale revision rejection, late join, disconnect, Apply success/rollback barriers, and source-only failure reporting.
- `tests/test_clipboard_scheduling.py`, `tests/test_latest_wins_sender.py`, `tests/test_clipboard_deduplication.py`, `tests/test_clipboard_formats.py` — pin all current contracts during the relay change.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_clipboard_hub tests.test_clipboard_scheduling tests.test_latest_wins_sender tests.test_clipboard_deduplication tests.test_clipboard_formats -q` — proves global newest selection without changing endpoint scheduling or format support.
- [ ] Shared landing gate — proves no clipboard/file regression outside the focused cases.

#### Evals / Regression Checks

- [ ] If A is in flight and B/C/D arrive at one source, A finishes and only D remains pending; no FIFO history appears.
- [ ] During Apply, Windows copy and `submit()` return normally; delivery pauses, intermediate pending copies coalesce, and the newest queued revision is delivered after commit or rollback.
- [ ] Server receive order decides simultaneous sources, remote injection cannot loop, and logs contain no clipboard payloads.

## Phase 5: Hub-Relayed Files and Cluster Commands

Generalize file-offer ownership and transfer jobs from one source/destination pair to stable source and destination session IDs. Keep file bytes on demand at paste time, preserve bounded transient staging, and broadcast cluster lifecycle commands without letting one failed Client block cleanup of another.

### File Changes

- `app/file_transfer/cluster_router.py` (new) — resolve the latest file offer, latch source/destination/job identity, relay manifests and encrypted frames, own per-job cancellation, and reject new jobs while Apply is quiescing file scheduling.
- `app/file_transfer/paste_coordinator.py`, `app/file_transfer/controller.py`, `app/file_transfer/transport.py` — carry stable endpoint/session IDs through offers and jobs without sharing mutable singleton destination state.
- `app/server.py`, `app/client.py` — route file protocol messages through the Server hub and cancel only jobs whose source or destination disconnects.
- `app/file_transfer/toast.py` — scope transfer progress/failure/cancellation to source and destination PCs only.
- `app/global_hotkey.py`, `app/gui.py` — coordinate reload, shutdown, and background mode across both ready sessions while preserving local cleanup if one broadcast fails.
- `tests/test_cluster_file_routing.py`, `tests/test_cluster_commands.py` (new) — cover every source/destination pair, active-job independence, endpoint loss, toast scope, and partial command failure.
- Existing `tests/test_file_paste_*.py` and `tests/test_file_transfer_*.py` — preserve manifest validation, transfer safety, staging, and virtual-file behavior.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_cluster_file_routing tests.test_cluster_commands tests.test_file_paste_routing tests.test_file_paste_clipboard tests.test_file_paste_service -q` — proves cluster routing and command behavior through focused seams.
- [ ] `.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_file*.py" -q` — proves the complete file subsystem remains green.
- [ ] Shared landing gate — proves integration with security, GUI, and clipboard behavior.

#### Evals / Regression Checks

- [ ] Copying newer clipboard data never blocks or corrupts a captured active transfer manifest.
- [ ] Disconnect during paste cancels and cleans only affected jobs; the uninvolved PC receives no transfer toast.
- [ ] File bytes are not uploaded before paste and are never stored as a permanent Server cache.

## Phase 6: Atomic Apply and Runtime Reconciliation

Complete the cross-service transaction that turns a validated draft into the active cluster graph. This phase wires topology, input, clipboard, file scheduling, session replacement, reconnect, and physical-display rescan into one explicit workflow with acknowledgement and rollback.

### File Changes

- `app/topology_service.py` (new, unless Phase 1's domain module already provides a deep enough owner) — own draft/active versions, participant acknowledgements, Apply barrier sequencing, persistence timing, rollback, and reconnect/display-loss reconciliation. Do not add a pass-through service if `display_topology.py` can own this workflow coherently.
- `app/topology_editor.py`, `app/topology_toast.py`, `app/gui.py` — finish automatic Client appearance, persistent toast lifetime, invalid red Client outlines after Apply, cancel-on-cursor-away, replacement selection, and successful color promotion.
- `app/server.py` — compose barrier participants in this order: validate; release input and center cursor; pause input/clipboard delivery/file scheduling; rescan and distribute; require all acknowledgements; commit or rollback; resume.
- `app/client.py` — rescan physical displays on request, acknowledge only successfully installed candidate graphs, preserve local clipboard capture through the barrier, and keep its identification toast until successful commit or explicit Cancel.
- `app/preferences.py` — persist only after all acknowledgements; restore trusted returning Clients into the draft, never directly into active routing.
- `tests/test_topology_apply.py`, `tests/test_topology_reconnect.py` (new) — cover successful Apply, invalid draft, acknowledgement failure, rollback, clipboard copies during the barrier, file-job rejection/quiescence, replacement, reconnect, and stale active geometry after display loss.
- `tests/test_topology_editor.py`, `tests/test_gui_connection_lifecycle.py`, `tests/test_emergency_release.py` — verify user-visible state and cleanup ordering.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_topology_apply tests.test_topology_reconnect tests.test_topology_editor tests.test_gui_connection_lifecycle tests.test_emergency_release tests.test_clipboard_hub tests.test_cluster_file_routing -q` — proves atomic cross-service activation and rollback.
- [ ] Shared landing gate — proves every prior slice composes without regressions.

#### Evals / Regression Checks

- [ ] Invalid validation never enters the barrier; failed distribution always restores the previous active graph and resumes queued clipboard delivery.
- [ ] Any new, returning, or replacement Client remains draft-only and unroutable until successful Apply.
- [ ] Physical display loss updates the visible draft and warns on the Server while leaving active calculations unchanged until Apply.
- [ ] Draft toasts persist across failed Apply and disappear only after successful commit, explicit Cancel, or candidate timeout.

#### Manual

- [ ] Start a valid transfer and make rapid screenshots while Apply succeeds and while one Client withholds acknowledgement; confirm the stated barrier, rollback, and newest-pending behavior.

## Phase 7: System Hardening, Packaging, and Physical Acceptance

Finish with system-level evidence and documentation. This phase should repair integration defects only; it must not reopen product architecture or broaden scope.

### File Changes

- `tests/test_multi_client_system.py` (new) — use real localhost TLS sockets to exercise two complete Clients, replacement timeout, topology distribution, input routing, clipboard races, file relay, and isolated disconnect.
- `tests/test_firewall.py`, `tests/test_windows_firewall.py`, `tests/test_ports.py`, `tests/test_release_packaging.py` — prove the feature does not add ports or broaden the managed firewall rule and that packaging contains any new Windows modules/resources.
- `Conduit.spec` — include new modules or Windows runtime hooks only where PyInstaller discovery requires it.
- `README.md`, `CONTRIBUTING.md` if needed — update the user workflow and manual validation instructions without exposing development-only IPs.
- `docs/plans/multi-client-topology/VALIDATION.md` (new during implementation) — record the Windows three-PC matrix and sanitized outcomes.

### Validation

#### Automated

- [ ] `.\venv\Scripts\python.exe -m unittest tests.test_multi_client_system tests.test_firewall tests.test_windows_firewall tests.test_ports tests.test_release_packaging -q` — proves system, firewall, and package contracts.
- [ ] Shared landing gate — final full automated acceptance.
- [ ] `powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -DevelopmentBuild` — after the worktree is otherwise suitable and NSIS is available, proves the packaged executable contains the new runtime and preserves release gates.

#### Evals / Regression Checks

- [ ] The managed rule remains executable-specific, Private-network-only, local-subnet-only, and exactly the same three consecutive TCP ports.
- [ ] Logs identify machine/session/revision/job/phase with safe IDs but never include passwords, tokens, clipboard contents, file contents, private paths, or user-facing development IPs.
- [ ] A one-Server/one-Client setup remains supported after the two-Client architecture lands.

#### Manual

- [ ] Run one Server and two physical Client PCs through mixed DPI, rotated and multi-monitor layouts; Client1↔Client2 transitions; local Client input; shared copy from every source; paste to every destination; disconnect; replacement timeout; reload; shutdown; and background mode.
- [ ] Unplug a physical monitor while active, confirm the warning/draft behavior, then Apply and confirm the rebuilt boundaries.
- [ ] Verify temporary gray/blue/green/purple identity colors and primary-display toast lifetime on the correct PCs.

## Dependency and Landing Rationale

- Phase 1 precedes networking because the session registry needs a stable machine/display and draft-placement contract.
- Phase 2 precedes all three-PC behavior because input, clipboard, and files must target isolated ready sessions rather than raw singleton sockets.
- Phase 3 lands before shared data workflows so cursor ownership and destination identity are explicit when file paste routing is generalized.
- Phases 4 and 5 can be reviewed separately: ordinary clipboard is latency-sensitive latest-state delivery; files are on-demand jobs with staging and cancellation.
- Phase 6 is intentionally late because it composes already-tested pause/resume interfaces instead of introducing topology, networking, clipboard, and file state in one transaction-sized change.
- Phase 7 is evidence and packaging, not a place for new architecture.

## Open Questions

None. Product and architecture choices are resolved in the accepted design. Review is limited to whether these phase boundaries are small enough to land and verify independently.

## Stop Gate

Outline gate passed on 2026-08-24. The next planning stage may write the executor plans. Do not modify application source while writing those plans.
