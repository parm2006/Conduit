# Plan 009: Add cursor return and compact topology controls

> **Executor instructions:** Follow this plan step by step. Run every verification command and confirm the expected result before moving on. If anything in "STOP conditions" occurs, stop and write a handback; do not improvise. When done, update this plan's status row in the effort README.
>
> **Step handoff checkpoint:** After completing and verifying every numbered Step, create a new append-only `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update `handoffs/index.md` before starting the next Step. Record the exact verification result, branch/SHA and working-tree state, decisions, remaining work, and the next Step. Do not overwrite an earlier handoff or commit handoff files unless the user explicitly requests it.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff ba11b72 -- app/global_hotkey.py app/input_router.py app/server.py app/gui.py app/topology_editor.py tests/test_global_hotkey.py tests/test_input_router.py tests/test_emergency_release.py tests/test_topology_editor.py tests/test_gui_connection_lifecycle.py docs/plans/multi-client-topology/README.md`
>
> If these paths changed after this plan was written, compare the current-state notes against the live code before proceeding. Stop if the change alters hotkey ownership, input-routing state, or Apply pause semantics.

## Status

- **Effort:** S
- **Risk:** MED
- **State:** Automated implementation complete at `3719bed`; packaged physical acceptance pending
- **Depends on:** Plan 008 automated/package gates; the user explicitly moved this work ahead of its remaining physical matrix
- **Planned at:** revision `ba11b72`, 2026-08-28
- **Design:** `docs/superpowers/specs/2026-08-28-cursor-return-and-compact-topology-controls-design.md`

## Why this matters

The roaming cursor can be difficult to recover when remote ownership or edge routing behaves unexpectedly. A Server-only emergency chord gives the user a fast, topology-independent way to release forwarded input and put the cursor at the center of the Server's primary display. Compact `✓` and `✕` controls also fit the editor without exposing its internal Apply/Reset lifecycle as changing button text.

## Accepted behavior

- Hold **Ctrl** and tap **Space** twice within 750 milliseconds.
- Detect the chord from the physical Server keyboard even while the shared cursor is on either Client.
- Ignore key auto-repeat. Require two distinct Space presses with a release between them while Ctrl remains held.
- Reset a partial chord when Ctrl is released, another key is pressed, the interval expires, or the monitor stops.
- Release forwarded keys and mouse buttons before restoring Server ownership.
- Center on the primary display reported by the active topology.
- If the input router is paused for Apply, re-release and re-center without resuming it. Apply already returns the cursor to the Server, so no separate pending queue is needed.
- If no topology router exists, release captured input and use the Server's stored primary-screen dimensions as a local fallback.
- Keep the shortcut fixed. Do not add settings, a toast, a network command, or a Client-side trigger. Log one concise `[cursor]` diagnostic.
- Display `✓` for the topology action throughout the Server lifetime and `✕` for Cancel.
- Internally, `✓` performs Apply until the first successful atomic transaction, then Reset for the rest of that Server lifetime. Failures do not advance the lifecycle; Server restart restores Apply behavior.
- Hover text exposes `Apply layout`, `Reset layout`, or `Cancel changes` without adding a package or image asset.
- Treat the hidden action lifecycle as GUI session state. Do not persist it or infer it from topology version, connections, or `routing_suspended`.

## Current state

- `app/global_hotkey.py` owns the always-active keyboard listener, callback set, pressed-key set, and lock. `GlobalHotkeyMonitor._on_press()` recognizes the existing three simultaneous modifier chords and dispatches callbacks on daemon threads.
- `app/input_router.py:197-228` handles destination loss and Apply pause/resume. `InputRouter.pause()` already releases remote input and restores the Server-primary center while preserving a `Paused` state.
- `app/input_router.py:311-339` has the release ordering primitives. `_release_remote()` sends releases for tracked keys and buttons; `_return_to_server_center()` currently clears tracking before restoring local input and is not a public user action.
- `app/server.py` creates the Server-owned `GlobalHotkeyMonitor`. The GUI also owns a monitor, so only the Server monitor may receive the new callback; otherwise one physical chord could trigger twice.
- `app/topology_editor.py` currently renders `Reset` and `Cancel` in two 62-pixel buttons over the grid. Preserve their commands and positions while replacing their display with compact glyphs.
- `app/server.py:336-346` installs the authoritative `InputRouter`. `_ServerInputEffects.restore_local()` at lines 50-55 stops keyboard capture, hides the capture overlay, injects the cursor position, and restarts edge detection.
- `tests/test_global_hotkey.py` directly drives pynput key objects to verify chord recognition. `tests/test_input_router.py:270-288` and lines 616-640 verify Server-primary recovery and paused-router behavior.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_global_hotkey tests.test_input_router tests.test_emergency_release tests.test_topology_editor tests.test_gui_connection_lifecycle -q` | All focused tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |

## Scope

**In scope:**

- `app/global_hotkey.py`
- `app/input_router.py`
- `app/server.py`
- `tests/test_global_hotkey.py`
- `tests/test_input_router.py`
- `tests/test_emergency_release.py`
- `app/gui.py`
- `app/topology_editor.py`
- `tests/test_topology_editor.py`
- `tests/test_gui_connection_lifecycle.py`
- `docs/plans/multi-client-topology/README.md` for status only

**Out of scope:**

- `app/client.py`, `app/network.py`, and cluster protocols; the shortcut is local to the authoritative Server.
- Topology validation or Apply transaction changes.
- Clipboard, file-transfer, firewall, port, toast, and settings behavior.
- User-configurable shortcut bindings.

The GUI/editor files remain out of scope for shortcut detection. The hotkey,
router, and Server files remain out of scope for glyph presentation.

## Steps

### Step 1: Specify and test the double-tap state machine

Extend `tests/test_global_hotkey.py` first. Cover one callback after two distinct Space taps while Ctrl stays held, left/right Ctrl normalization, the 750-millisecond boundary with an injected monotonic clock, key auto-repeat, Ctrl release, unrelated-key cancellation, monitor stop, and preservation of all existing chords.

Then extend `GlobalHotkeyMonitor` with an optional `on_return_to_server` callback and testable clock/interval inputs. Keep the state under the existing lock. Launch the callback through the same daemon-thread convention as the existing hotkeys and clear the chord state before dispatch.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_global_hotkey -q` -> all hotkey tests pass without timing sleeps.

