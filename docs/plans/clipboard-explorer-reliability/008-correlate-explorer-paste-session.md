# Plan 008: Correlate and retire only the active Explorer paste session

> **Executor instructions**: Follow this plan after Plan 005 lands. Run every
> verification command. Stop and write a handback if Windows correlation would
> require titles, translated button text, global Escape, or broad Explorer
> termination.
>
> **Drift check (run first)**:
> `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff 85dccb912871e05f401b117da3ed6c7e240594e9 -- app/file_transfer/publisher.py app/file_transfer/paste_service.py app/windows_virtual_files.py app/input_geometry.py tests/test_file_paste_publisher.py tests/test_file_transfer_lifecycle.py`
> Reconcile the live code with Plan 005's landed terminal API before editing.
> Stop if no immutable terminal-outcome query exists.

## Status

- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 005
- **Planned at**: revision `85dccb912871e05f401b117da3ed6c7e240594e9`, 2026-08-11
- **Design**: [Explorer and Toast Cancellation Design](../../superpowers/specs/2026-08-11-explorer-toast-cancellation-design.md)

## Why this matters

OLE outcomes do not report every conflict-dialog Cancel or X action. DeskFlow
must observe the one popup created by the latched destination paste, while
refusing to close unrelated Explorer windows. The same destination context is
the only safe basis for deleting a newly created empty folder after
cancellation.

## Current state

- `app/file_transfer/publisher.py:195-249` publishes the virtual owner, injects
  `Ctrl+V`, waits for stream/drop evidence, waits for terminal state, and then
  retires the owner. It captures no destination window or folder.
- `app/file_transfer/paste_service.py:47-53` exposes
  `destination_paste_active` as pending handshake or publisher work. This is
  the existing destination latch; preserve it.
- `app/input_geometry.py` shows the codebase pattern for small Win32 adapters
  with deterministic pure helpers and OS calls isolated at the boundary.
- `tests/test_file_paste_publisher.py:200-460` injects publisher functions,
  events, and receivers. Match this style; unit tests must not open Explorer.
- No current module enumerates Explorer Shell windows, records popup ownership,
  resolves the destination folder, or tracks pre-paste directory existence.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Session and publisher tests | `.\venv\Scripts\python.exe -m unittest tests.test_explorer_paste_session tests.test_file_paste_publisher tests.test_file_transfer_lifecycle -q` | all pass |
| Plan 005 regression | `.\venv\Scripts\python.exe -m unittest tests.test_windows_virtual_files tests.test_file_transfer_receiver tests.test_file_transfer_cancellation -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all pass |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff --check` | no output, exit 0 |

## Scope

**In scope**:

- `app/file_transfer/explorer_session.py` (new)
- `app/file_transfer/publisher.py`
- `app/file_transfer/paste_service.py` only if pending/latch observation needs a
  narrow test seam; do not change its handshake protocol
- `tests/test_explorer_paste_session.py` (new)
- `tests/test_file_paste_publisher.py`
- `tests/test_file_transfer_lifecycle.py`
- `docs/plans/clipboard-explorer-reliability/README.md` for this plan's status

**Out of scope**:

- `app/gui.py` and toast styling.
- Clipboard-offer/manifest protocol correlation.
- Recursive deletion or deletion of destination files.
- Matching popup titles, button labels, or arbitrary foreground windows.
- Large-transfer changes.

## Steps

### Step 1: Specify the session state machine with failing pure tests

Create tests for a session with an injected Windows/Shell adapter. The state
must record:

- job ID and latched destination Explorer HWND/process;
- top-level window snapshot before injection;
- zero or one correlated new popup;
- stream-open and performed-effect evidence;
- popup visible/closed state;
- cancellation initiated by DeskFlow or inferred from popup closure; and
- destination folder plus approved empty-directory cleanup candidates.

Cover these outcomes:

- exactly one new popup owned by the destination is correlated;
- unrelated, pre-existing, other-process, ownerless, and ambiguous popups are
  rejected;
- popup closure plus no positive evidence becomes cancelled only after a
  deterministic resolution grace;
