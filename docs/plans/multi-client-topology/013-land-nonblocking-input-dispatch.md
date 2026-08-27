# Plan 013: Keep every remote input write off GUI and hook threads

> **Executor instructions**: Follow this plan step by step and test-first. Run
> every verification command and confirm its expected result before moving on.
> If anything in "STOP conditions" occurs, stop and write a handback; do not
> improvise. Keep every commit green and narrowly scoped. When done, update
> this plan's status row in the effort README.
>
> **Step handoff checkpoint:** After completing and verifying every numbered
> Step, create a new append-only
> `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update
> `handoffs/index.md` before starting the next Step. Record exact verification,
> branch/SHA and working-tree state, decisions, remaining work, and the next
> Step. Do not overwrite or commit handoff files unless the user requests it.
>
> **Drift check (run first)**:
> `git -c safe.directory=C:/Users/parth/Projects/Conduit diff e69fdcf -- app/input_dispatcher.py app/input_router.py app/client.py app/server.py tests/test_input_dispatcher.py tests/test_input_router.py tests/test_topology_protocol.py tests/test_multi_client_system.py docs/plans/multi-client-topology/013-land-nonblocking-input-dispatch.md docs/plans/multi-client-topology/README.md`
> Expected drift: completed Plan 012 changes router, Server, Client, and their
> tests to require acknowledgement. Rebase the current-state facts onto that
> exact result. Any unrelated semantic drift is a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 012-land-acknowledged-cursor-handoff.md
- **Planned at**: revision `e69fdcf`, 2026-08-27

## Why this matters

Acknowledgement prevents capture for a dead destination, but an active Client
can still disappear after ownership commits. Tk overlay callbacks currently
write mouse packets synchronously through the router. A stalled TLS write can
freeze the GUI before heartbeat teardown. This plan adds isolated, bounded
per-session workers so callback threads enqueue and return immediately while
the existing heartbeat remains free to close a dead socket.

## Current state

- `app/server.py:929-1017` routes overlay mouse, click, scroll, and keyboard
  callbacks directly into router forwarding methods.
- `app/input_router.py:164-213` and `378-400` hold the router lock while calling
  the active session lane synchronously.
- `app/network.py:221-243` serializes `sendall()` under `_send_lock`; this method
  remains the worker's low-level send primitive.
- `app/client.py:815-843` accepts one `mouse_move` delta at a time and runs
  `inject_move()`, which performs Windows clamping and topology edge checks.
- `tests/test_network_sending.py:72-87` proves low-level TLS writes serialize;
  do not replace or weaken that lock.
- `tests/test_input_router.py:645-661` is the current failed-forward-send
  recovery exemplar.
- The reviewed design requires 256 ordered discrete records, at most 512
  pending movement deltas, batches of at most 32 original delta pairs, and no
  naive delta addition.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Dispatcher tests | `.\venv\Scripts\python.exe -m unittest tests.test_input_dispatcher -q` | all pass |
| Router/Client tests | `.\venv\Scripts\python.exe -m unittest tests.test_input_router -q` | all pass |
| Protocol/system | `.\venv\Scripts\python.exe -m unittest tests.test_topology_protocol tests.test_multi_client_system -q` | all pass |
| Input regressions | `.\venv\Scripts\python.exe -m unittest tests.test_input_numpad_forwarding tests.test_input_delete_forwarding tests.test_overlay_motion tests.test_emergency_release -q` | all pass |
| Security regression | `.\venv\Scripts\python.exe -m unittest tests.test_security_error_redaction -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | no output |

## Scope

**In scope**:

- `app/input_dispatcher.py` (new)
- `app/input_router.py`
- `app/client.py`
- `app/server.py`
- `tests/test_input_dispatcher.py` (new)
- `tests/test_input_router.py`
- `tests/test_topology_protocol.py`
- `tests/test_multi_client_system.py`
- `docs/plans/multi-client-topology/013-land-nonblocking-input-dispatch.md`
- `docs/plans/multi-client-topology/README.md`

**Out of scope**:

- `app/network.py` — keep framing, heartbeat, and TLS send serialization.
- Clipboard/data/file senders — they already use separate service scheduling.
- GUI appearance, toast timing, topology geometry, firewall, ports, and Plan 009.

## Steps

### Step 1: Write failing dispatcher behavior tests

Create `tests/test_input_dispatcher.py` around the public behavior of a new
per-session dispatcher. Use controllable fake lanes and events. Prove:

- enqueue returns while one lane blocks;
- another session continues sending independently;
- discrete key/button/scroll records preserve FIFO order;
- movement batches retain every original relative delta in order, cap each
  batch at 32, and preserve their position relative to discrete records;
