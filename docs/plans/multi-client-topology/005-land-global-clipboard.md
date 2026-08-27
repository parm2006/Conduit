# Plan 005: Synchronize one global newest clipboard item

> **Executor instructions:** Execute test-first. Preserve current clipboard behavior unless this plan explicitly changes cluster routing. Run every gate and write a handback at any unplanned format, ordering, or Windows clipboard fork.
>
> **Step handoff checkpoint:** After completing and verifying every numbered Step, create a new append-only `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update `handoffs/index.md` before starting the next Step. Record the exact verification result, branch/SHA and working-tree state, decisions, remaining work, and the next Step. Do not overwrite an earlier handoff or commit handoff files unless the user explicitly requests it.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- app/clipboard_hub.py app/server.py app/client.py app/clipboard_handler.py app/latest_wins_sender.py app/file_transfer/paste_coordinator.py tests/test_clipboard_hub.py tests/test_clipboard_scheduling.py tests/test_latest_wins_sender.py tests/test_clipboard_deduplication.py tests/test_clipboard_formats.py tests/test_file_paste_clipboard.py tests/test_security_error_redaction.py`
>
> Expected dependency drift: Plans 003 and 004 make Server callbacks session-aware and expose active destination identity. Confirm the data lane is still distinct from the file lane and every Client remains a one-Server endpoint.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** `003-land-two-client-sessions.md`, `004-land-graph-input-routing.md`
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

Clipboard state currently flows between the Server and one Client. With two Clients, pairwise echo suppression and one-peer sequence state cannot decide which machine owns the newest item. This plan adds one Server authority while preserving non-blocking copy, one-active/one-pending latest-wins scheduling, supported rich formats, and single-use remote-injection suppression.

## Current state

- `app/latest_wins_sender.py:11-57` runs one worker, finishes one active send, and retains only the newest pending payload.
- `tests/test_latest_wins_sender.py:8-79` pins A-then-D behavior when A is active and B/C/D replace the pending slot.
- `app/clipboard_handler.py:64-126` injects remote snapshots and records a fingerprint; `:253-283` suppresses the next matching observation once.
- `app/server.py:472-571` and `app/client.py:522-604` each queue local snapshots, encode wire messages, and accept one peer's remote offers.
- `app/file_transfer/paste_coordinator.py:13-128` models FILES versus ORDINARY offers and session-local source/destination rules.
- `tests/test_clipboard_scheduling.py:203-352` verifies ordering, non-mutation, offer precedence, and privacy-safe traces.

After dependencies, `ConduitServer` owns two ready `ClientSession` data lanes and `InputRouter` exposes active destination without coupling clipboard eligibility to cursor location.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_clipboard_hub tests.test_clipboard_scheduling tests.test_latest_wins_sender tests.test_clipboard_deduplication tests.test_clipboard_formats tests.test_file_paste_clipboard -q` | All pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |

## Scope

**In scope:**

- `app/clipboard_hub.py` (new)
- `app/server.py`
- `app/client.py`
- `app/clipboard_handler.py`
- `app/latest_wins_sender.py`
- `app/file_transfer/paste_coordinator.py`
- `tests/test_clipboard_hub.py` (new)
- `tests/test_clipboard_scheduling.py`
- `tests/test_latest_wins_sender.py`
- `tests/test_clipboard_deduplication.py`
- `tests/test_clipboard_formats.py`
- `tests/test_file_paste_clipboard.py`
- `tests/test_security_error_redaction.py`

**Out of scope:**

- File bytes, manifests, staging, or Client-to-Client file jobs — Plan 006 owns them.
- Clipboard history, disk persistence, FIFO queues, or cancellation of an in-flight ordinary send.
- Direct Client-to-Client data sockets.
- Changing supported clipboard format limits without a separate security decision.

## Steps

### Step 1: Characterize three-source ordering and Apply behavior

Add failing `tests/test_clipboard_hub.py` cases for:

- Server, Client1, and Client2 copies accepted in Server receive order;
- a monotonic Server revision attached to every accepted item;
- broadcast to both other ready machines, never echoing to the source as a new local copy;
- stale and duplicate source sequences rejected without overwriting a newer clipboard;
- a newly ready Client receiving the newest ordinary snapshot;
- one Client disconnect leaving hub state and the other Client intact;
- no ordinary snapshot surviving Server process stop;
- Apply pause holding broadcast delivery while local `submit()` returns immediately;
- one source's in-flight item finishing and only its newest pending item surviving the barrier;
- successful commit and rollback both reopening delivery and converging on the newest Server-received revision.

Do not assert private helper order. Drive the hub through accept, pause/resume, session-ready, and disconnect surfaces.

**Verify:** run the focused command. Expected: new hub tests fail because the hub does not exist; current sender and clipboard tests pass.

### Step 2: Implement the Server-owned clipboard authority

Create `ClipboardHub` with one lock/dispatcher owner and explicit state:

```text
cluster revision
latest accepted ordinary snapshot or file-offer metadata
source stable device ID and source sequence
ready delivery endpoints
running | delivery-paused | stopped lifecycle
```

Completed arrivals enter one serialized accept path; that order is authoritative. Validate source/session ownership and bounds before assigning a revision. Store only the newest ordinary snapshot in memory. Expose narrow endpoint registration and pause/resume operations; do not make callers coordinate broadcast loops.

Diagnostics may include stable source ID, revision, formats, sizes, and phase. Never log payload bytes or text.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_clipboard_hub -q` → hub state, ordering, lifecycle, and failure tests pass.

