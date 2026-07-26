# Handoff: Background Daemon Mode, Reload Invisibility & v4.3s Release

- **Branch:** `main` (merged from `background-daemon`, tag `4.3s`)
- **Date:** 2026-07-25
- **Status:** COMPLETED & MERGED (Release Tag: `4.3s`, Executable: `dist/DeskFlow.exe`)

## Completed Work

1. **Synchronized Background Daemon Mode (`Ctrl+Shift+Alt+B`):**
   - Updated `GlobalHotkeyMonitor` (`app/global_hotkey.py`) with `on_toggle_daemon` callback and normalized `b` key detection (`vk in (66, 98)` / `char in ("b", "\x02")`).
   - Implemented `set_daemon_mode(hidden)` and `toggle_daemon_mode()` in `DeskFlowGUI` (`app/gui.py`) using `withdraw()` and `deiconify()`.
   - Synchronized daemon mode state over control network using `set_daemon_mode` JSON messages so that pressing `Ctrl+Shift+Alt+B` on either Host or Client toggles GUI visibility on **both screens in sync**.

2. **Invisibility Lock on Connection Reload (`Ctrl+Shift+Alt+R`):**
   - Added `_is_reloading` state to `DeskFlowGUI` (`app/gui.py`).
   - While reloading connection, disconnect handlers (`_on_server_client_disconnected`, `_finish_client_disconnect`, `disconnect_client`, `_on_disconnect_notice`) are suppressed from calling `ensure_visible()`.
   - Registered `reload_connection` control network callbacks so both Host and Client maintain current window visibility during real socket disconnects and reconnects.

3. **Control-Lane Reconnect Race Condition & Transport Hardening (`app/network.py`):**
   - Fixed control-lane authentication race condition during quick reconnects by introducing stale session detection and automatic prior-session replacement in `NetworkServer._handle_candidate()`.
   - Added a 1-time 0.4s transient network retry loop in `NetworkClient.connect()` for control-lane connections, filtering out non-transient auth/password errors.

4. **Pre-Disconnect Notification & Emergency Exit (`Ctrl+Shift+Alt+Escape`):**
   - Emergency exit (`Ctrl+Shift+Alt+Escape`) resets `_is_reloading = False` and unconditionally restores window visibility (`deiconify()`, `lift()`, `focus_force()`).
   - Explicit disconnects send a `disconnect_notice` packet over the control lane prior to socket closure to inform the remote peer and unhide the GUI on both sides.

5. **Release v4.3s & Build Artifacts:**
   - Merged `background-daemon` branch into `main`.
   - Created annotated git tag **`4.3s`** and pushed `main` + tags to GitHub (`origin`).
   - Built standalone single-file Windows executable **`dist/DeskFlow.exe`** (18.0 MB).
   - Updated `README.md` with v4.3s features, hotkeys, and quick start guide.

6. **Verification & Testing:**
   - Added `tests/test_daemon_mode.py` covering daemon toggle, network sync, reload invisibility lock, and pre-disconnect notice handlers.
   - Added hotkey test cases in `tests/test_global_hotkey.py`.
   - 332 / 332 unit tests passing (`Ran 332 tests in 21.404s - OK`).
