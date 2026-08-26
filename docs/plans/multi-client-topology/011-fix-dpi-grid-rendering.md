# Plan 011: Keep the seven-by-four topology grid fitted at every Windows DPI

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If a
> STOP condition occurs, stop and write a handback; do not improvise. Update the
> effort README when complete.
>
> **Drift check (run first)**: `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 681d8da -- app/topology_editor.py tests/test_topology_editor.py`
> Plan 010 intentionally changes these paths. Rebase the excerpts below onto
> Plan 010's final state; an unrelated semantic change is a STOP condition.

## Status

- **Effort**: M
- **Risk**: MED
- **Depends on**: 010-stop-routing-and-reset-topology.md
- **Planned at**: revision `681d8da`, 2026-08-26

## Why this matters

CustomTkinter scales the editor frame on high-DPI laptops, while the raw Tk
canvas and its drawing coordinates remain fixed at 280×160 physical pixels.
The 7×4 grid therefore occupies only the upper-left of a larger frame and leaves
blank space on the right and bottom. The editor must preserve exactly seven
columns and four rows while fitting the actual mapped canvas at any supported
Windows scale.

## Current state

- `app/topology_editor.py:237-291` declares logical `GRID_WIDTH=280` and
  `GRID_HEIGHT=160`, then creates a raw `tk.Canvas` with those fixed dimensions.
- `app/topology_editor.py:320-400` draws and hit-tests with module-level
  `CELL_SIZE=40` and fixed grid dimensions, without consulting the mapped
  canvas width or height.
- `tests/test_topology_editor.py:228-231` only checks the logical 7×4 constants;
  it does not cover scaled drawing or hit-testing.
- Match the editor's existing pure-state test style: isolate geometry helpers
  so DPI math can be tested without opening a Windows GUI.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor -q` | all pass |
| GUI regressions | `.\venv\Scripts\python.exe -m unittest tests.test_gui_connection_lifecycle -q` | all pass |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | all pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | exit 0 |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | no output |

## Scope

**In scope**:

- `app/topology_editor.py`
- `tests/test_topology_editor.py`
- `docs/plans/multi-client-topology/011-fix-dpi-grid-rendering.md`
- `docs/plans/multi-client-topology/README.md`

**Out of scope**:

- Topology state, validation, or Reset behavior — Plan 010 owns it.
- Application-wide DPI-awareness policy or CustomTkinter theme changes.
- Changing the required seven-column/four-row grid or 40-unit logical cell
  model.

## Steps

### Step 1: Reproduce scaled geometry failures with pure tests

Add tests for a geometry helper at mapped sizes 280×160, 350×200, 420×240,
and 560×320. For every size, the helper must produce seven equal columns and
four equal rows that span the entire canvas, keep the Server's logical origin
at the same center grid intersection, and map pointer coordinates back to the
same logical cells. Confirm the new tests fail against fixed 40-pixel math.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor -q` → RED only for the new scaled-geometry assertions.

### Step 2: Render and hit-test from actual canvas dimensions

Make the canvas fill its parent instead of imposing an unscaled physical size.
Bind `<Configure>` to a debounced/idempotent render and derive row/column
boundaries from `winfo_width()`/`winfo_height()` (or the event dimensions).
Use the same geometry object for lines, cell rectangles, labels, drag origin,
and pointer-to-grid conversion. Avoid rounding drift by deriving each boundary
from total extent and its integer row/column index rather than repeatedly adding
a rounded cell size.

Buttons must remain inside the grid in the same top-right arrangement and must
not trigger render recursion. The logical constants remain 7×4 and 40 units;
only physical drawing scales.

**Verify**: `.\venv\Scripts\python.exe -m unittest tests.test_topology_editor tests.test_gui_connection_lifecycle -q` → all pass.

### Step 3: Run landing gates and record the physical test

Run full tests, compileall, and whitespace checks. Update the README to mark
Plan 011 DONE and return Plan 008 to IN PROGRESS for physical acceptance. The
physical test is: run `run.bat` on a 100%-scale Server and at least one
high-DPI laptop, visit both Server and Client tabs, and verify the grid always
fills its frame with seven columns and four rows and no right/bottom blank band.

**Verify**: `.\venv\Scripts\python.exe -m unittest discover -s tests -q` → all pass.

## Test plan

- Pure geometry tests at 100%, 125%, 150%, and 200% representative extents.
- Round-trip hit-testing for corner, center, and boundary-adjacent points.
- Existing drag, Apply/Reset, Cancel, and lifecycle tests remain green.
- Physical `run.bat` inspection on mismatched-DPI Windows machines remains the
  final acceptance gate.

## Done criteria

- [ ] The canvas grid spans its actual mapped width and height.
- [ ] Exactly seven columns and four rows render at all tested extents.
- [ ] Cell drawing and drag hit-testing use identical scaled boundaries.
- [ ] Focused, full-suite, compileall, and whitespace gates pass.
- [ ] No files outside the in-scope list are modified.

## STOP conditions

Stop and write a handback if:

- Plan 010 changes editor ownership so geometry is no longer local to
  `TopologyEditor`.
- Tk reports inconsistent canvas dimensions after the widget is mapped and one
  configure-cycle retry.
- Correct scaling requires changing application-wide DPI awareness.
- A verification fails twice after a reasonable focused fix.

## Maintenance notes

Future visual changes must use the shared geometry helper for both rendering and
hit-testing. Do not reintroduce raw `CELL_SIZE` pixel arithmetic in event code;
`CELL_SIZE` is a logical design unit, not a guaranteed physical pixel count.

