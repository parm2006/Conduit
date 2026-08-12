# Validate: Explorer and toast cancellation sync (Plan 009 v1)

## Build under test

- Functional v5 baseline: `8d17fdff4ef56f2181cc39411685512cb534dd34`.
- Cancellation implementation: `ec49860931c345dbc35a9d890a7c3e8cc9f12e62`
  (`ec49860`).
- Executable:
  `dist-plan009-v1\DeskFlow-ec49860-plan009-v1-explorer-toast-cancellation.exe`
- SHA-256:
  `296811FFDD29B92B32079E09E4B3D9D65CC007C397AC870ADFBAC7B989FC2793`
- Size: 18,470,009 bytes.
- Automated gate: 119 focused tests, all 543 tests, PyInstaller, packaged
  firewall-helper smoke test, and NSIS installer passed.

Use this exact executable on both machines. The laptop does not need the
repository or `run.bat`.

## What this validates

- Explorer **Cancel**, window **X**, and **Don't copy** close the DeskFlow
  toast and release mouse/keyboard control without waiting for a timeout.
- DeskFlow toast **Cancel** acts like **Don't copy**: it closes only the
  conflict prompt for this paste and does not replace the destination file.
- A new empty folder left by cancellation is removed, while existing or
  nonempty folders are preserved.
- A cancelled job cannot restart from a late Explorer callback, and the next
  transfer works without reconnecting.

This is not the large-transfer test. Use small harmless files except for the
folder row described below.

## Setup

1. Close every DeskFlow process on both machines. Check Task Manager if an old
   instance might still be running.
2. Copy the versioned executable above to the laptop and verify the SHA-256 on
   both machines:

   ```powershell
   Get-FileHash .\DeskFlow-ec49860-plan009-v1-explorer-toast-cancellation.exe -Algorithm SHA256
   ```

3. Start that exact executable on both machines and connect normally.
4. Create harmless test folders on both machines. For the file-conflict rows,
   create `conflict.txt` on the source and a different `conflict.txt` in the
   destination folder, so Explorer shows the native conflict prompt.
5. Before every row, recopy the source file/folder and restore the destination
   conflict file. Do not reuse a prompt from a prior row.
6. Run every row in both directions:
   - **Server → Client**: source selection is on the Server; paste in Client
     Explorer.
   - **Client → Server**: source selection is on the Client; paste in Server
     Explorer.

## Validation matrix

| # | Action and expected result | Server → Client | Client → Server | Notes |
|---|---|---|---|---|
| 1 | At the conflict prompt, press **Cancel**. The prompt and toast close promptly, the destination file is unchanged, and control crosses screens without reload/reconnect. | | | |
| 2 | At a fresh conflict prompt, press the window **X**. The prompt and toast close promptly, the destination file is unchanged, and control is released. | | | |
| 3 | At a fresh conflict prompt, choose **Don't copy**. The prompt and toast close promptly and the destination file is unchanged. | | | |
| 4 | At a fresh conflict prompt, choose **Copy and replace**. The transfer completes normally, the replacement is present, and the toast completes/hides normally. | | | |
| 5 | While the conflict prompt is open, press **Cancel on the DeskFlow toast**. Only that conflict prompt closes, the destination file is unchanged, both DeskFlow instances have no lingering transfer toast, and control is released. | | | |
| 6 | Paste a new uniquely named folder containing one harmless 8–10 MB file into a destination where that top-level folder does not exist. As soon as the new folder appears and before its child file appears, press the toast **Cancel**. If Explorer left the folder empty, DeskFlow removes it. If Explorer already created a partial/visible file, DeskFlow preserves it and you should note that instead of deleting it. | | | |
| 7 | Create an existing empty destination folder with the same name as the source folder. Open its merge/conflict prompt and cancel from the toast. The pre-existing empty folder remains. | | | |
| 8 | Create an existing nonempty destination folder with a harmless sentinel file. Cancel the matching folder paste. The folder and sentinel remain untouched. | | | |
| 9 | Keep a second unrelated Explorer window open during row 5. Toast Cancel closes only the correlated conflict prompt; the unrelated Explorer window remains open. | | | |
| 10 | Without reconnecting, copy a fresh small file in the same direction and then the reverse direction. Both transfers complete normally. | | | |

Use `PASS`, `FAIL`, or `N/A` in each direction column. Row 6 may be `N/A`
when Explorer creates a child file before you can cancel; report what appeared
instead of repeatedly racing the UI.

## Stop conditions

Stop that direction and report immediately if any of these occur:

- DeskFlow exits or disconnects;
- an unrelated Explorer window closes;
- toast Cancel leaves the correlated prompt open;
- Explorer Cancel/X/Don't-copy leaves the toast waiting until timeout;
- an existing or nonempty folder is removed; or
- control remains latched and requires `Ctrl+Shift+Alt+R`.

Do not compensate with global Escape, Task Manager termination, or repeated
reloads before recording the failure.

## Failure report

Return the completed table. For a failure, also report:

- direction and row number;
- which button was used;
- whether the prompt closed, whether the toast closed, and whether control was
  released;
- whether the destination file/folder was unchanged, empty, partial, or
  complete; and
- whether a fresh transfer worked afterward.

If the development PC was run through `run.bat`, include only lifecycle lines
containing `job=`, `reason`, `effect`, `correlated`, `closed`, or `result`.
Do not send full paths, filenames beyond the harmless test names, clipboard
contents, passwords, pairing codes, encryption material, or file bytes.

After reporting this matrix, wait for reconciliation. Do not begin large-file
testing yet.
