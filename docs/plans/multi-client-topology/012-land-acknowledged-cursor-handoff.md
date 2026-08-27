# Plan 012: Commit cursor ownership only after Client acknowledgement

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
> `git -c safe.directory=C:/Users/parth/Projects/Conduit diff e69fdcf -- app/input_router.py app/server.py app/client.py tests/test_input_router.py tests/test_topology_protocol.py tests/test_multi_client_system.py docs/plans/multi-client-topology/012-land-acknowledged-cursor-handoff.md docs/plans/multi-client-topology/README.md`
> If an in-scope production or test path changed after this plan was written,
> compare the current-state facts below with live code. A semantic mismatch is
> a STOP condition.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 010-stop-routing-and-reset-topology.md
- **Planned at**: revision `e69fdcf`, 2026-08-27

## Why this matters

The current router treats a successful local TLS write as proof that the
destination Client owns the cursor. During a Wi-Fi blackhole, Windows may
accept the write even though the Client never receives it. Conduit then shows
the capture overlay for a dead destination and can freeze before heartbeat
teardown. This plan makes remote ownership depend on an authenticated,
topology-scoped Client acknowledgement and fails back to the Server within a
750 ms deadline.

The accepted behavior is specified in
`docs/superpowers/specs/2026-08-27-acknowledged-cursor-handoff-design.md`.

## Current state

- `app/input_router.py:28-35` defines `Transitioning` without transaction or
  destination-session identity.
- `app/input_router.py:79-160` holds the router lock while `handle_edge()`
  resolves and executes a transition.
- `app/input_router.py:261-347` sends `switch`, then immediately starts Server
  capture and installs `RemoteClient` when the local send returns true.
- `app/client.py:656-814` validates and applies `switch` but sends no success
  acknowledgement.
- `app/server.py:184` registers `switch_back`, but no `switch_ack` callback.
- `app/server.py:860-927` updates clipboard routing immediately when
  `handle_edge()` returns true.
- `app/server.py:805-858` supplies the existing idempotent routing-suspension
  and cursor-return path. Reuse it for handoff failure.
- `tests/test_input_router.py:140-181` proves a pause request can invalidate a
  blocked handoff, but it releases the fake send manually and does not require
  peer acknowledgement.
- `tests/test_input_router.py:580-629` is the Client stale-topology rejection
  exemplar. Match its lightweight `__new__` fixture style.
- `tests/test_multi_client_system.py:112` is the real-TLS two-Client seam.
- Production error logs use exception class names without tracebacks; match
  `app/server.py:838-845` and preserve `tests/test_security_error_redaction.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Router/Client tests | `.\venv\Scripts\python.exe -m unittest tests.test_input_router -q` | all pass |
| Protocol tests | `.\venv\Scripts\python.exe -m unittest tests.test_topology_protocol -q` | all pass |
| Real-TLS system seam | `.\venv\Scripts\python.exe -m unittest tests.test_multi_client_system -q` | all pass |
| Security regression | `.\venv\Scripts\python.exe -m unittest tests.test_security_error_redaction -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | no output |

## Scope

**In scope**:

- `app/input_router.py`
- `app/server.py`
- `app/client.py`
- `tests/test_input_router.py`
- `tests/test_topology_protocol.py`
- `tests/test_multi_client_system.py`
- `tests/test_file_paste_routing.py` (switch-protocol compatibility fixtures only)
- `docs/plans/multi-client-topology/012-land-acknowledged-cursor-handoff.md`
- `docs/plans/multi-client-topology/README.md`

**Out of scope**:

- `app/network.py` — heartbeat and TLS framing remain unchanged.
- New dispatcher modules and batched movement protocol — Plan 013 owns them.
- `app/gui.py` and toast classes — existing suspension callbacks own recovery UI.
- Clipboard/file ordering, ports, firewall, authentication, and Plan 009.

## Steps

### Step 1: Pin the ownership transaction with failing tests

In `tests/test_input_router.py`, add deterministic tests with injected handoff
IDs and a controllable deadline scheduler. Prove:

- `handle_edge()` returns promptly when the destination lane blocks;
- state remains `Transitioning` and capture has not started before ACK;
- correct ACK commits exactly once;
- wrong session, machine, topology, ID, duplicate, and late ACKs do nothing;
- deadline/send failure returns Server center and reports the failed session;
- Server-to-Client and Client-to-Client pending movement is rejected;
- pause, topology replacement, and destination loss cancel pending work.

