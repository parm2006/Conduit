# Plan: Make the emergency hotkey close both connected DeskFlow apps

> **Executor instructions**: Follow this plan step by step. Use test-driven
> development: add each regression first, run it, and confirm it fails for the
> expected missing behavior before changing production code. Run every
> verification command before moving on. If a STOP condition occurs, write a
> handback instead of improvising.
>
> **Drift check (run first)**:
> `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff d514aa0a0d1db1bb2acb8e3377627c70e758651d -- app/gui.py app/client.py app/server.py app/global_hotkey.py tests/test_daemon_mode.py tests/test_emergency_release.py tests/test_global_hotkey.py README.md`
> The working tree already contains owner changes to `README.md`; preserve and
> reconcile them. Stop if any other in-scope file has changed in a way that
> invalidates the current-state description below.

## Status

- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Planned at**: revision `d514aa0a0d1db1bb2acb8e3377627c70e758651d`, 2026-08-12
- **Design**: `docs/superpowers/specs/2026-08-12-coordinated-emergency-exit-design.md`

## Why this matters

`Ctrl+Shift+Alt+Esc` currently disconnects DeskFlow and restores its window,
leaving the applications and server running. Three global-hotkey owners can
also race to perform overlapping disconnect work. DeskFlow 5.1 gives the
hotkey one meaning: close the two DeskFlow applications in the active
authenticated session, or close the local application when no peer is
reachable.

## Current state

- `app/gui.py:510-517` creates a GUI-level `GlobalHotkeyMonitor`.
- `app/gui.py:1036-1044` handles emergency exit by calling endpoint disconnect
  methods, hiding the overlay, and restoring the GUI.
- `app/gui.py:1106-1121` contains the authoritative `on_close()` lifecycle:
  pairing shutdown, monitor stop, server stop, client disconnect, and window
  destruction.
- `app/server.py:64-69` and `app/client.py:63-67` create additional global
  monitors whose emergency callbacks bypass GUI lifecycle ownership.
- `app/server.py:397-448` releases forwarded keys and disconnects lanes during
  its legacy emergency path. Preserve the release-before-disconnect invariant.
- `app/gui.py:766-770` and `app/gui.py:837-843` show the established convention
  for registering authenticated control-message callbacks at the GUI boundary.
- `tests/test_daemon_mode.py:15-49` provides a lightweight GUI test double and
  `tests/test_emergency_release.py:49-64` verifies input release ordering.

Network callbacks execute outside Tkinter's UI thread. Match the existing GUI
convention of scheduling UI work through `after(0, callback)`. Log only safe
error types through `app.safe_errors.error_name`; do not expose peer data.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_daemon_mode tests.test_emergency_release tests.test_global_hotkey -q` | all tests pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all tests pass |
| Compilation | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0, no output |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/DeskFlow diff --check` | exit 0, no output |
| Release | `.\scripts\build_release.ps1` | tests, PyInstaller, firewall-helper smoke test, and NSIS pass |

## Scope

**In scope**:

- `app/gui.py`
- `app/client.py`
- `app/server.py`
- `tests/test_daemon_mode.py`
- `tests/test_emergency_release.py`
- `tests/test_global_hotkey.py` only if the callback-dispatch contract changes
- `README.md` only to update the hotkey description while preserving owner edits
- `docs/plans/validate_coordinated_emergency_exit.md`

**Out of scope**:

- `app/network.py`: authenticated message dispatch already supplies the needed
  trust boundary.
- `app/global_hotkey.py`: keep key recognition and worker-thread dispatch
  unchanged unless a failing regression proves it cannot support one callback.
- Version and packaging files: the owner already has uncommitted release edits;
  do not overwrite or absorb them into the feature commit.
- Windows shutdown, unrelated process termination, configurable modes,
  acknowledgement/retry protocols, and changes to the reload or daemon hotkeys.

## Steps

### Step 1: Specify the one-shot GUI shutdown contract with failing tests

Replace the obsolete visibility-restoration test in `tests/test_daemon_mode.py`
with focused observable behavior. Tests must establish that:

- local initiation sends exactly one `{"type": "shutdown_app"}` on the active
  authenticated control lane before local `on_close()` runs;
- server and client roles each use the same contract;
- remote receipt schedules local closure without echoing a message;
- duplicate local callbacks and a local/remote race run `on_close()` once;
- no connected peer still closes the local GUI; and
- server shutdown occurs before GUI destruction.

Extend the test double only with public lifecycle seams (`after`, `on_close`,
recording networks). Do not test private lock choreography.

Add endpoint-level failing tests in `tests/test_emergency_release.py` proving
that endpoint/global and captured-key emergency callbacks delegate to the GUI
shutdown owner and that forwarded modifiers are released before lane teardown.

