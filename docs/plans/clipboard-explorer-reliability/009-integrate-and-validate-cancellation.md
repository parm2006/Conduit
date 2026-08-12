# Plan 009: Make toast and Explorer cancellation agree on both peers

> **Executor instructions**: Execute after Plans 005 and 008. This plan ends at
> a physical gate. Create the validation document and versioned build, then
> stop and wait for owner results before marking it DONE or starting Plan 004.
>
> **Drift check (run first)**:
> `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff 85dccb912871e05f401b117da3ed6c7e240594e9 -- app/client.py app/server.py app/gui.py app/file_transfer/cancellation.py app/file_transfer/paste_service.py app/file_transfer/toast.py app/file_transfer/publisher.py tests/test_file_transfer_cancellation.py tests/test_file_transfer_toast.py tests/test_file_transfer_lifecycle.py tests/test_file_paste_service.py`
> Reconcile the live code with the terminal and Explorer-session APIs landed by
> Plans 005 and 008. Stop on any unplanned protocol change.

## Status

- **Effort**: M
- **Risk**: HIGH
- **Depends on**: 005 and 008
- **Planned at**: revision `85dccb912871e05f401b117da3ed6c7e240594e9`, 2026-08-11
- **Design**: [Explorer and Toast Cancellation Design](../../superpowers/specs/2026-08-11-explorer-toast-cancellation-design.md)

## Why this matters

The terminal core and Explorer session are useful only when either peer can
cancel the same job and both user interfaces close promptly. This plan proves
the source-to-destination and destination-to-source cancellation orders,
preserves the screen latch, packages the build, and stops for real two-PC
Explorer evidence.

## Current state

- `app/gui.py:1095-1104` routes toast Cancel to the server or client endpoint
  that owns a transfer status.
- `app/file_transfer/toast.py:130-139` hides immediately after a successful
  cancellation request and suppresses scheduled refreshes for that job.
- `app/file_transfer/cancellation.py:24-75` owns the symmetric
  `cancel_job`/`cancel_ack` handshake.
- `app/client.py:115-117` and `app/server.py:125-127` expose
  `cancel_transfer` through that handshake.
- `app/file_transfer/paste_service.py:47-53` defines the destination latch from
  handshake and publisher pending state.
- `tests/test_file_transfer_cancellation.py:100-139` covers cancellation
  transport. `tests/test_file_transfer_toast.py:8-40` covers immediate hiding
  and stale refresh suppression. No current test connects toast cancellation,
  peer transport, destination publisher session, and latch release end to end.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Integration tests | `.\venv\Scripts\python.exe -m unittest tests.test_file_transfer_cancellation tests.test_file_transfer_toast tests.test_file_transfer_lifecycle tests.test_file_paste_service tests.test_file_paste_publisher tests.test_explorer_paste_session -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all pass |
| Release build | `.\scripts\build_release.ps1` | tests, PyInstaller, packaged firewall helper, and NSIS succeed |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff --check` | no output, exit 0 |

## Scope

**In scope**:

- `app/file_transfer/cancellation.py`
- `app/file_transfer/paste_service.py`
- `app/file_transfer/publisher.py`
- `app/file_transfer/toast.py` only if terminal status cannot drive the
  approved behavior without a narrow change
- `app/client.py` and `app/server.py` only for explicit cancellation/session
  wiring; do not change clipboard routing
- `app/gui.py` only for endpoint selection or stale-toast suppression
- `tests/test_file_transfer_cancellation.py`
- `tests/test_file_transfer_toast.py`
- `tests/test_file_transfer_lifecycle.py`
- `tests/test_file_paste_service.py`
- `tests/test_file_paste_publisher.py`
- `tests/test_explorer_paste_session.py`
- `docs/plans/clipboard-explorer-reliability/validate_explorer_cancellation.md`
- `docs/plans/clipboard-explorer-reliability/README.md`

**Out of scope**:

- Clipboard offer/manifest binding and route priority.
- Arbitrary Explorer UI automation.
- Rollback deletion of visible destination files.
- Large-transfer tuning.

## Steps

### Step 1: Add two-peer cancellation-order integration tests

Build an in-memory two-endpoint harness from the existing lane and endpoint
fakes. Cover:

