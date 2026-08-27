# Plan 010: Stop routing on Client loss and rebuild topology through Reset

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and write a handback; do not
> improvise. When done, update this plan's row in the effort README.
>
> **Drift check (run first)**: `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 681d8da -- app/server.py app/client.py app/gui.py app/topology_editor.py tests/test_topology_apply.py tests/test_topology_protocol.py tests/test_gui_connection_lifecycle.py tests/test_topology_editor.py`
> If these paths changed since this plan was written, compare the current-state
> facts below with the live code before proceeding. A semantic mismatch is a
> STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 007-land-atomic-apply.md
- **Planned at**: revision `681d8da`, 2026-08-26
- **Implemented**: 2026-08-26; disconnect-race hardening verified 2026-08-27

## 2026-08-27 implementation reconciliation

Physical rapid-handoff testing disproved this plan's original assumption that
calling `InputRouter.pause()` was sufficient. A graph transition holds the
router ownership lock while it releases the old owner and starts the new one.
If Client teardown enters `pause()` during that interval, the disconnect
callback can wait behind the transition: cursor recovery, edge shutdown,
survivor suspension, and the Server GUI notification are all delayed together.

The accepted safety behavior now has two phases:

1. `InputRouter.request_pause()` sets a thread-safe rejection event without
   acquiring the router lock. Every edge and forwarded-input entry point checks
   this event, including checkpoints inside an in-flight transition.
2. `ConduitServer.suspend_input_routing()` synchronously latches routing off,
   stops Server capture, centers the cursor, and sends `topology_suspend` to
   surviving Clients. It then finishes held-input release and the formal
   `Paused` state on a daemon cleanup thread after the router lock becomes
   available. Cleanup cannot delay the disconnect callback or reopen edges.

This evidence authorizes the previously out-of-scope `app/input_router.py` and
`tests/test_input_router.py` changes. It does not change clipboard routing and
does not implement Plan 009's deferred button-label lifecycle.

## Why this matters

The active input graph currently survives the loss of a non-owning Client. A
later edge transition can therefore target a dead bridge, strand the cursor,
or leave injected input held. Disconnect cleanup can also remove a Client's
color while stale draft cells still reference it, raising `KeyError` from
`TopologyEditorState.cell_views()` and blanking the editor. This plan makes any
ready-Client loss a persistent input-routing safety stop, returns control to
the Server primary display, and requires an authoritative Reset before input
routing can resume.

## Current state

- `app/server.py:336-377` installs and restores input routers. Restoring a
  previous topology currently starts edge detection even after a disconnect.
- `app/server.py:429-657` implements atomic Apply and rolls back to the old
  topology on failure. It has no persistent disconnect-suspension latch.
- `app/server.py:716-757` calls `InputRouter.destination_lost(session_id)`.
  That method only reacts if the lost session owns the cursor; a lost bridge
  leaves the rest of the graph live.
- `app/input_router.py:209-220` already supplies the desired primitive:
  `pause(reason)` releases remote/local held input, returns to Server center,
  and rejects graph transitions until resumed. Reuse it; do not duplicate
  release logic.
- `app/client.py:588-640` releases injected input and disables active routing
  during topology prepare/commit/rollback. Match this convention for an
  explicit best-effort `topology_suspend` control message.
- `app/gui.py:1569-1615` removes the disconnected Client from the draft and
  foregrounds the Server window, but it cannot reconstruct an absent Server
  anchor.
- `app/gui.py:1392-1451` rescans by calling `refresh_machine()`, which returns
  false when a machine is missing; Stop/Start and tab changes therefore cannot
  recover an empty editor.
- `app/topology_editor.py:162-226` removes a Client color and reads colors by
  direct dictionary indexing. The field trace shows `cell_views()` raising
  `KeyError` for a disconnected machine during both Cancel and removal.
