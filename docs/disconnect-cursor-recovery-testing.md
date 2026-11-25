# Disconnect, cursor and Remote Mode recovery preview

Test branch: `codex/networking-disconnect-cursor-recovery`.
Based on `codex/networking-optimization`, including the pending five-second
file-preparation / first-paste reliability work. Run Stage 1 **Full** on both PCs.
This is a development preview, not a published beta.

## What changed

- Server stop and Client disconnect immediately cancel local file work, without
  waiting for an acknowledgement from a disconnected peer. File-lane loss also
  terminates affected routed transfers; unrelated Client jobs stay independent.
- Cancel runs outside the GUI thread. Transfer status callbacks enter a queue
  drained by Tk instead of calling Tk from network/Explorer workers.
- Explorer reads wake on cancellation even if the controller was already terminal.
  A read waiting for unavailable file bytes fails after **15 seconds without
  progress**. Incoming bytes reset this deadline. This is not a 15-second maximum
  transfer duration, nor a deadline for answering Windows file-conflict prompts.
  Failed toasts dismiss after three seconds; cancelled toasts dismiss immediately.
- Aborted virtual-file publications release their OLE owner. Sender ACK waits stop
  when the destination reports a terminal failure.
- The source supplies a click-through software pointer while Windows reports its
  cursor hidden/suppressed, including no-mouse and hide-while-typing cases. The
  viewer supplies its pointer only when the captured image lacks one. Both native
  DXGI and JPEG paths report image cursor ownership. No Windows cursor preference
  is changed. Optional window startup happens after the ownership ACK, with a
  100 ms wait budget to avoid consuming handoff/native startup deadlines. Rebuilt
  native DLL is included.
- Remote Mode queues an entry warp and then a Tk readiness marker. Stale startup
  motions cannot immediately send the pointer back across the entry edge. Entry
  projection rounds inward, and every new handoff refreshes the marker.
- INFO logs record handoff readiness, selected display, native stream request/ACK,
  first frame, errors, JPEG fallback, timeouts and cursor policy transitions.
  Per-movement warp logs are DEBUG to keep normal diagnostics readable.

## Two-PC checks

1. Copy a large file in each direction. During network transfer, stop the Server.
   Explorer should finish with cancellation/error instead of remaining blocked;
   Conduit's toast should disappear. Reconnect and paste a fresh file once.
2. Repeat with **Cancel** in the Conduit toast and with **Cancel** in Explorer.
   The Conduit GUI must remain responsive. Verify no successful completion is
   reported for the partial destination file. Windows may leave a partial file;
   Conduit does not remove arbitrary files from the destination folder.
3. Simulate a network interruption rather than Stop Server. If disconnect detection
   does not fire first, an Explorer read waiting for bytes should fail after
   15 seconds without file progress. A healthy transfer taking longer than
   15 seconds must continue normally.
4. With ordinary KVM and then Remote Mode, unplug and reconnect the mouse on each
   computer **after connecting**. Move and type into Notepad. Check for one visible
   cursor on the Client and one representation in the viewer. Return locally and
   confirm Conduit's software pointer is removed.
5. Use the laptop as Server and PC as Client. Enter and leave Remote Mode at least
   20 times, then swap roles. Check every configured entry edge, including mixed
   resolutions/scales, and switch between Client displays if available.

Trackpad tuning and UDP mouse transport are outside this change. Video encryption
and file hash protocol remain unchanged.

## Diagnostics after a failed crossing

Keep logs from **both computers**, with the approximate time and which was Server:

- `%LOCALAPPDATA%\Conduit\conduit.log` plus `.1` / `.2` if present.
- `build\video-stage1\full-<timestamp>.stderr.log` and matching `.stdout.log` from the launcher.
  The launcher prints the exact paths when starting.

Search for `[remote-entry]`, `[video]`, `[cursor]`, `[file]` and `Switch accepted`.
Matching handoff and stream IDs distinguish a bounce before capture from missing
ACK, source initialization failure, missing first frame or a later stall. Stream
encryption keys and file contents are not added to these logs.

For a short reproduction needing individual pointer warps, set this before launch:

```powershell
$env:CONDUIT_LOG_LEVEL = 'DEBUG'
```

Clear it afterward with `Remove-Item Env:CONDUIT_LOG_LEVEL` to restore INFO logging.

## Local verification

- Rebuilt the native streamer DLL from the changed sources.
- Final complete suite: **1,120 tests passed**, 81.491 seconds.
- Regression tests cover terminal-first cancellation, file-only disconnect,
  stalled reads, progress resetting the read deadline, OLE-owner cleanup,
  GUI-thread independence, entry coalescing and stale handoffs, cursor visibility
  transitions, and ownership ACK preceding optional cursor-window startup.
- Real Win32 window/DLL-setter smoke passed three start/stop cycles: 19.4 ms,
  2.6 ms and 3.3 ms window startup on this PC, all inside the 100 ms budget.
- Physical two-PC disconnect/hotplug/typing and repeated laptop-Server crossings
  remain acceptance checks for this preview.
