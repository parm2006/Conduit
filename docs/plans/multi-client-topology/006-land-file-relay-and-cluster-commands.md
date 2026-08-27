# Plan 006: Relay file jobs and cluster commands through the Server

> **Executor instructions:** Execute test-first. Preserve on-demand file transfer, bounded staging, and endpoint-specific cancellation. Stop and write a handback at any unplanned transfer-protocol or Windows virtual-file fork.
>
> **Step handoff checkpoint:** After completing and verifying every numbered Step, create a new append-only `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update `handoffs/index.md` before starting the next Step. Record the exact verification result, branch/SHA and working-tree state, decisions, remaining work, and the next Step. Do not overwrite an earlier handoff or commit handoff files unless the user explicitly requests it.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- app/file_transfer/cluster_router.py app/file_transfer/paste_coordinator.py app/file_transfer/controller.py app/file_transfer/transport.py app/file_transfer/toast.py app/file_transfer/models.py app/file_transfer/protocol.py app/file_transfer/status.py app/file_transfer/queue.py app/file_transfer/paste_service.py app/file_transfer/sender.py app/file_transfer/receiver.py app/file_transfer/executor.py app/file_transfer/cancellation.py app/file_transfer/staging.py app/server.py app/client.py app/global_hotkey.py app/gui.py tests/test_cluster_file_routing.py tests/test_cluster_commands.py tests/test_file_paste_routing.py tests/test_file_paste_clipboard.py tests/test_file_paste_service.py tests/test_file_transfer_cancellation.py tests/test_file_transfer_controller.py tests/test_file_transfer_lifecycle.py tests/test_file_transfer_models.py tests/test_file_transfer_network_identity.py tests/test_file_transfer_protocol.py tests/test_file_transfer_queue.py tests/test_file_transfer_receiver.py tests/test_file_transfer_sender.py tests/test_file_transfer_staging.py tests/test_file_transfer_status.py tests/test_file_transfer_toast.py tests/test_file_transfer_transport.py tests/test_file_transfer_validation.py tests/test_global_hotkey.py tests/test_daemon_mode.py tests/test_emergency_release.py`
>
> Expected dependency drift: Plan 003 introduces per-session file lanes, Plan 004 exposes active destination identity, and Plan 005 makes file-offer metadata globally authoritative. Confirm those contracts before editing.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** `003-land-two-client-sessions.md`, `004-land-graph-input-routing.md`, `005-land-global-clipboard.md`
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

Current Explorer paste assumes one remote peer and derives source/destination from which side controls the cursor. With two Clients, a job must latch stable source and destination identities and relay Client-to-Client bytes through the Server. This plan adds that job owner while preserving copy-now/transfer-on-paste behavior, resumable encrypted staging, active-job independence, and narrowly scoped toasts.

## Current state

- `app/file_transfer/paste_coordinator.py:8-179` decides whether one source/destination pair should transfer and intercepts Ctrl+V.
- `app/file_transfer/controller.py:10-103` tracks job status and cancellation by job ID.
- `app/file_transfer/transport.py:30-374` frames encrypted file-lane traffic and currently exposes one attached peer per lane object.
- `app/server.py:578-598` and `app/client.py:611-650` route manifest messages directly to one peer.
- `app/file_transfer/toast.py:17-158` derives and renders transient transfer status.
- `app/global_hotkey.py:8-96` detects reload, emergency exit, and background-mode commands; `app/gui.py:1002-1115` coordinates them with one peer.
- `tests/test_file_paste_routing.py:132-590` provides recording network/input seams and pins destination latching and edge/paste races.
- The `tests/test_file_transfer_*.py` suite pins framing, range coverage, staging, encryption identity, cancellation, and lifecycle safety. Do not rewrite those internals unless the cluster contract requires it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused cluster tests | `.\venv\Scripts\python.exe -m unittest tests.test_cluster_file_routing tests.test_cluster_commands tests.test_file_paste_routing tests.test_file_paste_clipboard tests.test_file_paste_service -q` | All pass |
| Full file suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_file*.py" -q` | All file tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |

## Scope

**In scope:**

