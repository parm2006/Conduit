# Conduit 6.0.1

A fast Windows wireless KVM for sharing your mouse, keyboard, newest clipboard item, and on-paste file relay across one Server and up to two Clients on the same local network.

[![Download Latest Release](https://img.shields.io/github/v/release/parm2006/Conduit?label=Download%20Latest%20Release)](https://github.com/parm2006/Conduit/releases/latest)

---

## ✨ Features

- **Multi-PC Control**: Seamless cursor and keyboard routing across one Server and up to two Clients with multi-monitor support.
- **Shared Clipboard & Files**: Syncs the global newest clipboard item (text, images, HTML/RTF) and provides an on-paste file relay for files and folders.
- **Visual Display Arrangement**: Displays each Windows machine name in the layout; drag and snap screen edges, then click **Apply**.
- **Secure by Default**: Authenticated pairing, TLS identity, and private network firewall rules.

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

Conduit uses TCP ports 28903-28905. The installer creates a managed firewall rule restricted to Private networks and the local subnet. Choosing No cancels installation. Uninstall cleanly removes Conduit's firewall rule.

---

## Code Signing and Privacy

Conduit is applying to the SignPath Foundation open-source program to use SignPath.io for future official release signing. The current Conduit 6.0.1 installer and earlier public binaries are unsigned; do not interpret the application as an existing signature. If the application is approved and the integration becomes operational, signed releases will be identified in their release notes and covered by the [code-signing policy](CODE_SIGNING_POLICY.md).

Downloads are published on the [GitHub Releases page](https://github.com/parm2006/Conduit/releases/latest). Verify downloaded files against the release's [SHA-256 checksums](https://github.com/parm2006/Conduit/releases/latest/download/SHA256SUMS.txt). Conduit's data handling is described in the [privacy policy](PRIVACY.md).

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

</details>

---

## 📄 License & Contributing

Conduit is open source under GPL-3.0-only. Source code is available at https://github.com/parm2006/Conduit. Contributions and security reports: see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
