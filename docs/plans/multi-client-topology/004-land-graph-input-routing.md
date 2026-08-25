# Plan 004: Route the Server-owned cursor across the active display graph

> **Executor instructions:** Execute test-first. Run each verification gate. Stop and write a handback if the live architecture creates an unplanned routing or input-ownership fork.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- app/input_router.py app/display_topology.py app/server.py app/client.py app/input_handler.py app/input_geometry.py tests/test_input_router.py tests/test_input_geometry.py tests/test_emergency_release.py tests/test_input_numpad_forwarding.py tests/test_input_delete_forwarding.py tests/test_overlay_motion.py`
>
> Expected dependency drift: Plan 002 adds the topology model and Plan 003 replaces Server singleton lanes with ready `ClientSession` lookups. Confirm both outcomes. Stop if input already depends on raw socket objects or topology drafts.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** `002-land-single-client-topology.md`, `003-land-two-client-sessions.md`
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

Two connected Clients are useful only if one Server-owned cursor can move across their validated topology. The current Server toggles one `switching_to_client` boolean and sends every event through one peer. This plan gives routing one explicit owner, preserves Windows-native movement inside a machine, and releases all held input before every inter-machine transition.

## Current state

- `app/server.py:123` stores one `switching_to_client` flag.
- `app/server.py:243-298` checks one configured edge, switches one Client, and restores one Server edge.
- `app/server.py:300-391` forwards mouse/keyboard events and tracks forwarded keys for release.
- `app/client.py:430-520` installs one relative layout, injects one remote stream, releases injected keys, and reports one return edge.
- `app/input_handler.py:61-148` owns physical edge detection and keyboard capture; `:175-294` owns injection and release state.
- `tests/test_emergency_release.py:26-112` pins release-before-switch, reload, and shutdown behavior. Preserve those observable orderings.
- `tests/test_input_geometry.py:13-88` tests edge entry and work-area geometry as pure functions.

After dependencies, `ActiveTopology` is the only routable graph and `SessionRegistry` exposes at most two ready Clients by stable ID. Draft topology must never reach this plan's router.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_input_router tests.test_input_geometry tests.test_emergency_release tests.test_input_numpad_forwarding tests.test_input_delete_forwarding -q` | All pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |

## Scope

**In scope:**

- `app/input_router.py` (new)
- `app/display_topology.py`
- `app/server.py`
- `app/client.py`
- `app/input_handler.py`
- `app/input_geometry.py`
- `tests/test_input_router.py` (new)
- `tests/test_input_geometry.py`
- `tests/test_emergency_release.py`
- `tests/test_input_numpad_forwarding.py`
- `tests/test_input_delete_forwarding.py`
- `tests/test_overlay_motion.py`

**Out of scope:**

- Clipboard and file destination policy — later plans consume the router's active destination but do not belong here.
- Draft editing or Apply distribution — Plan 007 owns the final cross-service transaction.
- Multiple roaming cursors or Client-owned roaming input.
- Partial-edge or T-junction routing; topology validation already rejects both.

## Steps

### Step 1: Write behavior tables for graph routing

Add `tests/test_input_router.py` through the public router interface. Cover:

- Server→Client, Client→Server, and Client1→Client2 transitions;
- Client1→Client2 messages relay through the Server without a Server injection event;
- movement within two physical displays on one PC stays local and never selects a Conduit session;
- the outer edge of an oddly shaped machine group is the only eligible transition edge;
- full-edge coordinate ratios map into the destination display and scale for resolution/DPI while preserving perceived Server speed;
- corner coordinates clamp safely;
- a disconnected destination releases input and returns to Server-primary center;
- local Client mouse/keyboard events never mutate router state;
- repeated or stale transition messages cannot change the current destination.

Use recording session/input fakes at the router's real dependency seams, not mocks of private helpers.

**Verify:** run the focused command. Expected: new router tests fail because `InputRouter` does not exist; existing input tests pass.

### Step 2: Implement the explicit routing state machine

Create `app/input_router.py`. Use one lifecycle state rather than booleans:

```text
LocalServer(display_id, position)
RemoteClient(session_id, display_id, position)
Transitioning(source, destination, released_state)
Paused(reason)
```

The router owns the logical cursor location, active machine/display, target `ClientSession`, transition serialization, forwarded keys/buttons, and return-to-Server policy. It consumes immutable `ActiveTopology` queries and narrow session send capabilities. It must not import Tk, raw network messages, or Win32 discovery DTOs.

Expected transition contract:

1. Resolve a complete active edge and destination.
2. Release tracked/injected keys and buttons on the old destination.
3. Install the new destination and mapped entry position.
4. Start forwarding only after release completes.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_input_router -q` → state, mapping, stale-event, and failure tests pass.

### Step 3: Expose graph queries from active topology

Add only the queries `InputRouter` needs: locate machine/display at logical point, resolve an inter-machine full edge, map source ratio to destination coordinates, and find Server primary center. Keep adjacency construction and validation inside `display_topology.py`.

Do not add generic graph traversal APIs that expose internal storage or let callers route drafts.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_display_topology tests.test_input_router -q` → topology and router tests pass.

### Step 4: Move Server input policy behind the router

In `ConduitServer`, replace `switching_to_client`, scalar layout branches, active peer fields, and direct destination selection with router commands. Network callbacks translate validated message fields into router events tagged by session and display identity.

Server physical input remains the only roaming source. When the active destination is Client1 and its outward edge touches Client2, Client1 reports the edge event to the Server; the router releases Client1 and starts targeted forwarding to Client2. Do not inject that intermediate event on the Server.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_input_router tests.test_emergency_release tests.test_overlay_motion -q` → public Server routing and overlay behavior pass.

### Step 5: Narrow Client and InputHandler responsibilities

Update `ConduitClient` to install its targeted active graph slice, report return/forward edge events with stable display identity and normalized ratio, and release all injected keys and buttons before acknowledging any transition. Preserve local input isolation and native movement among that Client's physical displays.

Keep `InputHandler` responsible for physical capture, injection, and local release bookkeeping. Remove destination selection and one-screen authority from it after all callers move. Preserve special-key injection and numpad/Delete serialization exactly.

**Verify:** run the focused command → all focused input tests pass.

### Step 6: Prove failure cleanup and run the landing gate

Add tests for disconnect during a held modifier/button, transition during reload, destination loss during Client1→Client2, and transition rejection while the router is paused. The cursor must end at Server-primary center with empty held-input state.

**Verify:** run compile, full suite, and whitespace commands → all pass.

## Test plan

- Use table tests for every ordered machine pair and edge orientation.
- Test coordinate mapping with mixed resolution, effective DPI, rotations, and negative native rectangles.
- Extend emergency-release tests to buttons as well as keys and to every normal transition.
- Test stale session IDs and topology versions through the router interface.
- Preserve existing special-key/numpad/Delete tests without exposing router internals.

## Done criteria

- [ ] Focused tests, compileall, full suite, and whitespace checks pass.
- [ ] One Server-owned cursor crosses any valid active inter-machine edge.
- [ ] Client1→Client2 travels through the Server hub without Server cursor appearance.
- [ ] Same-PC multi-monitor movement stays outside Conduit routing.
- [ ] Every transition and destination loss releases held keys/buttons first.
- [ ] No scalar `switching_to_client` or routing `layout_position` remains.
- [ ] No file outside scope changed.

## STOP conditions

Stop and write a handback if:

- Plan 002 lets draft topology reach runtime input;
- Plan 003 callbacks lack stable session identity;
- Windows cursor ownership requires Client-local input to join the roaming stream;
- coordinate scaling cannot satisfy both topology mapping and current perceived-speed behavior without a product decision;
- a transition can only work by leaving keys/buttons pressed on the old destination;
- tests require exposing private router state as production API;
- verification fails twice or scope must expand.

## Maintenance notes

`InputRouter` owns routing policy and state; `InputHandler` owns device effects. Reviewers should reject destination logic in GUI/network callbacks and raw topology dictionaries in the router. Clipboard and file plans may query active destination identity but must not mutate cursor state.