- at most 512 movement deltas and 256 discrete records are accepted;
- blocked overflow invokes failure exactly once and rejects later work;
- send failure invokes failure outside queue locks;
- stopping a session rejects new work without joining a blocked daemon worker.

Run before production edits and confirm RED for missing dispatcher behavior.

**Verify**: dispatcher command → RED only in new tests.

### Step 2: Implement the isolated bounded dispatcher

Create `app/input_dispatcher.py` with one daemon worker/queue per session.
Expose narrow operations for session start/stop and typed enqueue methods. The
dispatcher resolves the current authenticated lane immediately before every
send. Never call external failure callbacks while holding queue locks.

Represent movement compression as ordered batches of original `(dx, dy)`
pairs; never add them into one net delta. Bound pending counts independently.
When a discrete event arrives, close the current movement batch so click/key
ordering remains exact.

**Verify**: dispatcher tests → GREEN. Commit the standalone dispatcher and its
tests as one green commit.

### Step 3: Add Client batch replay and integrate the router

In `app/client.py`, register and validate `mouse_move_batch`. Replay every
delta by calling the same `on_mouse_move()`/`inject_move()` path sequentially;
this preserves speed scaling, Windows clamping, and edge detection.

In `app/input_router.py`, create/start a dispatcher only for acknowledged
ready sessions. Snapshot ownership under the router lock, enqueue outside it,
and update held-key/button bookkeeping only when the dispatcher accepts the
record. Drop all pending-transition movement intentionally. Stop acceptance on
pause, destination loss, session replacement, and topology replacement.

Dispatcher failure must enter Plan 012's single destination-failure path. It
must not call socket close or Server UI while holding dispatcher/router locks.

**Verify**: dispatcher plus router/Client commands → all pass. Commit protocol
batch replay and router integration as a separate green commit.

### Step 4: Prove normal load and abrupt-loss responsiveness

Add deterministic stress tests:

- equivalent 30-character-per-second key repeat for ten seconds on a
  responsive fake lane, with no overflow or reordering;
- a macro-sized responsive burst, with no false failure;
- a 1,000 Hz movement producer, with bounded exact batches;
- blocked active Client while simulated Tk callbacks continue returning;
- heartbeat/session teardown while the worker remains blocked;
- simultaneous live Client 2 traffic while Client 1 is blocked;
- key/button failure clears logical held state and suspends routing once.

Tests must use conditions/events, not arbitrary long sleeps.

**Verify**: focused dispatcher/router/protocol stress suite repeated 50 times →
all pass. Commit any race hardening separately.

### Step 5: Run system and repository landing gates

Update the real-TLS seam to assert ordered batched motion and abrupt active
destination loss without blocking the test's simulated GUI caller. Run every
command above. Update the effort README only after the complete gate passes.

Do not claim the physical Wi-Fi bug fixed from automation alone. Plan 008 owns
the real three-PC rows and measured 50-crossing deadline evidence.

**Verify**: every command in “Commands you will need” succeeds.

## Test plan

- Dispatcher unit tests own bounds, ordering, isolation, and failure callback.
- Router tests own state snapshots, held input, and suspension integration.
- Client tests prove delta-by-delta batch replay.
- Real-TLS tests prove the coordinated wire protocol.
- Existing overlay/hotkey tests prove callback behavior remains responsive.

## Done criteria

- [ ] No remote mouse/key/button/scroll callback waits for a socket write.
- [ ] Movement batches preserve every original relative delta and edge check.
- [ ] Discrete input stays FIFO and bounded without normal-load false failure.
- [ ] One blocked Client cannot delay Client 2 or heartbeat/session teardown.
- [ ] Failure enters the existing suspension/reset path exactly once.
- [ ] Focused, 50-repeat stress, system, security, full, compile, and whitespace gates pass.
- [ ] Each large Step has an indexed handoff; every commit is green and narrow.
- [ ] No files outside the in-scope list change.

## STOP conditions

Stop and write a handback if:

- correct ordering requires changing the low-level TLS framing or adding a port;
- Client replay cannot preserve existing `inject_move()` edge behavior;
- normal Windows key repeat reaches the bound on a healthy fake/live lane;
- a worker must hold router state while calling `send_message()`;
- cleanup requires joining a potentially blocked worker;
- a verification fails twice after a narrow fix or scope expands materially.

## Maintenance notes

The dispatcher is an input safety boundary, not a general network queue. Keep
clipboard/file/control transactions on their current owners. Preserve original
movement deltas; net-vector coalescing changes Windows clamp and edge semantics.
