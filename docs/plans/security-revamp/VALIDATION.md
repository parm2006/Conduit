# DeskFlow focused two-PC validation

This handoff contains only the physical tests needed for the current clipboard,
edge-placement, and file-transfer changes. Earlier pairing, identity, window,
keyboard, security, and basic connection tests have passed and are outside this
retest.

Use two Windows PCs on the same private network. Call them `SERVER_PC` and
`CLIENT_PC` in your notes. Use disposable files only. Run the sections in
order, and record `PASS`, `FAIL`, or `NOT RUN` for each section.

Do not publish IP addresses, usernames, computer names, Wi-Fi names, MAC
addresses, fingerprints, clipboard contents, filenames, or absolute paths.
Replace private values with placeholders before sharing output.

If DeskFlow traps input, press `Ctrl+Alt+Shift+Escape` on the server keyboard.

## 0. Install and automated gate

Close DeskFlow on both PCs. Run on both PCs:

```powershell
git fetch origin
git switch codex/rebuild-from-5f97c81
git pull --ff-only
git rev-parse HEAD
git status --short
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

Pass when both PCs print the same full commit, both worktrees are clean,
compilation succeeds, both test suites finish with `OK`, and `git diff --check`
prints nothing.

If the client reproduces an order-dependent network-test timeout but that same
test passes five times by itself, record the full-suite failure and isolated
passes. Continue with the physical checks; do not hide the flaky result.

## 1. Connection and ordinary-use smoke test

1. Start DeskFlow on both PCs and connect normally.
2. Cross to the client, move, click, scroll, and type in Notepad.
3. Cross back to the server.
4. Copy and paste plain text in both directions.
5. Copy and paste one screenshot in both directions.

Pass when control crosses cleanly, input arrives once, and text and screenshots
paste correctly. Stop here if the basic connection is unusable.

## 2. Edge and corner placement

Test all four client layouts. The Left layout needs the most attention because
that is where the corner offset was reproduced.

For each layout:

1. Cross near the start, middle, and end of the shared edge.
2. Return across the matching edge.
3. Leave the server at each exact corner on that edge.
4. Confirm the client cursor appears at the corresponding corner with only the
   small safety inset, not about 60 or 96 pixels away.
5. Hold the physical mouse still for one second after entry. Confirm the cursor
   does not jump because of the synthetic warp event.
6. Disconnect, reconnect, and repeat one corner crossing.

For a client on the left, explicitly test server `(0, 0)` and `(0, Y_max)`.
The client should enter near `(X_max, 10)` and `(X_max, Y_max - 10)`, adjusted
only for Windows coordinate bounds.

Pass when every layout preserves the position along the shared edge and no
corner shows the old large offset.

## 3. Google Docs rich clipboard

Run every item Google Docs to Google Docs, first server-to-client and then
client-to-server. Use documents you can safely discard.

1. Copy several paragraphs containing blank lines. Confirm every Enter and
   blank line survives.
2. Repeat with a heading, font family, font size, bold, italic, text color,
   highlight, a link, and a list.
3. Select one image by itself, press Ctrl+C, and paste it into the other Doc.
4. Copy one selection containing formatted text, blank lines, and two images.
5. Copy a small table containing text and images.
6. For images in mixed content, test the Docs layout modes you use: inline,
   wrap text, break text, behind text, and in front of text.
7. Copy the same mixed selection twice and paste after each copy.
8. Copy text A, text B, and screenshot C quickly. Confirm C wins.
9. Disconnect, reconnect, and repeat one mixed text-and-image copy.

Pass when paragraph breaks, formatting, images, tables, and supported Docs
image layout state survive in both directions, and the newest copy wins.

Record a failed case by source application, direction, selection type, portable
format kind, source order, and byte count. Do not record clipboard contents or
encoded image data.

Word and cross-application Google Docs/Word behavior are deferred. They are not
part of this focused retest.

## 4. Large-file throughput and input priority

Use a random 100 MiB file and record its source SHA-256:

```powershell
$testDir = Join-Path $env:USERPROFILE "Desktop\DeskFlow-Validation"
New-Item -ItemType Directory -Path $testDir -Force | Out-Null
$path = Join-Path $testDir "DeskFlow-100MiB.bin"
$buffer = [byte[]]::new(1MB)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$stream = [IO.File]::Create($path)
1..100 | ForEach-Object {
    $rng.GetBytes($buffer)
    $stream.Write($buffer, 0, $buffer.Length)
}
$stream.Dispose()
$rng.Dispose()
Get-FileHash $path -Algorithm SHA256
```

Run two transfers server-to-client and two client-to-server. For each transfer,
record elapsed seconds, MiB/s, source hash, and destination hash.

During one server-to-client transfer:

1. run `ping <CLIENT_IP> -n 100` from the server;
2. move the server-controlled client cursor continuously;
3. type, click, and scroll in an application other than the Explorer copy
   window;
4. cross back to the server and return to the client;
5. copy and paste text;
6. copy and paste a screenshot.

Repeat the responsiveness checks during one client-to-server transfer.

Pass when all four hashes match, mouse and keyboard control remain usable,
ordinary clipboard sync remains usable, the cursor stays visible, and ping does
not develop multi-second stalls during the transfer. Record every speed. Repeat
one run if it falls below 8 MiB/s; report both results instead of discarding the
slow result.

Explorer may delay input in the window performing its synchronous paste. Other
applications and DeskFlow control must remain responsive.

## 5. Multi-file paste and clipboard lifecycle

Prepare three disposable files with different names, sizes, and hashes.

1. Select all three files in Explorer, copy once, and paste once on the other
   PC.
2. Confirm all three names, sizes, and hashes match.
3. Repeat immediately with three new files. Repeat the whole sequence three
   times to exercise Explorer's late stream requests.
4. Run the same test in the other direction.
5. Confirm neither console reports `KeyError`, `Unexpected exception in gateway
   method 'GetData'`, or `Could not restore clipboard after virtual paste`.
6. Paste one copied file where the same name already exists and choose
   **Don't copy**.
7. Confirm that file remains the active clipboard offer for a later explicit
   paste.
8. Copy newer text and then a screenshot. Confirm each newer copy replaces the
   file offer normally.

Pass when every requested file appears once, late Explorer requests cannot
crash the provider, **Don't copy** keeps the user's file copy, and newer
clipboard content still wins.

## 6. Cancellation and recovery

File queueing is intentionally out of scope. Use the large disposable file
from section 4 to verify that each active transfer cleans up independently.

1. Start a large server-to-client transfer and cancel it at the source. Confirm
   both toasts clear and a new small-file paste works without reconnecting.
2. Start a large client-to-server transfer and cancel it at the destination.
   Confirm both toasts clear and a new small-file paste works without
   reconnecting.
3. Disconnect once during an active transfer. Reconnect, then paste text, a
   screenshot, a multi-file selection, and one small file.

Pass when each cancellation finishes exactly once, no partial destination
remains, and reconnecting creates a clean session without stale jobs or
invisible cursors.

## 7. Final clean restart

1. Close both apps normally.
2. Confirm ports 5000-5002 no longer listen on the server.
3. Confirm no unexpected DeskFlow Python process remains.
4. Reopen both apps and reconnect with saved settings.
5. Cross once, type, paste rich Google Docs content, paste a screenshot, and
   perform one multi-file paste.
6. Close both apps again.

Pass when restart requires no repair, re-pair, or stale-process cleanup.

## Results template

```text
Full commit on SERVER_PC: <FULL_COMMIT>
Full commit on CLIENT_PC: <SAME_FULL_COMMIT>

0 Install and automated gate: PASS/FAIL/NOT RUN
1 Connection and ordinary-use smoke: PASS/FAIL/NOT RUN
2 Edge and corner placement: PASS/FAIL/NOT RUN
3 Google Docs rich clipboard: PASS/FAIL/NOT RUN
4 Large-file throughput and input priority: PASS/FAIL/NOT RUN
5 Multi-file paste and clipboard lifecycle: PASS/FAIL/NOT RUN
6 Cancellation and recovery: PASS/FAIL/NOT RUN
7 Final clean restart: PASS/FAIL/NOT RUN

First failure:
Expected:
Observed:
Exact status text:
Relevant redacted console lines:
Reconnect or relaunch required: YES/NO
```