- Test conventions: build lightweight server/GUI instances with `__new__` and
  fakes as in `tests/test_topology_apply.py:128` and
  `tests/test_gui_connection_lifecycle.py:328`; build real draft groups with
  the `machine()` helper in `tests/test_topology_editor.py:17`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_topology_apply tests.test_topology_protocol tests.test_gui_connection_lifecycle tests.test_topology_editor -q` | all pass |
| Input-router regression | `.\venv\Scripts\python.exe -m unittest tests.test_input_router -q` | all pass |
| System seam | `.\venv\Scripts\python.exe -m unittest tests.test_multi_client_system -q` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | no output |

## Scope

**In scope** (the only files you should modify):

- `app/input_router.py` (added by the 2026-08-27 reconciliation)
- `app/server.py`
- `app/client.py`
- `app/gui.py`
- `app/topology_editor.py`
- `tests/test_topology_apply.py`
- `tests/test_topology_protocol.py`
- `tests/test_gui_connection_lifecycle.py`
- `tests/test_topology_editor.py`
- `tests/test_topology_reconnect.py`
- `tests/test_input_router.py` (added by the 2026-08-27 reconciliation)
- `docs/plans/multi-client-topology/010-stop-routing-and-reset-topology.md`
- `docs/plans/multi-client-topology/README.md`

**Out of scope**:

- Clipboard ordering and ordinary clipboard fan-out — surviving endpoints keep
  using the existing independent clipboard lane.
- Ctrl+Space, Space recovery shortcut — deferred to Plan 009.
- DPI/grid geometry — Plan 011 owns that independently.

## Steps

### Step 1: Reproduce topology-state corruption before changing production code

Add failing tests to `tests/test_topology_editor.py` proving that:

- removing a Client from a draft that can be concurrently reset/cancelled never
  leaves a cell without a color;
- rendering/cell-view creation self-heals or safely assigns a deterministic
  available Client color when a draft contains a legitimate Client unknown to
  the color cache;
- an authoritative draft rebuild always restores the fixed Server at `(0, 0)`,
  drops absent Clients, preserves surviving Client placements, refreshes all
  supplied display groups, and retains `active` until a successful Apply.

Run the focused editor tests and confirm at least one new test fails for the
reported `KeyError` or missing reconstruction API before production edits.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor -q` → RED for the new assertions, with existing tests still passing.

### Step 2: Make editor reconciliation atomic and rendering total

In `app/topology_editor.py`, add one state-level reconciliation operation that
accepts the authoritative Server group plus currently ready Client groups and
optional saved/current placements. It must build a complete new draft before
publishing it, keep the Server fixed at `(0, 0)`, preserve valid surviving
positions, use existing default-placement priority for new Clients, and update
the color cache in the same state transition. `cell_views()` must never throw
because a legitimate draft Client lacks a cached color; assign from the three
existing colors deterministically. Ensure remove, cancel, commit, and rebuild
cannot expose mismatched draft/color state.

Do not silently mutate `active`. Only successful Apply commits candidate state.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor -q` → all pass.

### Step 3: Reproduce and implement the persistent cluster input-suspension latch

First add failing tests in `tests/test_topology_protocol.py` and
`tests/test_topology_apply.py` proving that any ready-session loss:

- pauses the Server router even when the lost session did not own the cursor;
- stops edge detection/capture and therefore prevents leaving the Server;
- releases held input and returns the Server cursor to primary center through
  the existing router/effects path;
- sends `topology_suspend` best-effort to surviving ready Clients;
- leaves clipboard service alive for surviving endpoints;
- remains suspended across failed Apply/rollback and topology restoration;
- clears suspension only after a fully persisted and installed successful
  Reset/Apply, including a Server-only topology with zero Clients.

Add a Client protocol test showing `topology_suspend` releases injected input,
sets `is_active=False`, clears pending routing state as needed, and stops edge
detection without disconnecting clipboard/data lanes.

Then implement the smallest explicit Server latch in `app/server.py` and the
Client callback in `app/client.py`. Suspension must be idempotent because three
lane receive loops may report one physical disconnect. Normal `Server.stop()`
must not emit a user-visible disconnect warning or accidentally resume routing.
`_install_topology()` and `_restore_topology()` must honor the latch; no helper
such as daemon-mode restoration may call `resume()` while it is set.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_topology_apply tests.test_topology_protocol tests.test_input_router -q` → all pass.

