# Plan 007: Activate topology through one atomic cluster Apply

> **Executor instructions:** Execute test-first. This plan composes existing services; it must not redesign them inside one transaction. Run every gate. At an unplanned failure or ordering fork, stop and write a handback.
>
> **Step handoff checkpoint:** After completing and verifying every numbered Step, create a new append-only `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update `handoffs/index.md` before starting the next Step. Record the exact verification result, branch/SHA and working-tree state, decisions, remaining work, and the next Step. Do not overwrite an earlier handoff or commit handoff files unless the user explicitly requests it.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- app/topology_service.py app/display_topology.py app/topology_editor.py app/topology_toast.py app/gui.py app/server.py app/client.py app/preferences.py app/input_router.py app/clipboard_hub.py app/file_transfer/cluster_router.py tests/test_topology_apply.py tests/test_topology_reconnect.py tests/test_topology_editor.py tests/test_gui_connection_lifecycle.py tests/test_emergency_release.py tests/test_clipboard_hub.py tests/test_cluster_file_routing.py`
>
> Expected dependency drift: Plans 002–006 create topology, session, input, clipboard, and file service interfaces. Confirm each exposes explicit pause/resume or cleanup commands and that no draft can reach active routing outside one activation owner.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** `002-land-single-client-topology.md`, `003-land-two-client-sessions.md`, `004-land-graph-input-routing.md`, `005-land-global-clipboard.md`, `006-land-file-relay-and-cluster-commands.md`
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

The feature's safety depends on one rule: only a fully validated, distributed, acknowledged topology may replace the active graph. Draft editing must leave the old system running, while valid Apply briefly pauses Conduit-controlled routing and delivery without blocking Windows copy capture. This plan gives that workflow one owner, one rollback path, and tests across every participating service.

## Current state

- Plan 002 is expected to provide `DraftTopology`, `ValidatedTopology`, `ActiveTopology`, compact editor state, persistence, and one-Client acknowledgement.
- Plan 003 is expected to provide ready `ClientSession` objects, draft-only new/reconnecting Clients, replacement lifecycle, and stable slot colors.
- Plan 004 is expected to provide `InputRouter.pause()`, release/center behavior, active-graph install, and resume.
- Plan 005 is expected to provide `ClipboardHub.pause_delivery()` / resume behavior while local capture and latest-wins submit continue.
- Plan 006 is expected to provide file scheduler quiescence, new-job rejection, resume, and endpoint job cleanup.
- `app/preferences.py:31-129` currently owns atomic JSON-like preference reads/writes; successful topology persistence must remain behind that boundary.
- `tests/test_gui_connection_lifecycle.py:15-55` pins stale callback isolation; apply acknowledgements need the same generation/version discipline.
- `tests/test_emergency_release.py:26-112` pins release-first behavior that Apply must preserve.

The accepted transaction appears in `docs/superpowers/specs/2026-08-24-multi-client-topology-design.md` under “Apply transaction.”

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_topology_apply tests.test_topology_reconnect tests.test_topology_editor tests.test_gui_connection_lifecycle tests.test_emergency_release tests.test_clipboard_hub tests.test_cluster_file_routing -q` | All pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |

## Scope

**In scope:**

- `app/topology_service.py` (new only if it earns a deep workflow boundary)
- `app/display_topology.py`
- `app/topology_editor.py`
- `app/topology_toast.py`
- `app/gui.py`
- `app/server.py`
- `app/client.py`
- `app/preferences.py`
- Narrow changes to `app/input_router.py`, `app/clipboard_hub.py`, and `app/file_transfer/cluster_router.py` only if their accepted participant contracts are incomplete
- `tests/test_topology_apply.py` (new)
- `tests/test_topology_reconnect.py` (new)
- `tests/test_topology_editor.py`
- `tests/test_gui_connection_lifecycle.py`
- `tests/test_emergency_release.py`
- `tests/test_clipboard_hub.py`
- `tests/test_cluster_file_routing.py`

**Out of scope:**

- New topology rules, extra Clients, extra ports, or different password/trust policy.
- Redesigning input, clipboard, or file service internals that already passed prior plans.
- Automatic active-graph mutation on connect, reconnect, replacement, or physical display loss.
- Clipboard FIFO/history or preserving remote cursor position across Apply.

## Steps

### Step 1: Write transaction and rollback tests first

Add `tests/test_topology_apply.py` with deterministic participants. Cover exact order and observable outcomes:

1. Rescan Server and all ready Clients.
2. Reconcile stable display IDs into the draft.
3. Validate the complete graph.
4. On invalid: return issues, mark invalid Client groups red, and never pause runtime.
5. On valid: release all input, center cursor on Server primary, then enter the barrier.
6. Pause input routing, clipboard broadcast delivery, and file scheduling; reject new file jobs.
7. Distribute one candidate version to all ready participants.
8. Require acknowledgements from every participant for that exact version.
9. Commit active graph and persistence, remove barrier, then deliver queued newest clipboard work and resume files/input.
10. On distribution/ack failure: restore the old graph everywhere that acknowledged, keep persistence unchanged, remove barrier, and resume old behavior.

Test validation failure, rescan failure, one Client disconnect, stale/wrong-version acknowledgement, participant timeout, persistence failure, rollback acknowledgement failure, and shutdown during Apply. Test copies before/during/after both success and rollback.

**Verify:** run the focused command. Expected: new Apply/reconnect tests fail; prior service tests pass.

### Step 2: Give the Apply workflow one deep owner

Use `TopologyService` only if it owns draft/active versions, participant coordination, acknowledgement tracking, persistence timing, rollback, and reconciliation. If `display_topology.py` already owns that workflow deeply after Plan 002, extend it instead of adding a pass-through wrapper. Keep pure validation separate from effect sequencing.

Use one explicit lifecycle:

```text
Editing(active, draft)
Validating(active, candidate)
Applying(active, candidate, version, acknowledgements)
RollingBack(active, failed_version)
Active(active)
```

Reject re-entrant Apply. Give every async participant operation a caller-owned deadline/cancellation path. Never hold a topology/persistence lock across network waits.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_topology_apply -q` → lifecycle, ordering, timeout, and rollback tests pass.

