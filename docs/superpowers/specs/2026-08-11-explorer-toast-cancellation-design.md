# Explorer and Toast Cancellation Design

**Status:** Approved for implementation

**Baseline:** `8d17fdff4ef56f2181cc39411685512cb534dd34`

**Scope:** Windows Explorer conflict prompts, DeskFlow transfer cancellation,
toast dismissal, destination-latch release, and safe empty-folder cleanup

## Problem

DeskFlow and Windows Explorer currently reach separate conclusions about one
paste. Closing Explorer's conflict prompt can leave DeskFlow waiting until its
timeout. Cancelling DeskFlow can stop the network transfer while leaving the
Explorer prompt open. A cancelled folder paste can also leave a new empty
folder at the destination.

The split originates in three boundaries:

- `VirtualFileDataObject.SetData` notices `Performed DropEffect` but discards
  its `DROPEFFECT` value.
- `TransferReceiver` terminalizes jobs through several independent methods,
  so the first terminal reason and peer notification are not centralized.
- `VirtualPastePublisher` owns the virtual clipboard but does not retain a
  correlated Explorer paste-dialog or destination-folder context.

Microsoft defines `CFSTR_PERFORMEDDROPEFFECT` as the target's notification of
the transfer outcome. It also defines `CFSTR_PREFERREDDROPEFFECT` as the
source's requested COPY, MOVE, or LINK operation:
<https://learn.microsoft.com/en-us/windows/win32/shell/clipboard>.
Explorer does not guarantee a source-side callback for every conflict-dialog
action, so DeskFlow must combine OLE evidence with a narrowly correlated
window-lifecycle observation.

## Required behavior

One accepted paste job has one terminal result.

- Explorer **Cancel**, the window **X**, and **Don't copy** end the job as
  `CANCELLED`, close the DeskFlow toast, notify the peer once, retire the
  virtual clipboard owner, and release the destination latch.
- DeskFlow toast **Cancel** ends the job as `CANCELLED` on both peers and
  dismisses only the conflict popup correlated with that paste. While the
  conflict prompt is still waiting, this is equivalent to **Don't copy**: no
  destination file is created and later confirmation cannot restart the job.
- **Copy and replace** continues the transfer.
- Cancellation after Explorer has already written bytes stops future reads and
  transfer work. DeskFlow does not delete visible files to simulate rollback.
- DeskFlow removes a top-level destination folder only when it recorded that
  the folder did not exist before this paste and `rmdir` confirms it is still
  empty. DeskFlow never removes an existing or nonempty folder.

## Approaches considered

### OLE outcomes only

DeskFlow could decode `Performed DropEffect` and rely on stream closure. This
is the smallest change, but it cannot close the toast when Explorer closes a
conflict prompt without publishing either event. It does not meet the required
behavior.

### Replace Explorer's copy workflow

DeskFlow could discover the destination, write files itself, and display its
own conflict prompt. That approach gives DeskFlow full control but replaces
the native delayed-rendering workflow, expands the security boundary, and
creates a second file manager. It is out of scope.

### OLE outcomes plus a correlated Explorer paste session

This design uses Shell outcomes whenever Explorer publishes them and tracks
only the window and filesystem context latched at paste injection. It closes a
popup only when Windows ownership and process relationships tie that popup to
the destination Explorer window. It never searches by translated title or
button text. This is the selected approach.

## Architecture

### `ExplorerPasteSession`

A small Windows-specific component owns the OS observations for one accepted
job. `VirtualPastePublisher` creates it before injecting `Ctrl+V` and gives it
the job ID, manifest, and destination window.

The session:

1. captures the foreground Explorer window and its process before injection;
2. records the existing top-level windows;
3. resolves the destination's local filesystem folder when Shell exposes it;
4. records which top-level manifest directories are absent;
5. detects a new visible popup only through owner/root-owner and process
   relationships with the latched Explorer window;
6. observes whether that popup remains open or closes; and
7. closes only that recorded popup when DeskFlow cancels the job.

If DeskFlow cannot correlate exactly one popup, it performs no window action.
It still cancels its transfer state. It never falls back to a title search,
global Escape, foreground-window guess, or broad Explorer termination.

The implementation injects the Win32/Shell adapter. Unit tests therefore use
deterministic fake windows and paths without opening Explorer.

### OLE outcome handling

`VirtualFileDataObject` exposes `CFSTR_PREFERREDDROPEFFECT` with
`DROPEFFECT_COPY`. It decodes the four-byte value received through
`CFSTR_PERFORMEDDROPEFFECT` and forwards it to the publisher.

- `DROPEFFECT_COPY` is positive Explorer acceptance.
- `DROPEFFECT_NONE` is cancellation for this copy-only virtual data object.
- malformed or unsupported effects fail closed and cannot report success.

