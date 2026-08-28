# Plan 014: Route file paste between distinct Clients by machine identity

> **Executor instructions**: Follow this plan step by step and test-first.
> Run every verification command and confirm its expected result before moving
> on. If anything in "STOP conditions" occurs, stop and write a handback; do
> not improvise. Create an append-only indexed handoff after each numbered
> Step. When done, update this plan's status row in the effort README.
>
> **Drift check (run first)**:
> `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 8da0dbf -- app/file_transfer/paste_coordinator.py app/server.py tests/test_file_paste_routing.py tests/test_clipboard_hub.py tests/test_multi_client_system.py docs/plans/multi-client-topology/014-fix-client-to-client-file-paste-routing.md docs/plans/multi-client-topology/README.md`
> Expected drift: only this plan and its README row. Any production or test
> drift is a STOP condition until reconciled against the approved design.

## Status

- **State**: DONE
- **Effort**: M
- **Risk**: MED
- **Depends on**: 005-land-global-clipboard.md, 006-land-file-relay-and-cluster-commands.md, 013-land-nonblocking-input-dispatch.md
- **Planned at**: revision `8da0dbf`, 2026-08-28
- **Design**: `docs/superpowers/specs/2026-08-28-client-to-client-file-paste-routing-design.md`

## Why this matters

The Server receives Client file offers and the encrypted relay can carry
Client-to-Client frames, but the paste hotkey still collapses both Clients to
the role `client`. It evaluates A-to-B as a same-endpoint paste and never sends
`file_paste_intent` to B. This plan makes the Server's cluster route decision
use stable source and active-destination machine IDs while preserving native
same-machine paste and every working Server-involved direction.

## Current state

- `app/file_transfer/paste_coordinator.py:174-186` computes
  `transfer_required` only from the endpoint-relative `ClipboardOffer.source`
  and destination role. Preserve this as the default for legacy callers.
- `app/server.py:1425-1433` maps every remote destination to `client`, then
  calls `PasteCoordinator.set_route`; this is the faulty A-to-B decision.
- `app/clipboard_hub.py:15-22` retains the authoritative `source_id`, `kind`,
  and cluster `revision` in `ClipboardHubItem`.
- `app/input_router.py:105-114` exposes `active_machine_id`; it returns the
  stable Client machine ID for remote ownership and the Server ID locally.
- `app/server.py:1515-1527` already targets `file_paste_intent` to the active
  session once the coordinator decides transfer is required.
- `tests/test_file_paste_routing.py:209-340` is the unit exemplar for Server
  route updates and Ctrl+V suppression.
- `tests/test_multi_client_system.py:588-735` is the real-TLS exemplar for
  two authenticated Client bundles and Client-to-Client file frames.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Paste routing | `.\venv\Scripts\python.exe -m unittest tests.test_file_paste_routing tests.test_clipboard_hub -q` | all pass |