Add Client tests proving invalid/missing IDs stay inactive, and ACK send failure
deactivates and releases injected input. Run the tests before production edits;
new assertions must fail for missing acknowledged ownership, not fixture errors.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_input_router -q`
→ RED only in the new acknowledgement tests.

### Step 2: Implement the router-owned acknowledged handoff

In `app/input_router.py`, extend `Transitioning` with the exact pending
transaction identity and add injected scheduler/ID seams suitable for pure
tests. `handle_edge()` must publish pending state and schedule a daemon send
without holding the router lock. The 750 ms deadline starts before dispatch.

Add an acknowledgement method that authenticates handoff ID, topology,
session, and machine, then commits `RemoteClient`. Start Server capture only
for a valid ACK from a Server-local source. Invalidate deadlines and IDs before
restoring the Server or invoking failure callbacks. Invoke external callbacks
outside router/queue/network locks.

Preserve held-input release order and existing pause semantics. Plan 013 will
move steady-state forwarded input off the socket path; do not build that queue
here.

**Verify**: run only the new router transaction tests → GREEN. The full module
remains intentionally red in Client ACK and legacy immediate-ownership tests;
Step 3 owns those protocol and fixture updates and restores the module to green.

### Step 3: Wire authenticated Server and Client protocol callbacks

In `app/client.py`, require a non-empty string `handoff_id` on `switch`.
Validate topology and geometry, configure edges, warp, activate, then send
`switch_ack` containing the ID and topology version. If ACK send fails, release
all injected input, stop Client edges, and set `is_active=False`.

In `app/server.py`, register `switch_ack` and pass authenticated callback
metadata into the router. Move clipboard destination updates from edge-start
to successful ownership commit. Add a single handoff-failure callback that
immediately latches suspension and closes the failed session on a daemon
cleanup path. It must be idempotent with heartbeat teardown.

Update existing synchronous transition tests to acknowledge explicitly. Do
not weaken stale-session/topology rejection.

**Verify**: the entire router module and topology-protocol commands → all pass. Commit the
acknowledged handoff as one green functional commit.

### Step 4: Prove deadline and teardown races

Add deterministic regressions for all orderings:

- timeout before send returns;
- send failure before timeout;
- ACK racing timeout;
- heartbeat disconnect racing ACK;
- Apply/pause racing ACK;
- a stale ACK after reconnect with the same Windows name but a new session.

Repeat the focused race cases at least 50 times in one test process. Assert one
failure callback, one capture start at most, no late `RemoteClient`, no blocked
test thread, and persistent routing suspension after failed ownership.

**Verify**: focused router/protocol tests and the 50-repeat stress command →
all pass. Commit race hardening separately if production changes are required.

### Step 5: Update the real-TLS seam and run the landing gate

Update `tests/test_multi_client_system.py` so Client callbacks return real
`switch_ack` messages. Prove acknowledged Server→Client, Client→Client, and
Client→Server paths before testing clipboard/file behavior. Add one silent
destination case that withholds ACK and confirms Server recovery without
waiting for the six-second heartbeat.

Run focused, system, security, full-suite, compile, and whitespace gates.
Update this plan and the effort README only after every gate passes.

**Verify**: every command in “Commands you will need” succeeds.

## Test plan

- Pure router tests own state, identity, deadline, and race behavior.
- Client protocol tests own validate/apply/ACK order and fail-closed cleanup.
- Server protocol tests own authenticated metadata and suspension callbacks.
- The real-TLS seam proves the new wire message on two simultaneous sessions.
- Existing disconnect, Apply, security, clipboard, and file suites remain green.

## Done criteria

- [ ] Remote ownership and capture never begin before exact ACK.
- [ ] `handle_edge()` cannot block on the target lane.
- [ ] Dead/silent target restores Server center and suspends routing by 750 ms.
- [ ] Failed target session closes and releases its registry slot.
- [ ] Stale/late/cross-session ACK cannot change ownership.
- [ ] Focused, stress, system, security, full, compile, and whitespace gates pass.
- [ ] Each large Step has an indexed handoff; every commit is green and narrow.
- [ ] No files outside the in-scope list change.

## STOP conditions

Stop and write a handback if:

- ACK requires a new port, direct Client socket, or weaker authentication;
- `NetworkServer` callback metadata cannot authenticate the session/machine;
- the timer must wait on a router or network lock to restore local control;
- mixed-version behavior cannot fail closed;
- existing Client-to-Client release-before-edge behavior must be removed;
- a verification fails twice after a narrow fix or scope reaches Plan 013.

## Maintenance notes

Treat `send_message() == True` as local write acceptance, never remote cursor
ownership. Future transitions must preserve exact ACK identity and invalidate
the transaction before cleanup. Keep the 750 ms deadline separate from the
six-second general heartbeat.