- stream-open or COPY evidence during the grace continues;
- DeskFlow cancellation requests close only the stored correlated popup;
- no correlation produces no close call; and
- the session reaches one terminal disposition.

Use an injected clock or timer; do not sleep in unit tests.

**Verify**: the session test command fails because the component does not yet
exist.

### Step 2: Implement the Windows/Shell adapter and pure session model

Add `app/file_transfer/explorer_session.py`. Keep policy in a testable session
class and Win32/Shell calls in a small adapter.

The adapter may use foreground HWND, process identity, root-owner/owner-chain
relationships, visibility, top-level enumeration, and Shell's window-to-folder
mapping. It must not inspect a title or localized control label. It must never
send a global key, terminate Explorer, or close a window that the session did
not record after injection.

Resolve only local filesystem destination folders. Normalize and verify each
cleanup candidate remains a direct child under that destination. Record
whether each top-level directory existed before injection.

Cleanup uses nonrecursive `rmdir`. Attempt it only for a candidate absent
before paste and still empty. Treat missing, nonempty, changed, unresolved, or
out-of-root candidates as preserved. Log safe reason codes without names or
paths.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_explorer_paste_session -q`
passes, including adversarial correlation and deletion tests.

### Step 3: Bind the session to the publisher's current job and OLE owner

Create the session before injecting `Ctrl+V`. The captured job, destination
window, manifest, and OLE owner remain immutable for that publisher queue item.

During the existing COM pump:

- observe new correlated popup state;
- treat a visible correlated conflict popup as Explorer activity, so the
  Explorer-start timeout does not expire while the user is deciding;
- forward stream-open and performed-effect evidence to the session;
- after popup closure, allow the bounded resolution grace to distinguish
  Copy-and-replace from Cancel/X/Don't-copy;
- route inferred cancellation through Plan 005's receiver transition; and
- on receiver cancellation, close only the correlated popup, retire the OLE
  owner, attempt approved empty-folder cleanup, and release the session.

The publisher's pending count must remain nonzero until this cleanup finishes.
`FilePasteService.destination_paste_active` must therefore keep the screen
destination latched during the prompt and become false after terminal cleanup.

**Verify**: run session/publisher/lifecycle tests. They pass and assert latch
release for cancel and success.

### Step 4: Prove safe failure and normal-transfer regressions

Add tests for adapter errors, no filesystem destination, popup disappearance
races, owner retirement failure, and duplicate terminal callbacks. Each case
must preserve unrelated windows and user-owned folders. A normal no-conflict
paste must retain the v5 owner-retirement behavior.

Run all commands in Commands you will need.

## Test plan

- New pure state-machine and adapter tests in
  `tests/test_explorer_paste_session.py`.
- Publisher tests for popup visibility, start-timeout pause, Copy vs Cancel
  grace ordering, remote cancellation, owner retirement, and cleanup ordering.
- Lifecycle tests for `destination_paste_active` before and after terminal
  session cleanup.
- Plan 005 regression tests remain green.

## Done criteria

- [ ] All four verification commands pass.
- [ ] No code matches popup titles or localized button labels.
- [ ] No global key or uncorrelated window close exists.
- [ ] Cleanup uses only nonrecursive empty-directory removal for proven new
  top-level folders.
- [ ] The destination latch releases after terminal session cleanup.
- [ ] No file outside the in-scope list is modified.

## STOP conditions

Stop and write a handback if:

- The supported Windows build cannot relate a conflict popup to the latched
  Explorer window through ownership/process evidence.
- Shell cannot resolve a destination folder without guessing.
- Meeting required behavior needs recursive deletion or destination-file
  deletion.
- The publisher would close an uncorrelated window on any fallback path.
- A verification step fails twice or an out-of-scope file becomes necessary.

Describe the observed HWND/process/owner relationships and missing evidence in
the handback. Do not broaden matching.

## Maintenance notes

Plan 009 validates the real Windows relationship. Windows-version changes must
fail closed: no correlation means no window action and no folder deletion.
