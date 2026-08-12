# Plan 005: Give each Explorer paste one terminal result

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If a
> STOP condition occurs, write a handback instead of improvising. Update this
> plan's status row in `README.md` when work starts and when it lands.
>
> **Drift check (run first)**:
> `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff 85dccb912871e05f401b117da3ed6c7e240594e9 -- app/windows_virtual_files.py app/file_transfer/receiver.py app/file_transfer/sender.py app/file_transfer/publisher.py app/file_transfer/cancellation.py tests/test_windows_virtual_files.py tests/test_file_transfer_receiver.py tests/test_file_transfer_sender.py tests/test_file_paste_publisher.py tests/test_file_transfer_cancellation.py`
> If these files changed, compare the live lifecycle with the Current state
> section. Stop on a semantic mismatch.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 003 and 007, completed by `8d17fdf`
- **Planned at**: revision `85dccb912871e05f401b117da3ed6c7e240594e9`, 2026-08-11
- **Design**: [Explorer and Toast Cancellation Design](../../superpowers/specs/2026-08-11-explorer-toast-cancellation-design.md)

## Why this matters

The receiver currently has separate completion, cancellation, failure,
disconnect, stream-close, and performed-drop paths. Competing events can
publish different terminal conclusions or leave one peer waiting. Explorer's
`Performed DropEffect` value is also discarded, so the receiver cannot
distinguish an explicit copy outcome from cancellation.

This plan establishes the terminal truth used by the later Windows popup and
toast integration. It must remain independently landable: all existing copy
and file-transfer tests stay green before any popup automation is added.

## Current state

- `app/windows_virtual_files.py:99-199` implements the Shell `IDataObject`.
  `SetData` accepts `Performed DropEffect` at line 173 but calls a no-argument
  callback and discards the `DWORD` stored in the medium.
- `app/file_transfer/receiver.py:322-576` independently implements
  `record_performed_drop`, `cancel_job`, `fail_paste`, terminal tombstones,
  progress publication, and `cancel_all`.
- `app/file_transfer/sender.py:128-200` consumes `paste_progress` and accepts
  terminal `FAILED` or `CANCELLED` messages even if they arrive before the
  source worker registers the job. Preserve this early-terminal behavior.
- `app/file_transfer/cancellation.py:24-75` applies local and peer cancellation
  separately around the `cancel_job`/`cancel_ack` handshake.
- `app/file_transfer/publisher.py:195-249` treats stream-open and the current
  no-argument performed-drop callback as acceptance, then polls
  `receiver.is_paste_terminal` before retiring the OLE owner.
- Tests use `unittest`, injected clocks/timers, and small fakes rather than live
  Explorer windows. Match the deterministic timer pattern in
  `tests/test_file_transfer_receiver.py:141-225`, the production OLE medium
  pattern in `tests/test_windows_virtual_files.py:85-111`, and owner-lifetime
  fakes in `tests/test_file_paste_publisher.py:200-330`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| OLE and terminal tests | `.\venv\Scripts\python.exe -m unittest tests.test_windows_virtual_files tests.test_file_transfer_receiver tests.test_file_transfer_sender tests.test_file_transfer_cancellation tests.test_file_paste_publisher -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all pass |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff --check` | no output, exit 0 |

## Scope

**In scope**:

- `app/windows_virtual_files.py`
- `app/file_transfer/receiver.py`
- `app/file_transfer/sender.py`
- `app/file_transfer/publisher.py`
- `app/file_transfer/cancellation.py`
- `tests/test_windows_virtual_files.py`
- `tests/test_file_transfer_receiver.py`
- `tests/test_file_transfer_sender.py`
- `tests/test_file_paste_publisher.py`
- `tests/test_file_transfer_cancellation.py`
- `docs/plans/clipboard-explorer-reliability/README.md` for this plan's status

**Out of scope**:

- Explorer window discovery, dialog dismissal, and destination paths; Plan 008
  owns them.
- GUI layout or toast presentation; Plan 009 tests existing toast behavior and
  changes it only if terminal updates cannot drive it correctly.
- Clipboard-offer/manifest correlation; Plan 004 remains separate.
- Transfer size, timeout, rate, and backpressure tuning; Plan 006 remains last.

## Steps

### Step 1: Lock the terminal contract with failing tests

Add tests that describe one first-wins terminal result:

- COPY effect plus verified full byte coverage completes once.
- NONE effect cancels even if all network bytes arrived.
- Explorer cancellation followed by toast cancellation keeps the original
  cancellation result and sends one terminal progress message.
- Toast cancellation followed by a late performed effect, stream frame, or
  verification callback remains cancelled.
- Failure followed by cancellation remains failed.
- A blocked stream wakes with Windows cancellation error after cancellation.
- Source-side early and active terminal messages both stop the sender.

Also add OLE tests for preferred COPY publication, exact four-byte effect
decoding, and rejection of malformed or unsupported effects.