| File relay | `.\venv\Scripts\python.exe -m unittest tests.test_cluster_file_routing tests.test_multi_client_system -q` | all pass |
| File regressions | `.\venv\Scripts\python.exe -m unittest tests.test_file_paste_service tests.test_file_transfer_sender tests.test_file_transfer_receiver tests.test_file_paste_publisher -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | no output |

## Scope

**In scope**:

- `app/file_transfer/paste_coordinator.py`
- `app/server.py`
- `tests/test_file_paste_routing.py`
- `tests/test_clipboard_hub.py`
- `tests/test_multi_client_system.py`
- `docs/plans/multi-client-topology/014-fix-client-to-client-file-paste-routing.md`
- `docs/plans/multi-client-topology/README.md`

**Out of scope**:

- `app/file_transfer/cluster_router.py` — the six-direction relay and identity
  latch already work; change it only after a written handback proves otherwise.
- `app/network.py` and file transport framing — no port, TLS, or frame changes.
- `app/client.py` — the destination already handles targeted
  `file_paste_intent` and remote offers.
- Explorer publication, topology, cursor ownership, and clipboard history.

## Steps

### Step 1: Reproduce the collapsed Client identity decision

Add failing tests in `tests/test_file_paste_routing.py` that construct a
Server with a latest file offer from Client A and active ownership on Client
B. Prove the current code fails to suppress Ctrl+V and fails to send one
`file_paste_intent` to B. Add the reverse B-to-A case, same-Client native
paste, ordinary clipboard, Server-to-Client, Client-to-Server, and stale
offer/hub revision cases so the desired decision table is explicit.

Use real `PasteCoordinator` behavior. Fake only network, clipboard, hub, and
router boundaries needed to observe targeting. Do not test a mock's internal
calls instead of Server behavior.

**Verify**: paste-routing command -> RED only for the two new cross-Client
cases, with failure showing no paste intent was sent.

### Step 2: Make the cluster route decision machine-aware

Extend `PasteCoordinator.set_route` with a narrow optional explicit transfer
decision. When omitted, preserve the existing role-based calculation exactly.
Reject a non-boolean override rather than accepting truthy values.

Update `ConduitServer._apply_clipboard_offer_route` to supply the override only
when all machine-aware facts agree:

- the current offer is a cluster offer;
- `ClipboardHub.latest_item` exists and has the same revision;
- the Server can resolve its own machine ID and the router's active machine ID;
- a remote destination still resolves to a ready session.

The override is true only for `files` whose source machine differs from the
destination machine. Missing, mismatched, or stale identity must not target a
Client. Keep Ctrl+V suppression and the targeted send in the existing
coordinator/`_request_remote_file_paste` path.

Add concise diagnostic logging at the Server route boundary without exposing
file paths, clipboard contents, passwords, or full stable identity hashes.

**Verify**: paste-routing command -> all pass. Run the file-relay command to
prove the existing transport remains green. Commit the production fix and its
unit tests as one narrow green commit.

### Step 3: Prove the complete authenticated Client-to-Client path

Extend the existing two-Client real-TLS system seam in
`tests/test_multi_client_system.py`. Drive the flow from a Client A file offer
through Server route selection, targeted paste intent to Client B, manifest
request/response, acknowledgement, and at least one encrypted file frame.
Repeat with A and B reversed. Assert the uninvolved Server-local endpoint does
not receive file bytes and that the targeted session is exact.

Run every command in "Commands you will need." Mark Plan 014 DONE and append
a reconciliation entry to the effort README only after the complete gate
passes. Create the final indexed handoff, then commit docs separately.

Physical acceptance remains Plan 008: use `run.bat`, transfer a small file and
one folder in both Client directions, and confirm same-Client paste stays
native.

**Verify**: every command in “Commands you will need” succeeds.

## Test plan

- Unit decision table: `tests/test_file_paste_routing.py`.
- Clipboard source/revision fan-out regression: `tests/test_clipboard_hub.py`.
- Existing six-direction router contract: `tests/test_cluster_file_routing.py`.
- Authenticated control and file lanes: `tests/test_multi_client_system.py`.
- Existing Explorer sender/receiver/publisher suites remain unchanged and green.

## Done criteria

- [x] A-to-B and B-to-A each target the correct Client with one paste intent.
- [x] Same-Client file paste and ordinary clipboard paste remain native.
- [x] Server-to-Client and Client-to-Server behavior remains green.
- [x] Stale or unresolved identities cannot target the wrong Client.
- [x] Real TLS proves intent, manifest exchange, acknowledgement, and file-frame relay in both Client directions.
- [x] Focused file tests, full suite, compileall, and whitespace gates pass.
- [x] Each numbered Step has an indexed handoff and commits remain narrow.
- [x] No files outside the in-scope list change.

## STOP conditions

Stop and write a handback if:

- the latest clipboard item lacks a stable source machine ID or cluster revision;
- the active router state cannot expose the exact destination machine/session;
- Client-to-Client success requires changing `ClusterFileRouter`, TLS framing,
  ports, direct Client sockets, or Explorer publication;
- a route can become active without matching the current offer revision to the
  hub item;
- a verification fails twice after a narrow fix;
- any out-of-scope file appears necessary.

## Maintenance notes

The endpoint-relative `server`/`client` model remains valid inside one peer's
clipboard state. Cluster routing must use stable machine IDs whenever two
different Clients can occupy the same endpoint role. Keep this distinction
explicit in future clipboard and file-paste work.