**Verify RED**:
`.\venv\Scripts\python.exe -m unittest tests.test_daemon_mode tests.test_emergency_release -q`
must fail because coordinated shutdown and `shutdown_app` handling do not yet
exist, not because of malformed fixtures.

### Step 2: Give the GUI sole ownership of coordinated shutdown

In `app/gui.py`, add one guarded coordinator for emergency application exit.
It must distinguish local initiation from peer receipt only to prevent message
echo; both paths converge on the same UI-thread `on_close()` call. Set the guard
before sending or scheduling so duplicate hotkey monitors and simultaneous peer
messages cannot race through it.

Choose the currently active endpoint's authenticated control lane. Attempt one
`shutdown_app` send before closing locally. A failed or absent connection must
not delay local closure. Register the peer-message callback alongside the
existing daemon, disconnect-notice, and reload callbacks for both server and
client construction paths.

Make `on_close()` itself safe against a duplicate scheduled invocation. Preserve
its existing cleanup order and ensure an active server is stopped before
`destroy()`.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_daemon_mode -q` passes.

### Step 3: Route every emergency-hotkey source through the GUI owner

In `app/server.py` and `app/client.py`, accept an optional application-shutdown
callback from the GUI. When supplied, endpoint global monitors delegate to it.
The server's captured-key emergency detection must use the same delegate.
Standalone endpoint construction may retain a safe local compatibility path,
but GUI-created endpoints must never disconnect the control lane before the GUI
has attempted the peer shutdown message.

Preserve input safety: forwarded server keys and locally injected client keys
must be released before endpoint teardown. Prefer a narrow preparation method
or existing release operation over calling the old disconnecting emergency
routine ahead of the peer notification.

Update `app/gui.py` server and client construction to supply the coordinator.
Do not add another hotkey or another shutdown protocol message.

**Verify**:
`.\venv\Scripts\python.exe -m unittest tests.test_emergency_release tests.test_global_hotkey -q`
passes, including the existing reload behavior.

### Step 4: Reconcile documentation and run release gates

Update the existing README hotkey line to say that `Ctrl+Shift+Alt+Esc` closes
DeskFlow on both connected machines. Preserve all current owner release edits;
stage this line separately or omit README from the feature commit if it cannot
be isolated safely.

Run focused tests, full tests, compilation, whitespace validation, and the
release build. Create `docs/plans/validate_coordinated_emergency_exit.md` with
the exact executable name and SHA-256 produced by the live 5.1 packaging
configuration. The physical matrix must cover initiation on the server,
initiation on the client, a hidden/daemon GUI, local-only operation, and clean
restart/reconnection afterward. Pause for owner results before calling the
feature physically accepted.

**Verify**: every command in “Commands you will need” succeeds and the
validation document records the exact artifact hash.

## Test plan

- GUI coordinator tests in `tests/test_daemon_mode.py`: both roles, peer receipt,
  no echo, idempotency, disconnected fallback, and cleanup ordering.
- Endpoint tests in `tests/test_emergency_release.py`: global and captured-key
  delegation plus input-release ordering.
- Existing `tests/test_global_hotkey.py`: Escape detection remains one callback
  per chord.
- Full suite for clipboard, file transfer, firewall, pairing, and lifecycle
  regressions.
- Two-machine physical validation using the same packaged executable.

## Done criteria

- [ ] New tests were observed failing for the missing coordinated behavior.
- [ ] The focused tests pass.
- [ ] All repository tests pass.
- [ ] Compilation and `git diff --check` pass.
- [ ] The release build succeeds.
- [ ] One `shutdown_app` message is sent before local lane teardown.
- [ ] Both role directions close both connected DeskFlow applications.
- [ ] Duplicate callbacks cannot execute GUI teardown twice.
- [ ] Input-release ordering remains covered.
- [ ] Physical validation instructions and exact artifact hash exist.
- [ ] No unrelated owner changes are staged with the feature.

## STOP conditions

Stop and write a handback if:

- application messages can reach GUI callbacks before control-lane
  authentication;
- closing both apps requires OS process enumeration, process killing, or an
  unauthenticated side channel;
- Tkinter shutdown cannot be scheduled through `after(0, ...)`;
- the peer message must wait for an acknowledgement or retry to avoid a proven
  race;
- preserving input release requires disconnecting the control lane before the
  peer message;
- an in-scope file has drifted from the current-state description;
- any verification fails twice after a reasonable correction; or
- implementation requires modifying an out-of-scope file.

The handback must state current behavior, desired behavior, failing evidence,
and the unresolved design fork without choosing a solution.

## Maintenance notes

Keep application-lifecycle authority in the GUI. Endpoint classes may prepare
input state and transport a shutdown request, but they should not destroy UI.
Future multi-peer support must define shutdown scope explicitly; this design
targets DeskFlow's current single active peer session.