- `app/file_transfer/cluster_router.py` (new)
- `app/file_transfer/paste_coordinator.py`
- `app/file_transfer/controller.py`
- `app/file_transfer/transport.py`
- `app/file_transfer/toast.py`
- `app/file_transfer/models.py`
- `app/file_transfer/protocol.py`
- `app/file_transfer/status.py`
- `app/file_transfer/queue.py`
- `app/file_transfer/paste_service.py`
- `app/file_transfer/sender.py`
- `app/file_transfer/receiver.py`
- `app/file_transfer/executor.py`
- `app/file_transfer/cancellation.py`
- `app/file_transfer/staging.py`
- `app/server.py`
- `app/client.py`
- `app/global_hotkey.py`
- `app/gui.py`
- `tests/test_cluster_file_routing.py` (new)
- `tests/test_cluster_commands.py` (new)
- `tests/test_file_paste_routing.py`
- `tests/test_file_paste_clipboard.py`
- `tests/test_file_paste_service.py`
- `tests/test_file_transfer_cancellation.py`
- `tests/test_file_transfer_controller.py`
- `tests/test_file_transfer_lifecycle.py`
- `tests/test_file_transfer_models.py`
- `tests/test_file_transfer_network_identity.py`
- `tests/test_file_transfer_protocol.py`
- `tests/test_file_transfer_queue.py`
- `tests/test_file_transfer_receiver.py`
- `tests/test_file_transfer_sender.py`
- `tests/test_file_transfer_staging.py`
- `tests/test_file_transfer_status.py`
- `tests/test_file_transfer_toast.py`
- `tests/test_file_transfer_transport.py`
- `tests/test_file_transfer_validation.py`
- `tests/test_global_hotkey.py`
- `tests/test_daemon_mode.py`
- `tests/test_emergency_release.py`

**Out of scope:**

- Permanent Server file cache, pre-upload on copy, clipboard history, or file broadcast to uninvolved PCs.
- Direct Client-to-Client sockets.
- Replacing the Windows virtual-file paste mechanism.
- Topology Apply acknowledgement/rollback orchestration — Plan 007 composes the file scheduler's pause interface.

## Steps

### Step 1: Write cluster job-routing tests

Add `tests/test_cluster_file_routing.py` through a public router seam. Cover all directed source/destination pairs among Server, Client1, and Client2. Prove:

- a copy broadcasts file-offer metadata but zero file bytes;
- destination paste resolves the current offer through the Server;
- the job latches stable source, destination, offer revision, selection sequence, and manifest before bytes flow;
- Client1→Client2 manifest and frames relay through the Server file lanes;
- a newer clipboard item changes future paste but never corrupts an active job;
- source or destination disconnect cancels and cleans only affected jobs;
- an uninvolved third PC receives no progress, cancellation, or failure toast;
- Apply quiescence rejects new jobs and pauses active scheduling at a safe boundary;
- transfer resume/backpressure remains bounded and no permanent cache survives job cleanup.

Add `tests/test_cluster_commands.py` for reload, shutdown, and background broadcast to two sessions, including one failed recipient.

**Verify:** run focused cluster tests. Expected: new tests fail because cluster router/commands do not exist; existing file tests pass.

### Step 2: Implement one Server-owned cluster file router

Create `app/file_transfer/cluster_router.py`. It owns global offer resolution and job lifecycle, not frame encoding or Windows publication. Use explicit job states such as offered, preparing, transferring, publishing, completed, cancelled, and failed. Phase-specific data belongs to its state.

Each job captures:

```text
job_id
offer_revision and source selection sequence
source stable endpoint/session
destination stable endpoint/session
manifest snapshot
cancellation owner and cleanup handles
```