### Step 3: Route every endpoint through the hub

Update `ConduitServer` so Server-local and per-session `clipboard_sync` events enter `ClipboardHub`. Update `ConduitClient` messages with source sequence/session identity and cluster revision. Clients reject stale revisions before Windows injection.

Copies are eligible from any PC regardless of roaming cursor location. A local Client Ctrl+C stays local input but still submits globally. Joining after readiness receives the latest ordinary snapshot only after all lane/session checks pass.

Translate wire dictionaries at `server.py`/`client.py` boundaries into hub commands and outcomes. Hub core must not know network callback shapes.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_clipboard_hub tests.test_clipboard_scheduling -q` → Server/Client relay tests pass.

### Step 4: Preserve endpoint latest-wins scheduling exactly

Keep `LatestWinsSender.submit()` non-blocking. One active send finishes; one pending slot is replaced atomically by every newer local copy. Add a barrier hook only if the existing worker cannot wait for delivery without blocking submit. Do not introduce one queue per format or a global FIFO.

During Apply, Windows observation and submission continue. If a send has crossed the transport boundary, it may complete into the hub while broadcasts are held. Otherwise it waits at the endpoint barrier. When resumed, the active item completes and the newest pending item follows. Server receive order still chooses the final global winner.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_latest_wins_sender tests.test_clipboard_hub -q` → current A-then-D tests and barrier tests pass.

### Step 5: Preserve echo suppression and format behavior

Keep the current single-use fingerprint rule in `ClipboardHandler`: suppress exactly the next matching observation after remote injection, then clear the marker. A later user copy of identical content must synchronize. Preserve text, HTML, RTF, PNG/DIB, Chromium registered data, current bounds, and validation-before-emptying behavior.

Extend source-only warnings for oversized or malformed payloads. Other machines keep their previous clipboard unchanged.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_clipboard_deduplication tests.test_clipboard_formats tests.test_security_error_redaction -q` → format, echo, and redaction tests pass.

### Step 6: Align file-offer metadata without moving file bytes

Update `ClipboardOfferState` so global revisions distinguish ordinary snapshots from file-offer metadata and a newer item of either kind supersedes the prior global offer. Keep file bytes absent. An already active file transfer remains bound to its captured job/manifest; Plan 006 implements the generalized job relay.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_file_paste_clipboard tests.test_clipboard_hub -q` → ordinary/file offer precedence passes.

### Step 7: Run the landing gate

Search for one-peer clipboard authority left in Server state. Per-endpoint senders remain correct; global newest/revision state belongs only to `ClipboardHub`.

**Verify:** run focused tests, compile, full suite, and whitespace commands → all pass.

## Test plan

- Test all six directed source→destination machine pairs plus Server broadcast to both Clients.
- Race copies from three source workers with deterministic synchronization; assert Server receive order, not wall-clock order.
- Test Apply success and rollback while copies arrive before, during, and after the barrier.
- Preserve A-then-D latest-wins, repeat-identical-copy, rich-format, malformed, and privacy tests.
- Test late join, disconnect, stopped hub, stale revisions, and source-only errors.

## Done criteria

- [ ] Focused tests, compileall, full suite, and whitespace checks pass.
- [ ] Every PC receives the newest accepted ordinary snapshot before paste or cursor movement.
- [ ] Copy submission never waits for Apply or file transfer.
- [ ] Apply drops no item except documented latest-wins coalescing.
- [ ] Server stop erases clipboard state; no history or disk write exists.
- [ ] Existing supported formats and echo suppression remain unchanged.
- [ ] No file outside scope changed.

## STOP conditions

Stop and write a handback if:

- dependency changes merged ordinary clipboard and file bytes onto one blocking lane;
- the only barrier implementation blocks Windows clipboard capture or `submit()`;
- global ordering requires trusting Client clocks;
- echo suppression would need permanent fingerprints or suppress repeated user copies;
- a format or size limit must change to support three machines;
- tests would log or compare private clipboard contents outside sanitized fixtures;
- verification fails twice or scope must expand.

## Maintenance notes

The hub owns cluster order and newest state; endpoint senders own non-blocking coalescing; `ClipboardHandler` owns Windows capture/injection and one-use echo suppression. Keep those three responsibilities separate. File offers share revision authority, not ordinary-payload storage or scheduling.
