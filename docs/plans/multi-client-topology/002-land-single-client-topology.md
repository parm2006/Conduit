# Plan 002: Land the single-Client multi-monitor topology editor

> **Executor instructions:** Execute test-first. Run every verification command and confirm its expected result before continuing. If a STOP condition occurs, write a handback for the planning agent; do not improvise. Update this plan's README row when the work lands.
>
> **Step handoff checkpoint:** After completing and verifying every numbered Step, create a new append-only `handoffs/YYYY-MM-DD-HHMM-multi-client-topology.md` and update `handoffs/index.md` before starting the next Step. Record the exact verification result, branch/SHA and working-tree state, decisions, remaining work, and the next Step. Do not overwrite an earlier handoff or commit handoff files unless the user explicitly requests it.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- app/display_topology.py app/windows_displays.py app/topology_editor.py app/topology_toast.py app/preferences.py app/gui.py app/server.py app/client.py app/input_handler.py app/input_geometry.py tests/test_display_topology.py tests/test_windows_displays.py tests/test_topology_editor.py tests/test_topology_toast.py tests/test_gui_preferences.py tests/test_input_geometry.py`
>
> Expected result before Plan 002 starts: no output. If these paths changed, compare the live code with “Current state.” Stop if ownership, lifecycle, or GUI composition no longer matches.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** none
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

Conduit currently treats a remote PC as one rectangle on one chosen edge. This plan replaces that scalar model with real Windows monitor groups, validated draft/active topology, and the approved compact Server editor while keeping the runtime limited to one Client. Landing this slice first gives later multi-Client work a stable topology contract and preserves a usable application after the change.

## Current state

- `app/server.py:28-45` constructs `ConduitServer(..., layout_position='right')` and three singleton lanes.
- `app/server.py:204-213` sends one `layout_config` with one position and starts one edge detector.
- `app/client.py:430-455` receives one width, one height, and one relative edge.
- `app/input_handler.py:61-103` stores one screen size and one Server/Client edge.
- `app/preferences.py:41-62` persists one `client_position` string.
- `app/gui.py:350-525` builds the fixed CustomTkinter window; the Server controls include four position buttons rather than a grid.
- `app/input_geometry.py:21-116` already isolates Windows work-area and toast placement calculations. Match this pure-calculation-plus-native-adapter pattern.
- `tests/test_gui_preferences.py:63-819` uses small widget fakes and public GUI callbacks. Extend that pattern instead of requiring a real Tk display for every test.
- `tests/test_input_geometry.py:12-88` table-tests negative coordinates and DPI. Use the same style for display normalization.

The accepted behavior lives in `docs/superpowers/specs/2026-08-24-multi-client-topology-design.md`. It requires equal 40×40 visual cells, immutable per-machine physical groups, full-cell adjacency, a fixed Server anchor, draft/active separation, and Apply-time validation.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_display_topology tests.test_windows_displays tests.test_topology_editor tests.test_topology_toast tests.test_gui_preferences tests.test_input_geometry -q` | All focused tests pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0, no output |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0, no output |

## Scope

**In scope:**

- `app/display_topology.py` (new)
- `app/windows_displays.py` (new)
- `app/topology_editor.py` (new)
- `app/topology_toast.py` (new)
- `app/preferences.py`
- `app/gui.py`
- `app/server.py`
- `app/client.py`
- `app/input_handler.py`
- `app/input_geometry.py`
- `tests/test_display_topology.py` (new)
- `tests/test_windows_displays.py` (new)
- `tests/test_topology_editor.py` (new)
- `tests/test_topology_toast.py` (new)
- `tests/test_gui_preferences.py`
- `tests/test_input_geometry.py`

**Out of scope:**

- `app/network.py`, `app/session.py`, `app/file_transfer/transport.py` — the Server still accepts one Client in this plan.
- `app/latest_wins_sender.py`, clipboard routing, and file-job routing — Apply may expose participant hooks, but later plans own their cluster behavior.
- Firewall rules or port allocation — this plan adds no listener.
- More than one movable Client group — the model may enforce the final maximum, but the runtime remains one Client.

## Steps

### Step 1: Characterize the old one-edge contract and write topology tests

Add failing tests that prove the desired replacement behavior through public seams:

- monitor groups preserve native adjacency, rotation, negative coordinates, primary status, and DPI while rendering equal cells;
- full-edge contact is valid; overlap, gap, corner contact, partial contact, and T-junctions are invalid;
- each Client must have a graph path to the fixed Server;
- auto-placement tries right, left, top, then bottom without occupying an existing cell;
- dragging mutates only the draft; invalid Apply leaves active topology unchanged; Cancel restores active;
- the Server group can never move or receive the red invalid outline.

Keep existing `client_position` tests until migration behavior is pinned. Do not delete old assertions before the replacement tests fail for the intended reason.

**Verify:** run the focused test command. Expected: new modules/tests fail because the topology model and editor do not exist; existing tests still pass.

### Step 2: Implement the refined topology model

Create `app/display_topology.py` as the pure owner of topology meaning. Use immutable dataclasses/enums or equivalent explicit types for stable machine ID, stable display ID, native display rectangle, normalized cell, machine group, draft topology, validation issue, validated topology, active topology, and edge mapping.

Required contract:

```text
DraftTopology.validate() -> ValidationResult
ValidationResult.valid -> ValidatedTopology
ValidatedTopology.activate(version) -> ActiveTopology
```