### Step 4: Turn Apply into authoritative Reset and repair lifecycle recovery

Add failing GUI lifecycle tests before production edits. They must cover:

- a Client disconnect removes only that Client from the draft, asks the Server
  to suspend input, updates status, and foregrounds the Server GUI;
- Reset discovers the current Server displays, requests/waits for inventories
  from every ready Client, reconciles one authoritative draft, validates it,
  and then invokes the existing atomic Apply transaction;
- an invalid or failed Reset leaves input suspended and the previous active
  topology stored but not routable;
- successful Reset clears identification toasts and makes the candidate active;
- Stop/Start Server or returning to the Server tab reconstructs the gray Server
  anchor even when the previous editor draft is empty/corrupt;
- intentional Client Disconnect still triggers the safety stop but suppresses
  the transient unexpected-disconnect warning.

Rename the visible editor button from `Apply` to `Reset`. Keep Cancel semantics:
it restores the active draft only when that state is complete; after a safety
stop, it must not reactivate routing. Replace refresh-in-place calls with the
authoritative reconciliation path where absence is possible.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_gui_connection_lifecycle tests.test_topology_editor tests.test_topology_apply tests.test_topology_protocol -q` → all pass.

### Step 5: Run the complete landing gate

Run the focused, router, system, compile, whitespace, and full-suite commands.
Update the README row to DONE only after all gates pass. Plan 008 then remains
blocked only on Plan 011 and subsequent physical acceptance.

**Verify**: `.\venv\Scripts\python.exe -m unittest discover -s tests -q` → all tests pass.

## Test plan

- Editor regression tests for the exact missing-color `KeyError`, atomic
  reconstruction, missing Server anchor, surviving positions, and absent Client
  removal.
- Protocol tests for Server suspension fan-out and Client suspension handling.
- Apply tests for failed rollback staying suspended and successful Reset being
  the only resume path.
- GUI lifecycle tests for foregrounding, status, Stop/Start reconstruction,
  renamed Reset control, and intentional versus unexpected disconnect text.
- Reconnect/rescan tests use the authoritative reconciliation contract instead
  of the superseded refresh-in-place test double.
- Existing router, topology, clipboard, file, and real-TLS system tests remain
  green.

## Done criteria

- [x] Any ready Client loss immediately prevents every new edge transition.
- [x] Held input is released and the cursor returns to Server primary center.
- [x] The editor does not raise `KeyError` or go blank during disconnect/Cancel.
- [x] Reset reconstructs Server plus all currently ready Clients from fresh
  inventories and resumes routing only after atomic success.
- [x] Clipboard among surviving connected machines remains available.
- [x] Focused, system, full-suite, compileall, and `diff --check` gates pass:
  745 tests plus 150 repeated race-regression executions on 2026-08-27.
- [x] Production and regression changes stay within the reconciled scope.

Physical three-PC acceptance remains Plan 008's release gate, not an incomplete
Plan 010 implementation step.

## STOP conditions

Stop and write a handback if:

- `InputRouter.pause()` does not release all held keys/buttons and return to the
  Server primary center as its existing tests claim.
- Clipboard polling cannot remain active while input routing is suspended.
- A correct Reset requires changing the wire authentication or lane-binding
  protocol.
- The live Apply transaction differs semantically from the current-state facts.
- A verification fails twice after a reasonable focused fix.

## Maintenance notes

The suspension latch is a safety boundary. Future topology, background-mode,
reload, and reconnect code must not resume input independently; only successful
authoritative Reset may clear it. Keep protocol suspension idempotent because
multiple lane teardown callbacks are expected for one Client loss.
