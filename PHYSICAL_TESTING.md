# Conduit Two-Client Physical Test Guide

Use this guide with one Server PC and two Client PCs. Run the phases in order.
If a phase fails, stop there and report the failed step before testing later
phases.

## Prepare all three PCs

1. Close every older Conduit process.
2. Put the same `Conduit-v5.1.1.exe` development build on all three PCs. Do not
   use the older GitHub v5.1.1 build on either Client.
3. Confirm the executable's SHA-256 hash on each PC:

   ```powershell
   Get-FileHash .\Conduit-v5.1.1.exe -Algorithm SHA256
   ```

   Expected hash:
   `EC4C38174466A4F5FA60B806F8A1F885E509B28AE675F57CE8CD252D334A751D`
4. Use `ParthPC` as the Server. Use the same password and base port on both
   Clients. Keep the Server network classified as Private in Windows.
5. Write down each PC's Windows machine name. Conduit should show those names,
   never the Server's development address, in topology-related UI.

## Phase 1: One-Client baseline

1. Start the Server, then connect only Client 1.
2. If Conduit shows a pairing prompt, compare the code on the Server and
   Client, then approve it.
3. Confirm a blue identification toast appears on Client 1's primary display.
   It should use the normal file-toast size and remain visible.
4. Confirm the Server grid shows the Server and Client 1 automatically. Each
   physical monitor attached to a PC should appear as a fixed adjacent cell
   with the PC's first letter.
5. Before Apply, try crossing the proposed boundary. Client 1 must remain
   unroutable.
6. Place Client 1 against one full Server edge and select **Apply**. The toast
   should disappear only after Apply succeeds.
7. Move the Server-controlled pointer across the edge in both directions.
8. Hold and release `Ctrl`, `Shift`, and one mouse button while crossing. No key
   or button may remain stuck.
9. Copy text on each PC and paste it on the other PC.
10. Copy one small file on each PC and paste it on the other PC.

Stop and report any Phase 1 failure before connecting Client 2.

## Phase 2: Add Client 2

1. Connect Client 2 with the same Server password and port.
2. Confirm a green identification toast appears on Client 2's primary display.
   Client 1 must remain connected and usable through the old active layout.
3. Confirm Client 2 appears automatically in the Server grid but remains
   unroutable before Apply.
4. Confirm both Clients' detected physical-monitor groups match Windows. The
   monitor cells inside each group must stay fixed when you drag the PC.
5. Arrange a line that supports direct Client-to-Client travel:

   ```text
   Server | Client 1 | Client 2
   ```

6. Select **Apply**. Client 2's visible identification toast should disappear;
   no identification toast should remain on either Client.
7. Move the pointer Server → Client 1 → Client 2, then back Client 2 → Client 1
   → Server. The pointer must cross Client 1/Client 2 directly without appearing
   at the Server center between them.
8. Compare pointer speed on all three PCs. Motion should feel proportional to
   each destination's resolution.
9. Move a Client's physical mouse while the Server-controlled pointer is on a
   different PC. The local pointer may move locally, but it must not move or
   replace the Server-controlled pointer.

## Phase 3: Draft, Cancel, and failure safety

1. Drag one Client away to create a gap, then select **Apply**.
2. Confirm Apply is rejected and the disconnected Client group turns red. The
   Server cell must stay gray.
3. Confirm the previous active routing still works after the failed Apply.
4. Start another edit, then move the Server-controlled pointer to another PC.
   The edit should cancel and the old layout should return.
5. Start another edit and select **Cancel**. Any visible identification toast
   should close and the active layout should remain unchanged.
6. Disconnect Client 2. The Server should show one warning, remove Client 2
   from the draft, and keep the old active boundary calculations until the next
   Apply.
7. Reconnect Client 2. Its saved position should return in the draft, but it
   must remain unroutable until Apply succeeds again.

## Phase 4: Physical-monitor changes

Run this phase on any PC with more than one attached monitor.

1. In Windows Display Settings, arrange a layout that includes rotation or a
   monitor left of the primary display. In Conduit, compare the physical
   monitor count and relative cell order. Confirm the identification toast
   appears on that PC's Windows primary display and lists its resolutions.
2. Unplug one secondary monitor while Conduit is connected.
3. Within about two seconds, confirm the Server shows one normal-sized orange
   warning and removes that monitor from the draft without silently changing
   the active graph. The warning should dismiss itself after about five seconds.
4. Select **Apply** and confirm routing rebuilds against the new display group.
5. Reconnect the monitor, wait for it to return automatically in the draft,
   select **Apply**, and confirm its stable physical position returns.

## Phase 5: Global clipboard

1. Copy and paste plain text from each of the three PCs to each other PC.
2. Repeat with browser rich text/HTML and screenshots.
3. Copy two screenshots rapidly on different PCs. Copying the second item must
   remain responsive. After processing settles, all three PCs must paste the
   same one of those two screenshots; either may win according to Server
   receive order.
4. Copy identical content twice and confirm it remains pasteable everywhere.
5. Copy on a Client using that Client's local keyboard while the
   Server-controlled pointer is elsewhere. The shared clipboard should update
   without moving the roaming pointer.

## Phase 6: Files and concurrency

Test all six directed routes with a small file:

- Server → Client 1
- Client 1 → Server
- Server → Client 2
- Client 2 → Server
- Client 1 → Client 2
- Client 2 → Client 1

For each route, copy the file on the source and paste on the destination. Bytes
must begin moving only when you paste. Only the source and destination should
show transfer notifications.

Then run these failure checks:

1. Start a larger paste and copy new clipboard content during the transfer.
   The copy must remain responsive and become the newest shared item.
2. Cancel a transfer and confirm only that job stops.
3. Start another transfer, then disconnect one involved Client. The paste must
   cancel on the source and destination; the uninvolved PC should show no
   transfer toast.

## Phase 7: Capacity and replacement

This phase requires a temporary third Client PC or Windows VM in addition to
the two connected Clients.

1. Attempt to connect the third Client. Confirm it appears purple and the
   Server explains the two-Client limit.
2. Make no choice. Confirm the pending connection closes after 15 seconds.
3. Try again and reject it. Both current Clients should remain unchanged.
4. Try again and replace Client 1. Confirm the replacement inherits Client 1's
   draft position, stays purple/unroutable before Apply, then inherits Client
   1's slot color only after successful Apply.
5. Repeat by replacing Client 2.

If no fourth machine or VM is available, report Phase 7 as `NOT RUN — no third
Client available` rather than PASS.

## Phase 8: Cluster commands

Run shutdown last because it closes every connection.

1. Press `Ctrl + Alt + Shift + B`. All three Conduit apps should enter or leave
   background mode together.
2. Press `Ctrl + Alt + Shift + R`. Both Clients should reload, the Server should
   release input first, and no modifier or mouse button should remain stuck.
3. Re-Apply if the reloaded sessions return as draft-only.
4. Press `Ctrl + Alt + Shift + Escape`. All three Conduit apps should close
   their connections and return control safely.

## Report results

Send one line per failed or skipped step:

```text
Phase/step:
Expected:
Observed:
PC role and Windows machine name:
Single or multiple physical monitors:
Reproducible every time, sometimes, or once:
```

Attach a screenshot when the grid or toast is wrong. Do not include passwords,
pairing codes, private filenames, clipboard contents, or network addresses.
