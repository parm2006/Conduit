# Claude video Stage 1 experiment

This branch tests selected, reviewed components from Claude's Stage 1 response. It is an experiment, not a replacement release. The original download was not compiled or run by Claude; the imported code and wiring need local verification plus two-PC acceptance. Passing queue tests does not demonstrate a latency improvement on Wi-Fi.

The ordinary application checkout is separate from this worktree. Start the app from this worktree to test it. Starting an installed Conduit shortcut runs the installed version instead.

## What the test modes mean

| Mode | Client video sender | Server video receiver |
|---|---|---|
| Baseline | Existing scheduling | Existing receive/decode/present path and original decoder property |
| Decoder | Existing scheduling | Existing topology; corrected low-latency decoder property only |
| Sender | Dirty-frame/trailing-edge scheduler with idle keepalives | Existing receiver topology plus decoder correction |
| Receiver | Existing sender scheduling | Bounded keyframe-aware worker pipeline plus decoder correction |
| Full | Experimental scheduler | Experimental receiver pipeline plus decoder correction |

**Test Decoder mode first.** During local integration, the old decoder configuration was found to pass `VT_BOOL` for `CODECAPI_AVLowLatencyMode`, while the decoder expects `VT_UI4`. The old setting did not produce the intended low-latency behavior. Buffering was especially visible with the new low-rate idle sender: an idle stream could wait seconds for decoder output even though packets were arriving. The corrected property is therefore a dependency of **every non-baseline mode**, including Sender; otherwise reducing idle encodes can make the receiver look stalled. This is a specific codec configuration correction, not proof that all remaining latency is solved.

The default is **Baseline**. `CONDUIT_VIDEO_STAGE1` is set only for the launched test process. Fully exit both apps and relaunch before changing mode. Test both PCs in the same mode so results have an unambiguous configuration.

Modes compare paths within the test DLL; Baseline is not a claim that this binary is byte-identical to the installed release. Keep the installed version available as a separate physical baseline. Input routing, pairing, file transfer, the control heartbeat and Python video-failure policy are not redesigned in this stage. Video failure can still invoke the existing fallback/local-return behavior: record unexpected returns as a failure, rather than increasing timeouts to hide them.

## Build and launch

Use an administrator PowerShell terminal for the source app's normal elevated-input behavior. MSVC Build Tools and the Windows SDK are required to compile the DLL. Use a Python interpreter with the existing requirements already installed; the launcher does not install dependencies.

From the worktree root:

```powershell
powershell -NoProfile -File scripts\run_video_stage1.ps1 -Mode Baseline -Build
```

The first command builds the native DLL in **this worktree** and starts the baseline path. Close Conduit fully before rebuilding, because a running process can lock its DLL.

Subsequent runs can reuse that build:

```powershell
powershell -NoProfile -File scripts\run_video_stage1.ps1 -Mode Decoder
powershell -NoProfile -File scripts\run_video_stage1.ps1 -Mode Sender
powershell -NoProfile -File scripts\run_video_stage1.ps1 -Mode Receiver
powershell -NoProfile -File scripts\run_video_stage1.ps1 -Mode Full
```

Run one command at a time and fully quit the app before running the next. Close any installed or other source copy first; otherwise the listener ports can conflict. Only the active test process should own Conduit's ports.

The launcher selects `C:\Users\parth\Projects\Conduit\venv\Scripts\python.exe` when present, then the worktree's `venv\Scripts\python.exe`, then `python` on PATH. On the other PC, or to choose explicitly:

```powershell
powershell -NoProfile -File scripts\run_video_stage1.ps1 -Mode Baseline -PythonPath C:\path\to\venv\Scripts\python.exe
```

The interpreter supplies dependencies; the launched `run.py` and imported application files come from the test worktree. A successful build alone does not activate the experiment; select a non-baseline mode to do that. For the first physical comparison use Baseline versus Decoder, which isolates the corrected decoder configuration before adding the scheduling or pipeline changes.

## Diagnostics

The launcher prints the chosen mode, source directory, and two log paths. Its default log directory is `build\video-stage1` in the worktree:

- `<mode>-<timestamp>.stdout.log`: native output and probe-style messages.
- `<mode>-<timestamp>.stderr.log`: Python diagnostics, including native mode selection and available sender/receiver statistics when streams stop.