**Verify**: run the focused command. The new effect and exactly-once tests must
fail against the current split lifecycle while existing tests still run.

### Step 2: Preserve the Shell's COPY and performed-effect evidence

Update `VirtualFileDataObject` so its advertised formats include
`CFSTR_PREFERREDDROPEFFECT` with `DROPEFFECT_COPY`. Extend `GetData`,
`QueryGetData`, and `EnumFormatEtc` consistently; do not advertise a format
that `GetData` cannot produce.

Decode `CFSTR_PERFORMEDDROPEFFECT` from its HGLOBAL `DWORD` and pass the
integer effect to the callback. Accept only effects defined by the design.
Malformed media must raise the existing safe COM format error and must not
call the outcome callback.

Use the existing descriptor/content format helpers as the exemplar. Keep
format validation in one place rather than branching differently in
`GetData`, `QueryGetData`, and `EnumFormatEtc`.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_windows_virtual_files -q`
passes, including the new format and decoding tests.

### Step 3: Centralize receiver terminalization

Introduce one private receiver transition, plus a read-only terminal-outcome
query for the publisher. The stored outcome must include at least phase and a
safe reason code; add byte coverage and timestamp only if callers need them.

All receiver terminal callers route through it:

- performed effect NONE;
- abandoned incomplete final stream;
- explicit local or peer cancellation;
- publisher failure;
- disconnect/cancel-all; and
- successful Explorer completion after positive COPY evidence, verified
  network content, and full Explorer coverage.

The transition must atomically preserve the first terminal outcome, wake
blocked streams, update the controller, queue one terminal `paste_progress`,
and retain the bounded tombstone. Cancelled/failed jobs abort staging
immediately. Completed jobs may retain completed ciphertext until the existing
last-stream cleanup condition releases it; terminal truth and cache lifetime
are separate facts.

Replace direct terminal updates in `record_performed_drop`, `cancel_job`,
`fail_paste`, and `cancel_all`. Preserve late-frame rejection and active-job
capacity release.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_file_transfer_receiver -q`
passes, including first-wins and cache-lifetime tests.

### Step 4: Make sender and cancellation consume the same result

Keep `cancel_job`/`cancel_ack` idempotent, but make both local and peer requests
use the receiver transition's changed/already-terminal result. A duplicate
request still receives its acknowledgement but cannot publish another
terminal status.

Update `TransferSender._on_paste_progress` only as needed to accept the
terminal metadata shape. Preserve input validation, early-terminal storage,
monotonic byte checks, cancellation exceptions, and chunk-window release.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_file_transfer_cancellation tests.test_file_transfer_sender -q`
passes.

### Step 5: Pass typed Explorer outcomes through the publisher

Change the publisher callback from `performed_drop()` to
`performed_drop(effect)`. COPY records positive outcome; NONE records
cancellation. A terminal receiver result must unblock the worker and preserve
v5's rule that DeskFlow retires only its current virtual owner.

Do not add window enumeration, dialog closure, destination paths, or folder
deletion in this plan.

**Verify**: run the focused command, then the full-suite and whitespace
commands. All pass.

## Test plan

- `tests/test_windows_virtual_files.py`: preferred COPY, COPY/NONE callback,
  malformed media, and format enumeration/query agreement.
- `tests/test_file_transfer_receiver.py`: one terminal transition for every
  event order, successful positive outcome, stream wakeup, staging cleanup,
  completed-cache lifetime, and late-frame rejection.
- `tests/test_file_transfer_cancellation.py`: local/remote/duplicate request
  ordering and one peer notification.
- `tests/test_file_transfer_sender.py`: early and active destination
  cancellation stop the source without becoming failure or success.
- `tests/test_file_paste_publisher.py`: effect forwarding, terminal wait, and
  owner retirement remain deterministic.

## Done criteria

- [ ] The focused command passes.
- [ ] The full suite passes.
- [ ] `git diff --check` passes.
- [ ] Every receiver terminal path uses one first-wins transition.
- [ ] COPY and NONE remain distinct from OLE callback through peer status.
- [ ] No file outside the in-scope list is modified.

## STOP conditions

Stop and write a handback if:

- `CFSTR_PERFORMEDDROPEFFECT` medium data on the supported pywin32 runtime does
  not expose a bounded four-byte value that tests can decode.
- A positive Shell effect can complete without verified content and full
  Explorer coverage.
- First-wins terminalization would require deleting user-visible destination
  content.
- Sender cancellation requires a new file-lane protocol message rather than
  the existing `paste_progress` and `cancel_job` messages.
- A verification step fails twice or an out-of-scope file becomes necessary.

The handback must state the observed code/runtime shape, desired terminal
contract, and unresolved question without choosing a new architecture.

## Maintenance notes

Plan 008 consumes the terminal-outcome query and adds Windows popup/destination
context. Future changes must keep terminal outcome immutable and keep
completed-cache lifetime separate from the user-visible terminal phase.
