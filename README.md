# Conduit 7.2.1

A fast Windows wireless KVM for sharing your mouse, keyboard, newest clipboard item, on-paste file relay, and low-latency native hardware video streaming across one Server and up to two Clients on the same local network.

[![Download Latest Release](https://img.shields.io/github/v/release/parm2006/Conduit?label=Download%20Latest%20Release)](https://github.com/parm2006/Conduit/releases/latest)

---

## ✨ Features

- **Multi-PC Control**: Seamless cursor and keyboard routing across one Server and up to two Clients with multi-monitor support.
- **Hardware Input Pipeline**: Native hardware scan-code injection and 1:1 synchronized absolute cursor coordinate projection across machines.
- **Native Hardware Video Streaming**: Native 60 FPS DXGI Desktop Duplication capture, hardware H.264 MFT encoding/decoding, AES-GCM encrypted UDP transport, and Direct3D 11 swapchain presentation.
  > [!NOTE]
  > Video stream transfer is in active development. While it is fully functional, hardware-accelerated, and provides low-latency synchronized cursor control, it is not yet 100% perfect across all network conditions and setups, but is in a solid, usable state.
- **Shared Clipboard & Files**: Syncs the global newest clipboard item (text, images, HTML/RTF) and provides an on-paste file relay for files and folders.
- **Visual Display Arrangement**: Displays each Windows machine name in the layout; drag and snap screen edges, then click **Apply**.
- **Secure by Default**: Authenticated pairing, TLS identity, AES-GCM authenticated media encryption, and private network firewall rules.
- **Remote Mode**: Before starting the Server, enable the bottom Remote Mode switch. Only the Client monitor you control is shown fullscreen on the Server primary display, with its cursor and aspect ratio preserved. The switch stays locked until the Server stops and defaults off on a fresh launch.

The live monitor map stays at the Server primary screen's upper-left corner, including while the GUI is hidden. It is 70% opaque, click-through, unlabeled, and grouped by computer color. Its active squircle tile grows 15% with a white outline. Moving the cursor into the surrounding hide region temporarily hides the map. Server monitors are never streamed.

Connection reload waits three seconds for Clients, restores accepted positions by machine identity, and invokes the existing layout checkmark. Invalid layouts reveal the GUI with the normal validation highlights. Later arrivals require a manual checkmark press. Reload preserves the current Remote Mode choice.

---

## 🚀 Quick Start (Recommended)

1. **Install**: Run the installer on each Windows 10/11 PC from [Releases](https://github.com/parm2006/Conduit/releases/latest).
2. **Start Server**: On your host PC, select **Server (Host)**, enter a shared password, and click **Start Server**.
3. **Connect Clients**: On client PCs, enter the Server IP, port, and password, then click **Connect** (a third Client may replace one of the two connected Clients; unanswered requests close after 15 seconds).
4. **Pair & Arrange**: Approve the pairing code on first connect, arrange the screens so edges touch, and select **Apply**.

---

## ⌨️ Server Hotkeys

Use these on the physical Server keyboard:

| Hotkey | Action |
| --- | --- |
| Hold `Ctrl`, tap `Space` twice | Return roaming cursor to the center of the Server primary display |
| `Ctrl + Alt + Shift + B` | Toggle synchronized background mode |
| `Ctrl + Alt + Shift + R` | Reconnect and restore local control |
| `Ctrl + Alt + Shift + Esc` | Close Conduit across all connected machines |

---

## 🔒 Network & Firewall

Conduit uses TCP ports 28903-28905 and UDP port 28906. The installer creates managed firewall rules restricted to Private networks and the local subnet. Choosing No cancels installation. Uninstall cleanly removes Conduit's firewall rules.

Run only one Server instance per port range. Close the installed Conduit app before testing the source version through `run.bat`, including any copy hidden in background mode. Windows listeners reserve their ports exclusively; a second instance reports a startup error instead of sharing connections with the first.

---

## Code Signing and Privacy

Conduit release binaries and installers are unsigned open-source community builds. They do not include an Authenticode digital signature.

Downloads are published on the [GitHub Releases page](https://github.com/parm2006/Conduit/releases/latest). Always verify downloaded files against the release's [SHA-256 checksums](https://github.com/parm2006/Conduit/releases/latest/download/SHA256SUMS.txt). Conduit's data handling is described in the [privacy policy](PRIVACY.md).

---

## 🛠️ Building & Development

<details>
<summary>Run from Source or Build Release (Click to expand)</summary>

### Run from Source
```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run.py
```

### Build a Release
Install `requirements-release.txt` and NSIS 3.12, then run:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

The build script removes old release outputs before compilation and produces `Conduit.exe`, the installer, and release assets. Pass `-DevelopmentBuild` for an unsigned development build before tagging.

### Test the source branch

Double-click `run.bat` to install the declared requirements and launch the GUI. It works from any current directory. For automated checks from a terminal:

```bat
run.bat --test
run.bat --smoke-test
```

The smoke test briefly opens the real GUI using fake endpoints, checks the toggle and native viewer/map lifecycle, and closes it without pairing or capturing input. Native capture tests require an interactive Windows desktop. Multi-computer verification steps are in `PHYSICAL_TESTING.md`.

</details>

---

## 📄 License & Contributing

Conduit is open source under GPL-3.0-only. Source code is available at https://github.com/parm2006/Conduit. Contributions and security reports: see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