Callers must not install raw dictionaries or an invalid draft. Keep the fixed Server anchor and maximum two Clients in construction/transition rules, not scattered GUI checks. Put grid validation and coordinate-ratio calculations in this module; keep Tk and Windows calls out.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_display_topology -q` → all topology tests pass.

### Step 3: Translate real Windows displays at one boundary

Create `app/windows_displays.py`. Use Windows display-configuration APIs for stable target/device identity and monitor APIs for current rectangles, work areas, primary state, and DPI. Translate raw Win32 values into `display_topology` types before returning. Do not use browser screen data or infer a display from a Conduit connection.

Make Windows calls injectable through the production adapter's constructor or narrow call parameters so tests can supply recorded API results without exporting private parsing helpers. Classify unavailable/invalid native results into safe adapter failures; logs may contain stable device categories but no private paths or raw structures.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_windows_displays tests.test_input_geometry -q` → all tests pass.

### Step 4: Persist one versioned active topology

Extend `UserPreferences` with load/save methods for a versioned topology keyed by trusted machine identity and stable display identity. Validate all loaded data through `display_topology`; corrupt or unsupported data falls back safely without exposing file contents.

Use the old `client_position` only as a one-time seed when no new topology exists. After a successful new-format save, stop reading the scalar as active state. Preserve role, Server port, and known-host settings.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_gui_preferences -q` → migration, corrupt-data, round-trip, and unrelated preference tests pass.

### Step 5: Build the compact Server editor and Client identification toast

Create `TopologyEditor` as a cohesive CustomTkinter component. Its canvas is exactly seven cells wide by four cells tall (280×160 px), using exact 40×40 neutral dark grid cells. It repeats the Windows-name initial on each physical display, fixes the Server in gray, colors the one current Client by its stable slot color, and places Apply/Cancel inside the grid. It shows no IP, legend, add button, detect button, “Client Position” label, or Server-ready text.

Create a separate persistent topology-identification toast for the Client primary display. The whole body uses the assigned color and shows Windows name, display count, resolutions, and connection state. Do not reuse file-transfer toast lifetime rules. Include a Client-side disconnect action only if the current GUI already exposes an equivalent safe disconnect path.

Tests should drive component state through a narrow view model and widget fakes. Do not publish Tk internals for tests.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor tests.test_topology_toast tests.test_input_geometry -q` → all UI state and placement tests pass.

### Step 6: Wire one Client through draft and Apply

Replace the Server edge selector in `app/gui.py` with `TopologyEditor`. At startup, detect the Server group automatically. When the existing Client completes all lanes, request its display inventory, add its group to the draft at the first free priority position, and show its primary-display toast.

In `ConduitServer` and `ConduitClient`, translate inventory/layout messages at the lane boundary. Keep the previous active graph running while the user edits. Cancel, closing the editor, or moving the Server-controlled cursor to the Client discards the draft. A valid Apply releases tracked input, centers the cursor on the Server primary display, distributes the candidate, requires the one Client acknowledgement, persists after acknowledgement, and resumes the old one-Client runtime against the new graph. Invalid Apply never pauses runtime behavior.

Adapt `InputHandler` only enough to consume the active graph's one inter-machine mapping. Plan 004 replaces destination routing fully; do not build the multi-Client router here.

**Verify:** run the focused test command → all focused tests pass.

### Step 7: Remove obsolete scalar UI state and run the landing gate

Delete the four production edge controls and active runtime dependence on `layout_position`. Keep migration code only at the preference boundary with a clear removal condition. Search for stale production references and update tests that asserted old UI mechanics rather than user behavior.

**Verify:**

- `rg -n "layout_position|load_client_position|save_client_position" app tests` → only named migration compatibility and intentional historical tests remain.
- Run compile, full suite, and whitespace commands → all pass.

## Test plan

- Add pure topology tables in `tests/test_display_topology.py` for every adjacency and connectivity rule.
- Add recorded Win32 API fixtures in `tests/test_windows_displays.py`; test malformed, disabled, rotated, negative, and duplicate-name displays.
- Add editor view-state tests in `tests/test_topology_editor.py` for exact 7×4 canvas dimensions, exact cell size, fixed Server, automatic Client, Apply/Cancel, and delayed red state.
- Add primary-work-area toast tests in `tests/test_topology_toast.py`, following `tests/test_input_geometry.py`.
- Extend `tests/test_gui_preferences.py` for version migration and corruption.
- Preserve all existing one-Client behavior tests through the full suite.

## Done criteria

- [ ] Focused tests pass.
- [ ] Compileall passes.
- [ ] Full unittest discovery passes.
- [ ] `git diff --check` passes.
- [ ] One real Client can connect, appear automatically in a real multi-monitor draft, and become routable only after valid Apply.
- [ ] No production edge selector, IP label, add/detect control, or invented display remains.
- [ ] No file outside the in-scope list changed.

## STOP conditions

Stop and write a handback if:

- stable Windows display identity cannot be obtained without a new dependency or a materially different persistence model;
- the current GUI cannot host the approved sub-500×500 editor without changing the entire application layout;
- acknowledgement requires changing session/lane ownership before Plan 003;
- native monitor discovery conflicts with the accepted rule that physical groups preserve Windows arrangement;
- migration would need permanent dual topology models;
- focused or full verification fails twice after a reasonable correction;
- any out-of-scope file must change.

## Maintenance notes

Reviewers should scrutinize the boundary between raw Win32 display records and the pure topology model, persistence validation, and the rule that only validated topology reaches input code. Later plans may deepen the Apply participant interface, but they must not weaken draft/active separation or reintroduce scalar edge state.
