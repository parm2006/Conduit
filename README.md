# 🚀 DeskFlow v4.3s

**DeskFlow** is a lightweight wireless KVM for Windows. Share your mouse, keyboard, rich clipboard, and Explorer file transfers between 2 PCs over your local network.

### ✨ What's New in v4.3s
* **Synchronized Background Daemon Mode (`Ctrl + Shift + Alt + B`):** Hides the GUI window completely out of the way on both Host and Client PCs in sync across the network. Pressing it again restores visibility on both ends.
* **Seamless Connection Soft-Reset (`Ctrl + Shift + Alt + R`):** Soft-resets socket connections between server and client in reality without disturbing window visibility if hidden in daemon mode.
* **Race Condition & Reconnect Hardening:** Eliminates control lane authentication race conditions on quick reconnects with automatic stale session replacement and transient network retries.
* **Pre-Disconnect & Exit Visibility Sync:** Emergency exit (`Ctrl + Shift + Alt + Esc`) and explicit disconnects notify the remote peer and unhide the GUI window so neither side is left hidden.

### 🛠️ All Features
* **Mouse Roaming & Resolution Scaling:** Smooth edge transition with auto-calculated X/Y speed scaling across 4K, 1440p, and 1080p screens.
* **Low-Latency Keyboard Simulation:** Full forwarding of modifiers (`Ctrl`, `Alt`, `Shift`, `Win`) with strict host suppression so passwords aren't typed on the host screen.
* **Rich Clipboard Sync:** Supports Plain Text, Formatted HTML, RTF, PNG/DIB Images, and Web MIME formats with Zlib compression and SHA-256 echo suppression.
* **Explorer File Paste:** Transfer files on demand by pressing `Ctrl+V` on the client, powered by a fast 256 KiB sliding-window pipeline (~5.5 MiB/s).
* **TLS Security & Pairing:** Auto-generated RSA certificates, encrypted lanes, session-bound tokens, and interactive 4-digit pairing approval.
* **Global Hotkeys:**
  - **`Ctrl + Alt + Shift + B`**: Toggle Background Daemon Mode (syncs across network).
  - **`Ctrl + Alt + Shift + R`**: Reconnect / Soft-reset connection.
  - **`Ctrl + Alt + Shift + Escape`**: Emergency Exit (restores GUI & local input).

### 📦 Quick Start
1. Run **`DeskFlow.exe`** on both Windows 10/11 PCs.
2. **PC 1 (Host):** Select edge $\rightarrow$ **Start Server**.
3. **PC 2 (Client):** Enter Host IP & Password $\rightarrow$ **Connect**.

