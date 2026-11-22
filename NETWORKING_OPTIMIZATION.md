# Networking optimization branch

Priority: mouse responsiveness, then reliable keyboard and files, then video.

## Implemented design

- Control clients offer an optional mouse lane over the already authenticated TLS connection. Each connection generates a fresh 256-bit AES-GCM key. The listener uses UDP on the configured base port (28903 by default); video remains on base+3. Firewall rules retain executable, Private-network, and local-subnet restrictions. Existing installations need the updated managed firewall rule for UDP mouse delivery; blocked UDP automatically retains TLS input.
- Absolute positions carry sequence numbers. Relative movement retains the original ordered TLS batches: individual deltas affect edge crossings and screen clamping, so summing or dropping them would change behavior. Replays and out-of-order positions are discarded. Position packets authenticate their sequence and reliable-input barrier.
- Keys, buttons, scroll, handoffs, and fallback movement retain TLS reliability. Click/key messages include the latest movement snapshot so lost movement is repaired before discrete input. New-barrier datagrams are discarded until TLS installs that barrier. Switching from absolute to relative movement restores the latest absolute position through TLS before delivering the original relative batch.
- Fresh HMAC probe acknowledgements enable UDP; confirmation expires after 300 ms. Probes run approximately every 50 ms on a responsive path. The latest position is repeated on confirmation to recover a lost final movement without retransmitting an event history. Reliable input clears obsolete repeats. Reconnect atomically detaches the old lane and uses fresh keys. Older peers continue using TLS.
- InputDispatcher.latency_snapshot(session_id) reports queue dwell and send duration p50/p95/max for the latest 256 sends, plus pending counts, failures, and position coalescing. These are sender timings, not end-to-end input latency or display latency.
- File transfers begin with a 512 KiB outstanding window, grow toward the existing 1 MiB cap, and reduce the window on receiver ACK-delay inflation. The 256 KiB floor allows progress. ACK delay includes receiver and disk work; this is downstream-pressure feedback, not network RTT estimation.
- UDP video reception hands off to a separate decode/render worker with a three-frame/16 MiB bound. Overflow or missing frame IDs clears stale compressed work and requires a keyframe before resuming. Recovery requests are throttled. Shutdown joins the worker before releasing the decoder. H.264 frames are not independently replaceable.

A QUIC migration was not selected: the targeted lane provides datagram semantics while retaining the current reliable control/file protocol. Capture-rate, display-presentation, FEC and video-bitrate changes require measurements on the actual PCs; they are follow-up experiments rather than assumed speedups in this branch.

## Verification and physical acceptance

Automated: `venv\Scripts\python.exe -m unittest discover -s tests`.
Native: `powershell -File scripts\build_streamer.ps1` (MSVC build and Defender scan).

Regression coverage includes real TLS negotiation of mouse UDP, lost/reordered/duplicate/tampered packets, exact relative-batch preservation, absolute/relative transitions, click ordering, blocked UDP fallback, old-key rejection, reconnect cleanup, bounded diagnostics, file-pressure feedback, and compiled C++ video queue lifecycle/reference recovery.

Two-PC performance measurements remain required. Compare this branch with its parent on the same Wi-Fi/Ethernet setup. Record sender queue/send p50/p95, and measure physical input-to-cursor/display response using a high-frame-rate camera or synchronized instrumentation. Repeat idle, during a large file transfer, with Remote Mode active, under packet loss, and with UDP base-port blocked. Verify final mouse position after lost packets; clicks and drag releases at edges; held modifiers; rapid client-to-client handoffs; reconnects; complete file hashes; video recovery and shutdown. Record file throughput alongside input latency so a throughput regression cannot hide behind lower queue depth.

No two-PC speedup or end-to-end latency number is claimed from local regression tests.

## Repeatable transport benchmark

Run from the repository root on Windows. This opens isolated loopback TLS/UDP endpoints, uses temporary DPAPI-protected identities, and injects no OS input.

```powershell
venv\Scripts\python.exe -m scripts.benchmark_networking --samples 1000 --interval-ms 1 --output build\networking-adaptive.json
venv\Scripts\python.exe -m scripts.benchmark_networking --samples 1000 --interval-ms 1 --fixed-file-window --output build\networking-fixed.json
```

Run the commands sequentially with other benchmarks stopped. `--input-kind absolute`, `relative`, or `keyboard` limits the workload; the default runs all eight scenarios. Absolute positions compare TLS and UDP-enabled operation. Relative mouse and keyboard retain TLS ordering. A UDP startup failure invalidates the run instead of silently measuring TLS.

The report includes actual UDP/TLS input-send counts (excluding probes), offered and observed events, coalesced-or-undelivered events, final-position delivery, reliable-event order, producer scheduling lateness, queue/send percentiles, round-trip percentiles, and file bytes/throughput. The mouse ACK returns over TLS. Coalesced/undelivered counts are not a packet-loss estimate. RTT excludes producer scheduling lateness; inspect both fields. File load uses the production sender/window and an in-memory receiver, so its throughput is not disk throughput.

Example local run on 2026-11-22, Windows / Python 3.14.6. Values below are p95 transport round-trip milliseconds from one sequential run pair; this is not a statistical baseline. Raw results: [benchmarks/networking-loopback-2026-11-22.json](benchmarks/networking-loopback-2026-11-22.json).

| Input | Transport | File load | Adaptive window RTT p95 (ms) | Fixed window RTT p95 (ms) |
|---|---|---|---:|---:|
| absolute | tls | no | 0.451 | 0.445 |
| absolute | tls | yes | 0.703 | 0.749 |
| absolute | udp | no | 0.389 | 0.404 |
| absolute | udp | yes | 0.770 | 0.790 |
| relative | tls | no | 0.461 | 0.488 |
| relative | tls | yes | 0.710 | 0.736 |
| keyboard | tls | no | 0.526 | 0.554 |
| keyboard | tls | yes | 0.701 | 0.746 |

All reliable relative/keyboard events arrived in order in these runs. These loopback results do not establish a consistent UDP advantage or a file-window improvement. The next decision requires the two-PC acceptance measurements above, especially Wi-Fi contention and loss. Do not tune capture rate or replace the transport based on this local run alone.