- source-side toast Cancel while destination conflict popup is open;
- destination-side toast Cancel while its popup is open;
- Explorer Cancel/X first, followed by a late toast Cancel;
- toast Cancel first, followed by late Explorer outcome/stream callbacks;
- exactly one terminal state and one peer terminal notification;
- destination popup dismissal only on the destination endpoint;
- both toasts hidden or terminal, both controllers cancelled, sender stopped,
  publisher idle, and `destination_paste_active` false; and
- a fresh next transfer succeeds without reconnecting.

Use fakes for popup and OLE behavior. Do not open Windows UI in automated tests.

**Verify**: the integration command fails at any remaining wiring gap while
the unit tests still run.

### Step 2: Close the narrow endpoint and UI wiring gaps

Route toast cancellation from either peer through the existing cancellation
handshake. The destination receiver terminal result must wake the publisher,
which dismisses its correlated popup and releases the session. The source must
stop its sender and acknowledge cancellation.

Prefer no toast code change: `TransferPhase.CANCELLED` already hides
immediately, and `_dismissed_job_id` already blocks stale scheduled refreshes.
Change toast or GUI code only if the integration test proves a specific gap.

Preserve v5 clipboard routing, active-screen selection, and the existing
destination lock. Do not add manifest fields in this plan.

**Verify**: the integration command passes for both role directions and both
event orders.

### Step 3: Run release verification and prepare the physical build

Run the full suite, whitespace check, and release build. Copy the resulting
canonical executable to a versioned validation artifact:

`dist-plan009-v1/DeskFlow-8d17fdf-plan009-v1-explorer-toast-cancellation.exe`

Use the same executable on both machines. Record its SHA-256 in the validation
document. The canonical `dist/DeskFlow.exe` and installer remain the current
release outputs; the versioned copy exists only for this physical gate.

**Verify**: every command succeeds, the versioned file exists, and its hash is
recorded exactly.

### Step 4: Create the physical validation and pause

Create `validate_explorer_cancellation.md` with baseline/implementation commit,
artifact path/hash, privacy-safe log instructions, and a PASS/FAIL table for
both Server-to-Client and Client-to-Server directions.

Required rows:

1. Explorer conflict **Cancel** closes toast and releases control.
2. Explorer conflict **X** closes toast and releases control.
3. Explorer **Don't copy** closes toast and creates no file.
4. Explorer **Copy and replace** completes normally.
5. Toast **Cancel** while the conflict prompt is open closes the correlated
   prompt, creates no file, closes both toasts, and releases control.
6. Cancelling a new folder leaves no empty folder.
7. An existing empty folder is preserved.
8. A nonempty folder is preserved.
9. An unrelated Explorer popup/window remains untouched.
10. A fresh transfer succeeds afterward without reconnecting.

Ask for lifecycle reason codes and popup-correlation booleans only. Exclude
full paths, filenames, clipboard contents, secrets, and file bytes.

Stop and wait for the owner report. Do not mark the plan DONE or begin Plan
004/006 before the report is reconciled.

## Test plan

- Both peer directions and both cancellation orders.
- Toast stale-refresh suppression after terminal peer events.
- Popup action occurs only at the destination.
- Sender, receiver, publisher, toast, and destination latch agree.
- Safe next-job recovery.
- Full release build and physical Windows matrix.

## Done criteria

- [ ] Integration and full suites pass.
- [ ] Release build and `git diff --check` pass.
- [ ] Versioned executable and SHA-256 are recorded.
- [ ] `validate_explorer_cancellation.md` contains every required row.
- [ ] Execution is paused for owner evidence.
- [ ] After owner PASS, update this plan and README to DONE; before PASS, leave
  it IN PROGRESS.
- [ ] No file outside the in-scope list is modified.

## STOP conditions

Stop and write a handback if:

- Either peer cannot identify which endpoint owns the destination popup.
- Cancellation produces different terminal reasons on the peers.
- Popup dismissal would require title/button matching, global input, or closing
  an uncorrelated window.
- Physical validation shows no unique popup correlation or ambiguous
  close-versus-copy ordering.
- Cleanup would remove an existing or nonempty folder.
- A verification step fails twice or an out-of-scope file becomes necessary.

When physical evidence is needed, this plan's validation document is the
handback. Do not guess past it.

## Maintenance notes

Plan 004 may add offer identity to manifest messages after this accepted-job
lifecycle is stable. Plan 006 remains blocked until both Plan 004 and this
physical cancellation gate pass.