### Step 3: Reconcile physical display rescans without mutating active state

At Apply start, request native inventories from Server and every ready Client. Match stable display IDs, preserve unchanged physical-group arrangement, remove missing displays from the draft, and place newly discovered displays according to their real Windows adjacency.

Outside Apply, a display disconnect updates the visible draft and emits one Server warning toast. It leaves active calculations and routes unchanged; the cursor may enter the missing logical region until Apply. A reconnecting trusted Client restores its saved draft placement when compatible. New, returning, and replacement Clients always remain unroutable until successful Apply.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_topology_reconnect tests.test_topology_apply -q` → reconnect, replacement, display loss, and stable-ID reconciliation pass.

### Step 4: Wire the cluster barrier in the Server composition root

Make `ConduitServer` sequence effects through narrow participant capabilities. Release input and center before pausing delivery. Clipboard pause covers hub broadcast, not Windows observation or endpoint `submit()`. File pause rejects new jobs and quiesces scheduling at its tested safe boundary. Preserve the in-memory newest clipboard snapshot/revision throughout.

On resume, open services against either the committed graph or restored old graph. Do not resume a subset before the final outcome is known. If rollback itself partially fails, release input, keep the Server local, disconnect only inconsistent participants, report a safe Server error, and avoid persisting the candidate.

**Verify:** run focused tests → cross-service order, success, and rollback pass.

### Step 5: Finish editor, toast, and replacement state

Keep Client identification toasts visible throughout editing and failed Apply. Successful commit promotes a selected purple replacement to its evicted blue/green slot and dismisses draft toasts. Explicit Cancel discards draft and dismisses them. Candidate timeout/reject dismisses only the purple candidate toast.

Moving the Server-controlled cursor to another machine while editing performs Cancel before the transition. Failed validation marks only invalid Client groups after Apply; the Server stays gray. Two Clients disconnected from the Server component both turn red.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor tests.test_topology_reconnect tests.test_gui_connection_lifecycle -q` → UI lifecycle and stale callback tests pass.

### Step 6: Persist only committed topology

Write the active topology version only after all participants acknowledge. A failed persistence write must not leave the cluster believing an unpersisted graph committed: treat persistence as part of commit preparation or roll back participants before resuming. Preserve trust and clipboard memory; do not persist clipboard or draft state.

On startup, validate persisted topology against current trusted IDs and detected displays. Restore only a valid active graph; otherwise start with Server active and Clients draft-only.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_topology_apply tests.test_topology_reconnect tests.test_gui_preferences -q` → persistence timing and restart behavior pass.

### Step 7: Run the landing gate

Search for any code outside the topology owner that assigns active topology or persists a draft. Remove duplicated orchestration from GUI callbacks and lane handlers.

**Verify:** run focused tests, compile, full suite, and whitespace commands → all pass.

## Test plan

- Use deterministic participant fakes with recorded public effects and explicit acknowledgements.
- Test every failure point before and after the barrier separately.
- Race copy submissions and file paste attempts against Apply success and rollback.
- Test replacement, reconnect, physical display add/remove, stale acknowledgement, re-entrant Apply, Cancel, and cursor-away cancellation.
- Preserve all prior service suites to prove composition did not weaken their contracts.

## Done criteria

- [ ] Focused tests, compileall, full suite, and whitespace checks pass.
- [ ] Validation failure never pauses or changes active runtime.
- [ ] Successful Apply changes routing only after all acknowledgements and persistence readiness.
- [ ] Any failure restores the old active graph and delivery behavior or safely disconnects only inconsistent participants.
- [ ] Copies during Apply remain non-blocking and converge on newest pending delivery.
- [ ] New/reconnecting/replacement Clients stay draft-only until commit.
- [ ] Toast and invalid-outline lifetimes match the accepted design.
- [ ] No file outside scope changed.

## STOP conditions

Stop and write a handback if:

- prior services lack bounded pause/resume or cleanup contracts and need architectural redesign;
- a transaction requires holding locks across network acknowledgement waits;
- persistence cannot participate without a crash window that can activate an unpersisted graph;
- a Client cannot distinguish candidate topology version from active version;
- rollback can silently leave participants on different active graphs;
- clipboard capture must be stopped or file jobs cannot quiesce safely;
- verification fails twice or scope must expand materially.

## Maintenance notes

Apply is the only authority allowed to replace active topology. Keep validation pure, effects explicit, versions monotonic, and persistence after full readiness. New services that depend on topology must register as barrier participants rather than inserting ad hoc pause code into GUI callbacks.
