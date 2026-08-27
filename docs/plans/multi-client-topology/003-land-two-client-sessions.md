# Plan 003: Keep two isolated Client sessions on the existing ports

> **Executor instructions:** Execute test-first. Run every verification command. Stop and write a handback if a STOP condition occurs. Update this plan's README row when the work lands.
>
> **Step handoff checkpoint:** After completing and verifying every numbered Step, create a new append-only `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update `handoffs/index.md` before starting the next Step. Record the exact verification result, branch/SHA and working-tree state, decisions, remaining work, and the next Step. Do not overwrite an earlier handoff or commit handoff files unless the user explicitly requests it.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- app/session.py app/network.py app/file_transfer/transport.py app/server.py app/client.py app/gui.py app/topology_editor.py app/topology_toast.py tests/test_client_session_registry.py tests/test_security_session.py tests/test_security_network.py tests/test_security_full_session.py tests/test_file_transfer_network_identity.py tests/test_gui_connection_lifecycle.py`
>
> Expected dependency drift: Plan 002 changes `server.py`, `client.py`, `gui.py`, `topology_editor.py`, and `topology_toast.py` to support one topology-aware Client. Confirm those changes match Plan 002's accepted outcome. Stop on unrelated lane, authentication, or listener changes.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** `002-land-single-client-topology.md`
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

The Server's listeners currently accept candidates concurrently but attach only one active socket per lane. A second Client therefore replaces or collides with the first. This plan introduces a bounded session registry and per-connection lane ownership so two complete Client bundles can coexist without adding ports, weakening trust, or letting one Client's failure reset the other.

## Current state

- `app/network.py:147-300` gives `NetworkNode` one socket, generation, heartbeat worker, and callback set.
- `app/network.py:309-519` lets `NetworkServer` accept candidates but stores one `session_id` and attaches the accepted socket to itself.
- `app/session.py:27-95` stores one `_active_session` and purpose-bound tokens; new control authentication clears prior state.
- `app/file_transfer/transport.py:88-164` gives `_FileLane` one socket generation; `FileLaneServer` therefore owns one live peer.
- `app/server.py:28-123` composes one control, one clipboard-data, and one file lane.
- `app/client.py:205-396` correctly remains a one-Server endpoint and waits for all three lanes before declaring readiness.
- `tests/test_security_network.py:89-349` tests real candidate authentication, timeout, stale socket generations, and privacy-safe errors. Preserve these claims while widening capacity.
- `tests/test_security_full_session.py:36-136` proves one control session owns its data and file lanes. Generalize this real-socket seam to two bundles.

After Plan 002, a Client also has a stable trusted identity, reported display group, draft placement, slot color, and topology toast. `ClientSession` must own those facts alongside its three lanes.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_client_session_registry tests.test_security_session tests.test_security_network tests.test_security_full_session tests.test_file_transfer_network_identity -q` | All focused tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |

## Scope

**In scope:**

- `app/session.py`
- `app/network.py`
- `app/file_transfer/transport.py`
- `app/server.py`
- `app/client.py`
- `app/gui.py`
- `app/topology_editor.py`
- `app/topology_toast.py`
- `tests/test_client_session_registry.py` (new)
- `tests/test_security_session.py`
- `tests/test_security_network.py`
- `tests/test_security_full_session.py`
- `tests/test_file_transfer_network_identity.py`
- `tests/test_gui_connection_lifecycle.py`

**Out of scope:**

- Graph cursor routing — Plan 004 owns destination transitions.
- Three-PC clipboard broadcast — Plan 005 owns `ClipboardHub`.
- Client-to-Client file jobs — Plan 006 owns job routing.
- Extra ports, multiplexing all traffic onto one socket, per-Client passwords, or direct Client sockets.
- More than two active Clients.

## Steps

### Step 1: Pin the two-session lifecycle with failing tests

Add `tests/test_client_session_registry.py` with a deterministic fake clock. Cover:

- two control sessions authenticate with the same Server password and different trusted peer identities;
- lane tokens bind to session, purpose, peer identity, and existing address rules;
- partial lane bundles time out independently;
- readiness requires all three lanes;
- closing one session leaves the other ready;
- one pending third candidate waits at most 15 seconds and consumes no active slot;
- a second pending candidate is rejected immediately;
- replace Client 1, replace Client 2, reject, and timeout are explicit outcomes;
- timeout closes every candidate lane and releases all registry resources;
- duplicate Windows names receive Conduit-only suffixes while stable identity remains authoritative.

Extend real-socket tests so two bundles connect simultaneously. Keep current wrong-password and pairing tests intact.

**Verify:** run the focused test command. Expected: new capacity and lifecycle assertions fail; current one-session tests pass.

### Step 2: Replace singleton session state with an explicit registry

Refactor `app/session.py` around a bounded `SessionRegistry`. Model legal phases explicitly rather than combining booleans and nullable lanes. One cohesive `ClientSession` value should own:

```text
stable peer identity
display name and unique Conduit label
control/data/file lane handles and binding state
slot and color assignment
reported display inventory and draft placement
session cancellation/cleanup state
```

The registry owns capacity, token issuance/consumption, the 15-second candidate timer, replacement decisions, and cleanup. Use a caller-owned clock/scheduler seam so timeout tests do not sleep. Keep the shared password behavior and per-device pending trust commit.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_client_session_registry tests.test_security_session -q` → all registry and token tests pass.