The normal application runtime log under `%LOCALAPPDATA%\Conduit\conduit.log` is still written. The launcher does not change logging settings or account/trust storage. Its mode environment variable is restored when the process exits.

Python `NativeStreamerSender.get_stats()` and `NativeStreamerReceiver.get_stats()` expose optional text snapshots from the test DLL. They return an empty string when used with an older DLL without the diagnostics exports. Statistics are sampled before stream stop and logged; inspect the matching PC's log, not just the server log.

Timing reports use each machine's local clock. They are stage-duration observations, not synchronized cross-PC capture-to-display latency. `Present` returning is also not proof that a physical display has scanned out the image. Do not report those values as glass-to-glass measurements.

## Two-PC test procedure

1. Keep network, topology, monitor resolution/refresh rate, Remote Mode, and source build consistent. Record the GPU models and driver versions. Keep your installed version available for comparison.
2. Build the test branch on each PC. First run Baseline on both. Enable Remote Mode and leave the desktop idle for at least 30 seconds. Confirm the picture stays live and no spurious local return occurs.
3. Scroll text, drag and resize windows, type, click small targets, and drag/release items for about a minute. Check mouse mapping, held modifiers and buttons. A lower video latency number is not a pass if input becomes less reliable.
4. Repeat during a large file transfer. Record file throughput, visible video age/stutters and input responsiveness. Save both PCs' logs after stopping the stream.
5. Switch controlled monitors several times, including monitors with different resolutions/DPI. Return control locally and enter Remote Mode again. Verify clean startup, first frame and pointer mapping each time.
6. Repeat steps 2–5 in Sender mode, then Receiver mode. Only try Full if each isolated change is acceptable. Stop at the first repeatable regression and note the exact mode and activity.
7. Finally test Wi-Fi disconnect/reconnect. Preserve the normal approximately six-second control-heartbeat behavior; confirm input releases and reconnecting works. Check that native ports can be reused without a leftover receiver thread.
8. For visible video latency, film a changing clock/counter on the client and the server's displayed copy together using a high-frame-rate camera. Compare multiple samples across modes; a local trace alone does not measure the network and display scanout.

A failure report should include mode, both PCs' GPU/resolution/refresh rate, Wi-Fi/Ethernet, Remote Mode status, activity, file-transfer status, and the matching two logs. Do not include passwords, stream keys or trust files. Keep first-frame startup, steady-state latency, recovery time and control-disconnect time separate.

## GPU-free tests and codec probe

From the worktree root:

```powershell
powershell -NoProfile -File scripts\test_video_stage1.ps1
powershell -NoProfile -File scripts\test_video_stage1.ps1 -Probe
```

The first command compiles and runs the native queue/scheduler/statistics tests and builds the codec probe. `-Probe` also enumerates this PC's available H.264 transforms. Executables are written to `build\video-stage1\pipeline_tests.exe` and `build\video-stage1\mft_probe.exe`. Run the probe on both PCs and save its output alongside the test logs. Enumeration is a capability report, not proof of the active encoder/decoder path or a latency measurement.

## Boundaries of Stage 1

This stage does not implement hardware-MFT encoder selection, async encoder events, a new wire protocol, sender capture timestamps over the network, FEC/congestion control, a new native top-level viewer, or a replacement Python video-health state machine. The codec probe identifies available transforms; availability alone does not prove which path is active or measure its latency.

Do not merge or ship based on unit tests alone. The normal application remains the fallback if this experiment is worse. Fully quit the test process and launch the installed app to leave the experiment.

## Native integration references