Stream-open evidence and positive drop evidence take precedence over popup
closure. When a correlated popup closes without either form of positive
evidence, the publisher waits for a short, bounded resolution grace period.
If no stream opens and no positive outcome arrives, it records Explorer
cancellation. Detecting the correlated popup also pauses the existing
Explorer-start timeout while the user decides.

### One receiver-owned terminal transition

`TransferReceiver` gains one idempotent terminal method. Cancellation,
publisher failure, incomplete abandoned streams, disconnect, Explorer outcome,
and toast cancellation call this method.

The method performs the first transition only:

1. records phase, reason code, byte coverage, and terminal time;
2. wakes blocked virtual streams;
3. aborts encrypted staged data for failed or cancelled jobs;
4. updates the local `TransferController`;
5. queues one terminal `paste_progress` message for the peer; and
6. retains a bounded terminal tombstone so late frames and duplicate events
   cannot restart or change the job.

Later events return the stored outcome. Explorer Cancel followed by toast
Cancel, and toast Cancel followed by a late Explorer callback, therefore
produce one notification and one cleanup.

The existing `cancel_job`/`cancel_ack` protocol remains the user-requested
cancellation handshake. Both endpoints route its result through the same
receiver transition. The sender's current cancellation checks stop network
work after the destination reports `CANCELLED`.

### Publisher retirement and latch release

`VirtualPastePublisher` keeps the `ExplorerPasteSession` with its current OLE
owner. While a correlated conflict popup is visible, the publisher pumps COM
messages and keeps the paste pending without consuming the start timeout.

When the receiver becomes terminal, the publisher:

1. closes the correlated popup for DeskFlow-initiated cancellation;
2. retires DeskFlow's current virtual clipboard owner;
3. runs safe empty-folder cleanup for cancelled jobs;
4. releases the session; and
5. decrements its pending count.

`FilePasteService.destination_paste_active` then becomes false through its
existing publisher-pending predicate. This preserves the destination latch
during the decision and releases screen control after the terminal result.

### Empty-folder cleanup

The session derives cleanup candidates only from top-level directory entries
in the accepted manifest and a filesystem destination resolved before paste.
For each candidate it records `existed_before`.

After cancellation, cleanup uses nonrecursive `rmdir` only when:

- `existed_before` is false;
- the candidate remains under the latched destination folder;
- the candidate is a directory; and
- the directory is empty at deletion time.

Failure to resolve, inspect, or remove a candidate is logged with a safe error
class and otherwise ignored. Logs omit full paths.

## Ordering rules

The first terminal transition wins. These rules resolve races:

1. A completed job cannot become cancelled.
2. A cancelled or failed job cannot become completed.
3. Positive stream or OLE evidence observed during the popup-resolution grace
   prevents a false cancellation and allows **Copy and replace** to continue.
4. A toast cancellation closes the correlated popup and terminalizes
   immediately; later stream requests receive Windows' cancellation error.
5. Popup disappearance alone never reports success.
6. Uncorrelated Windows are never closed or used as cancellation evidence.

## Diagnostics and privacy

Add one lifecycle line per boundary: publish, popup correlated, popup closed,
stream opened, performed effect, cancellation source, terminal phase, popup
dismissal result, owner retirement, and empty-folder cleanup result.

Logs include job ID, reason code, effect, and boolean correlation outcomes.
They exclude clipboard contents, full paths, filenames, encryption material,
and file bytes.

## Tests

Automated tests cover:

- preferred COPY publication and valid/invalid performed-effect decoding;
- popup correlation and rejection of unrelated windows;
- Explorer Cancel/X/Don't-copy before a stream opens;
- Copy-and-replace stream acceptance after popup closure;
- toast cancellation on the source and destination endpoints;
- exactly-once local state, peer notification, owner retirement, and latch
  release for both cancellation orders;
- late stream/frame rejection after cancellation;
- deletion of a newly created empty top-level folder;
- preservation of existing, nonempty, unresolved, and out-of-root folders; and
- successful transfer and reconnect regressions.

Focused tests run before the full suite. The release build must also pass.

## Physical validation gate

After automated verification, create
`docs/plans/clipboard-explorer-reliability/validate_explorer_cancellation.md`
and a versioned executable. Pause for two-PC validation with both role
directions.

The matrix includes Explorer Cancel, X, Don't copy, Copy and replace, toast
Cancel while the conflict prompt is open, a new folder cancellation, an
existing-folder preservation case, immediate control recovery, and a fresh
successful transfer afterward. Record only terminal codes and safe lifecycle
events.

If the target Windows build does not expose a uniquely correlated popup or a
reliable close/accept ordering, stop at this gate. Do not broaden matching or
automate an unverified window.

## Scope boundaries

This work binds the cancellation context to the accepted job, destination
Explorer window, and destination folder. It does not add the separate
clipboard-offer revision to manifest request/response messages; that remains
the immutable-offer protocol phase. It also does not tune large-transfer
limits, delete partially written destination files, or automate arbitrary
Explorer dialogs.