### Step 3: Separate listener lifetime from connection lifetime

Refactor `NetworkServer` so the listener accepts many candidates but never uses its own singleton socket as the accepted peer. Create a per-connection object only if it owns meaningful socket generation, receive loop, heartbeat, callbacks, and cleanup. The listener must retain and close those objects by session/lane identity.

Every callback into `ConduitServer` must include the refined session identity; raw address tuples and wire dictionaries stay at the network boundary. A stale receive loop may close only its own connection. Candidate-worker concurrency must remain bounded and preserve existing handshake deadlines and safe error mapping.

Do not keep a permanent legacy singleton path “just in case.” Update current one-Client callers to use the registry path.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_security_network tests.test_security_full_session -q` → all socket, pairing, and two-bundle tests pass.

### Step 4: Bind file lanes per session

Refactor `FileLaneServer` to keep one listener and one authenticated connection object per ready session. Preserve frame bounds, TLS fingerprint checks, purpose-bound token consumption, and generation-safe cleanup. One Client's file-lane loss must update only that `ClientSession`.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_file_transfer_network_identity tests.test_security_full_session -q` → both session bundles bind correct file lanes and reject cross-session use.

### Step 5: Compose two ready sessions in the Server

Replace `ConduitServer`'s singleton lane fields with registry/session lookups. Route callbacks by stable session ID. Keep the Client process as one Server connection. Ensure startup, stop, reload, and shutdown enumerate owned sessions and clean each independently.

When a ready Client arrives, add its group to the topology draft automatically. It stays unroutable until Apply. A returning trusted Client restores its saved draft placement when possible; no reconnect mutates the active graph.

**Verify:** run the focused test command → all focused tests pass.

### Step 6: Implement the bounded replacement interaction

On the Server only, show the two-Client limit and replace Client 1 / replace Client 2 / reject actions. On the candidate's primary display, show the purple identification toast for the 15-second decision window. If selected, keep the candidate purple and draft-only until successful Apply; then inherit the evicted blue/green slot color. Timeout or reject closes candidate resources and dismisses its toast.

Replacing a Client must release its input, cancel its transfer jobs through the existing one-peer cleanup hook, disconnect all lanes, and remove it from active routing before admitting the candidate to the draft. Later plans generalize input/file cleanup; preserve an explicit hook now.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_client_session_registry tests.test_gui_connection_lifecycle -q` → replacement, timeout, stale callbacks, color, and prompt tests pass.

### Step 7: Run the complete landing gate

Search for listener-owned active peer state that escaped the refactor. Any remaining singleton is acceptable only on the Client side, which connects to one Server.

**Verify:**

- `rg -n "_active_session|self\.session_id|self\.session_offer" app/session.py app/network.py app/file_transfer/transport.py app/server.py` → no Server-side singleton ownership remains unless documented as candidate-local state.
- Run compile, full suite, and whitespace commands → all pass.

## Test plan

- Use a fake clock for registry deadlines; never sleep 15 seconds in unit tests.
- Generalize `tests/test_security_full_session.py` to two real TLS bundles on the same three listeners.
- Extend stale-generation tests so one Client's delayed callback cannot close or relabel another.
- Cover wrong password, declined pairing, changed identity, cross-session tokens, partial bundles, isolated disconnect, candidate replacement, reject, and timeout.
- Test GUI replacement through public callbacks and view state, not Tk internals.

## Done criteria

- [ ] Focused tests pass.
- [ ] Compileall, full suite, and whitespace checks pass.
- [ ] Two complete Clients remain ready concurrently on the same three ports.
- [ ] A third candidate is bounded to one prompt and 15 seconds.
- [ ] One session's disconnect, stale callback, or authentication failure cannot disturb the other.
- [ ] No Server-side singleton peer path remains.
- [ ] No file outside scope changed.

## STOP conditions

Stop and write a handback if:

- retaining multiple sockets requires a fourth port or weakens lane/session binding;
- a shared listener cannot identify and isolate a lane before it mutates active session state;
- first-time pairing cannot remain per physical device under one shared password;
- replacement cannot cleanly identify the exact session to evict;
- Plan 002 did not produce stable machine identity and draft placement seams;
- tests require real 15-second sleeps or expose private socket helpers as production API;
- verification fails twice or an out-of-scope file must change.

## Maintenance notes

The listener owns acceptance; `ClientSession` owns peer resources; the registry owns lifecycle and capacity. Keep those lifetimes distinct. Future routing code must consume ready sessions by stable ID, never raw sockets or connection order.