The experiment also needs correct decoded texture-array selection and presentation-handle lifetime. The relevant Windows API contracts are [IMFDXGIBuffer::GetSubresourceIndex](https://learn.microsoft.com/en-us/windows/win32/api/mfobjects/nf-mfobjects-imfdxgibuffer-getsubresourceindex) and [IDXGISwapChain2::GetFrameLatencyWaitableObject](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_3/nf-dxgi1_3-idxgiswapchain2-getframelatencywaitableobject). A waitable swap chain is a presentation-queue control, not proof of a one-refresh end-to-end latency bound.

## Verified decoder setting and local results

The existing decoder passed `VT_BOOL` to `CODECAPI_AVLowLatencyMode`. Microsoft's H.264 decoder specifically requires `VT_UI4`; see the [documented exception](https://learn.microsoft.com/en-us/windows/win32/medfound/codecapi-avlowlatencymode). Decoder/Sender/Receiver/Full use that corrected type and check the result. Baseline retains the old setting deliberately for comparison.

During local integration, the 4 Hz idle scheduler with the old decoder setting produced no presented frames in three seconds. Using the original 30 FPS sender with the old setting produced first output after roughly 19 inputs; the matched first-frame receiver timing was about 791 ms. With the corrected setting, the combined experimental path produced frames, and an initial receiver-local complete-to-Present-return sample was about 65 ms. These are small diagnostic startup observations, not steady-state percentiles, physical scanout measurements, or a two-PC speedup claim. The actual bounded runtime test now requires nonzero sender/receiver counts, FIRST_FRAME, idle progress, resize, and cleanup in every mode.

The codec probe found Intel Quick Sync hardware encoder transforms on this PC, but the existing selected Microsoft encoder appeared in the synchronous/software enumeration and reported no D3D11 awareness. Stage 1 still uses the existing encoder. Hardware enumeration is not proof of which acceleration path the running app uses; proper hardware-MFT selection and async event handling remain Stage 2.

Shared adaptations in every test-DLL mode include bounded/reentrant frame assembly, optional statistics exports, and safe decoder-sample ownership. The runtime mode isolates the algorithmic changes, not every safety correction in the compiled binary.

## Sharing this test build

The source test archive, when provided, includes the compiled test DLL and codec probe. On another Windows PC, extract it into its own folder. Use a Python environment with Conduit's requirements installed, then run the launcher with `-PythonPath` pointing to that interpreter. Recompilation is optional when using the included DLL; do not use `-Build` unless MSVC/SDK are installed. Do not overwrite the installed application's files. Close other Conduit copies before testing.

## Cursor and encoder reliability follow-up

The original `Conduit-video-stage1-test.zip` was compared with commit `ea337e8`: its native DLL, viewer, capture, bridge and Full-mode launcher match byte-for-byte. The original DLL SHA-256 is `e122158664f034d016e1b912382ebf795ff1ef0226c8aa93be26d268f95cd99b`. Both original source copies explicitly enable a local arrow. Earlier cursor-hiding code predates the Claude integration; restoring the original files alone does not explain the reported difference in cursor appearance.

Windows DXGI can supply a separate cursor or pixels that already contain it. The reliability branch carries an optional cursor policy in authenticated packet-header flags, associates it with actual encoder/decoder output sample times, and publishes it only when that image is presented. Both the Canvas and its parent suppress their local cursor for image-owned or source-hidden pointers. Separate pointers keep the local arrow. JPEG fallback already draws the Client cursor into its pixels, so it suppresses the extra local pointer after the image is displayed. Old peers/DLLs remain supported with unknown policy; update and fully restart both PCs to test the fix.

An actual Media Foundation output-error regression was reproduced with a forwarding `IMFTransform` proxy: one encoded reference access unit is consumed, then `ProcessOutput` returns `E_FAIL`. The original encoder silently continued with a dependent frame; the real decoder returned success with pixels differing from an uninterrupted decoder. A second case returns an error while output remains pending; this previously stalled every later input. The recovery fix retains a keyframe request, discards partial/unsafe output, flushes pending transform data and waits for an IDR before sending dependent frames again. In the 1080p synthetic tests, the next emitted packet became an IDR and subsequent decoded pixels matched. Normal `MF_E_TRANSFORM_NEED_MORE_INPUT` is not classified as an encoder error.

Run `python -m unittest tests.test_encoder_recovery tests.test_native_cursor_metadata tests.test_remote_cursor_policy tests.test_video_transport tests.test_video_stage1_assembler` for the focused regressions. The transport tests use real AES-GCM and reversed UDP fragments, tamper with ciphertext and all fragments' cursor flags, require rejection without state changes, then verify recovery with a valid keyframe. The encryption algorithm and authenticated-header protection are unchanged. File-transfer hashing is a separate path.

Diagnostics now log the loaded DLL path/SHA-256/cursor-metadata capability, encoder HRESULT warnings and error counts, receive/authentication/replay counts, and the displayed cursor policy. Packet rejection counts alone do not identify Wi-Fi as the cause; packet loss, queue pressure and codec errors must be distinguished. Synthetic tests do not establish physical latency or prove both PCs' driver paths. Retest Full mode in both server/client arrangements, with pointer movement, idle periods, scrolling/video playback and reentry. Preserve both logs if corruption remains.