### Step 2: Add an ordered, idempotent router recovery action

Add a public `InputRouter.return_to_server_primary(reason="shortcut")` operation. Under the router's re-entrant lock, it must release tracked remote keys and buttons before restoring local input, compute the center from `topology.server_primary_center()`, and leave the router in `LocalServer`. If the router is already `Paused`, restore the center but preserve the exact `Paused` state so the shortcut cannot bypass Apply.

Refactor existing private center-return paths only as needed to share safe ordering. Preserve their failure behavior when a destination has already disconnected.

Add tests for remote and local ownership, held key/button release ordering, repeated calls, actual primary-display selection in a multi-monitor Server group, and invocation while paused.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_input_router -q` -> all router tests pass.

### Step 3: Wire only the Server monitor

Pass the new callback when `ConduitServer` constructs its `GlobalHotkeyMonitor`. Add a narrow Server method that calls the active router's recovery action and records one `[cursor]` diagnostic. If no router is installed, release injected/captured input and center with the Server's stored screen dimensions without starting a session or changing topology.

Do not add the callback to the GUI or Client monitor. Verify that stopping the Server also stops the only active return-shortcut callback.

Extend `tests/test_emergency_release.py` with callback delegation and no-router fallback cases. Assert input release occurs before cursor injection and that connections remain active.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_global_hotkey tests.test_input_router tests.test_emergency_release -q` -> all focused tests pass.

### Step 4: Add compact topology controls and run regression gates

Implement the compact action controls test-first:

- `✓` always renders in the right action button and `✕` always renders in the left Cancel button.
- The buttons are narrow, retain their existing commands, and expose lifecycle-aware hover text.
- The hidden action starts as Apply, advances to Reset only after the first successful transaction, and returns to Apply after Server restart.
- Invalid or failed first attempts keep Apply behavior; later failures keep Reset behavior.

Keep this presentation state separate from `routing_suspended`; disconnect
safety remains Plan 010's responsibility.

Run compileall, the full unit suite, and `diff --check`. Build the existing development executable through Plan 008's package command before physical testing.

Physically verify the chord with the shared cursor on the Server primary display, a Server secondary display, Client 1, and Client 2. Repeat while holding a mouse button and a non-chord modifier; no input may remain stuck. Confirm Client sessions, clipboard state, file jobs, and active topology remain unchanged.

**Verify:** every command in “Commands you will need” succeeds, and the physical rows above pass on the packaged build.

## Test plan

- `tests/test_global_hotkey.py`: deterministic chord timing, reset, repeat suppression, and existing-hotkey regression tests.
- `tests/test_input_router.py`: ordered remote release, primary-center selection, idempotence, and paused-state preservation.
- `tests/test_emergency_release.py`: Server callback wiring, local fallback, lifecycle, and connection preservation.
- `tests/test_topology_editor.py` and `tests/test_gui_connection_lifecycle.py`: compact glyphs, hover text, command preservation, action lifecycle, and Server restart.
- Physical test: Server primary/secondary and both Client destinations, including held-input recovery.

## Done criteria

- [ ] The fixed Ctrl+Space, Space chord works from the physical Server keyboard while the shared cursor is local or remote.
- [x] Forwarded keys and buttons are released before Server ownership returns.
- [x] The cursor lands at the current Server primary display's center.
- [x] Apply remains paused when the shortcut runs during Apply.
- [x] No topology, connection, clipboard, file, toast, firewall, port, or settings behavior changes in the automated suite.
- [x] `✓` and `✕` remain fixed while hover text and behavior reflect Apply, Reset, and Cancel accurately.
- [x] The hidden action starts as Apply, advances only after the first successful Apply, and returns to Apply only after Server restart.
- [x] Focused tests, compileall, full suite, and `diff --check` pass (99 focused and 799 total tests).
- [ ] Packaged physical tests pass from every supported cursor location.
- [x] No implementation files outside the in-scope list are modified.

## STOP conditions

Stop and write a handback if:

- reliable detection requires suppressing normal Ctrl+Space input globally;
- the shortcut requires a new Client or network command;
- recovery cannot release remote input before restoring local ownership;
- the active topology cannot identify the current Server primary display;
- the shortcut would resume a paused Apply transaction or mutate another service;
- a focused verification fails twice after a narrow fix;
- implementation requires an out-of-scope file.

## Maintenance notes

Keep this recovery action Server-authoritative and idempotent. Future configurable hotkeys should reuse the detector only after resolving Ctrl+Space input-method conflicts; they must not weaken the fixed emergency path or duplicate callbacks across the GUI and Server monitors.