Expose narrow commands for paste request, manifest result, frame relay, endpoint disconnect, cancel, scheduler pause/resume, and shutdown. Bound concurrent jobs and staging using the current queue/rate-control contracts; do not create an unbounded thread per paste.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_cluster_file_routing -q` → job state, routing, isolation, and cleanup tests pass.

### Step 3: Carry stable endpoint identity through the existing file protocol

Update offer, manifest, acknowledgement, frame, cancellation, and status messages with validated job/source/destination identities where absent. Parse wire fields at Server/Client boundaries and pass refined job commands inward. Bind every frame to the authenticated file lane's session; never trust a claimed source ID that disagrees with the lane owner.

Preserve all size/path/manifest/range validation and encryption. Keep compatibility only if an active persisted or public protocol obligation exists; Conduit sessions are same-version local processes, so prefer one coherent end-state protocol.

**Verify:** run the full file suite → all framing, security, staging, and cluster tests pass.

### Step 4: Route paste intent through the hub

Update `ConduitServer` and `ConduitClient` so a local Ctrl+V consults the latest global offer and current `InputRouter` destination. Server-local paste, Client-to-Server, Server-to-Client, and Client-to-Client paths all create the same router job. Bytes move only after the destination asks to paste and the source confirms a manifest.

Preserve the current rule that local Client Ctrl+V and Ctrl+C do not move the roaming cursor. Serialize destination latching against edge transitions using the existing route lock pattern in `server.py:130-137` and `client.py:133-140`, generalized to stable IDs.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_cluster_file_routing tests.test_file_paste_routing tests.test_file_paste_clipboard -q` → all routing and race tests pass.

### Step 5: Scope toasts and cancellation

Tag status events with source and destination. Render transfer toasts only on those two machines. A cancellation initiated on either endpoint reaches the Server job owner and exact counterpart; it never broadcasts to the uninvolved PC. If disconnect occurs while paste is processing, cancel Windows publication safely and clean transient staging.

Do not combine persistent topology identification toasts with transient transfer toasts.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_cluster_file_routing tests.test_file_transfer_toast tests.test_file_transfer_cancellation tests.test_file_transfer_lifecycle -q` → toast scope and cleanup pass.

### Step 6: Broadcast cluster commands with isolated cleanup

Generalize reload, emergency shutdown, and background mode to both ready sessions. The Server releases input first, broadcasts best-effort commands, and completes local cleanup even if one send fails. Reload rebuilds all lanes while preserving trust and active topology. Shutdown closes Server and both Client apps. Background state remains synchronized.

Prevent command echo loops with command IDs or origin/session validation at the existing authenticated control boundary.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_cluster_commands tests.test_global_hotkey tests.test_daemon_mode tests.test_emergency_release -q` → command scope, release order, and partial failure pass.

### Step 7: Run all file and repository gates

Search for file routing based on one implicit peer or cursor-side strings. Job routing must use stable endpoint/session IDs.

**Verify:** run focused cluster tests, full file suite, compile, full suite, and whitespace commands → all pass.

## Test plan

- Cover every source/destination pair with the same behavior table.
- Capture a manifest, then issue newer ordinary/file copies; assert the active job remains unchanged.
- Disconnect source, destination, and uninvolved Client at each job phase.
- Test scheduler pause/resume and new-job rejection without sleep-based timing.
- Preserve existing frame, range, encryption, staging, Windows publication, and cancellation tests.
- Test reload/shutdown/background with zero, one, and two ready Clients and one failing lane.

## Done criteria

- [ ] Focused cluster tests, full file suite, compileall, full suite, and whitespace checks pass.
- [ ] All directed paste pairs work through one Server-owned job contract.
- [ ] File bytes move only on paste and use no permanent cache.
- [ ] New clipboard offers do not alter active jobs.
- [ ] Disconnect/cancel affects only jobs involving that endpoint.
- [ ] Toasts appear only on source and destination.
- [ ] Cluster commands reach both sessions without coupling cleanup failures.
- [ ] No file outside scope changed.

## STOP conditions

Stop and write a handback if:

- Client-to-Client relay requires direct sockets or a fourth port;
- the current file protocol cannot bind frames to authenticated source/destination sessions;
- Windows virtual-file publication requires pre-uploading all bytes on copy;
- active-job independence conflicts with the global offer revision model from Plan 005;
- Apply pause cannot quiesce scheduling at a bounded safe point;
- source/destination-only toasts require broadcasting private file metadata to the third PC;
- verification fails twice or scope must expand beyond the file/command paths.

## Maintenance notes

`ClusterFileRouter` owns job policy; transport owns encrypted frames; paste service owns Windows publication; toast code owns presentation. Keep source/destination identity immutable after job creation. New transfer features must preserve bounded concurrency and cancellation cleanup.
