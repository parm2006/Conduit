# Conduit 6.0.1

Conduit is a Windows wireless KVM for sharing one Server's mouse and keyboard,
the newest clipboard item, and Explorer file pastes with up to two Clients on
the same local network.

[Download the latest release](https://github.com/parm2006/Conduit/releases/latest)

## What it supports

- One Server and up to two Clients, each with multiple Windows displays.
- Mouse and keyboard routing between any touching machine groups.
- Text, HTML, RTF, images, browser formats, files, and folders.
- One global newest clipboard item and an on-paste file relay through the
  Server.
- TLS identity, first-connection pairing approval, and remembered trust.

## Install and connect

1. Install Conduit on every Windows 10 or 11 PC.
2. On the main PC, select **Server (Host)**, enter one shared password, and
   select **Start Server**.
3. On each Client, enter the Server address, port, and shared password, then
   select **Connect**.
4. Compare and approve the pairing code the first time a PC connects.
5. On the Server, arrange the automatically detected machine groups and select
   **Apply**.

Conduit displays each Windows machine name in the layout. A third Client may
replace one of the two connected Clients; an unanswered replacement request
closes after 15 seconds.

## Arrange screens

Each PC's physical monitors remain fixed in their Windows arrangement. Drag the
whole Client group until one full side touches another machine group. Gaps,
overlaps, corners, and partial-side contact are invalid. The previous layout
stays active until Apply succeeds.

If a Client disconnects, Conduit stops roaming input and returns control to the
Server. Reconnect the device, confirm the layout, and use the `✓` control to
reset it.

## Hotkeys

Use these on the physical Server keyboard:

| Hotkey | Action |
| --- | --- |
| Hold `Ctrl`, tap `Space` twice within 750 ms | Return the roaming cursor from any connected screen to the center of the Server's primary display. |
| `Ctrl + Alt + Shift + B` | Toggle synchronized background mode. |
| `Ctrl + Alt + Shift + R` | Reconnect and restore local control. |
| `Ctrl + Alt + Shift + Escape` | Close Conduit on the Server and connected Clients. |

## Network and firewall

The Server uses TCP ports 28903-28905. Its managed firewall rule allows only
the exact Conduit executable on Private networks and from the local subnet. It
never enables Public-network access or changes the Windows network profile.

The installer asks for administrator approval and firewall consent. Choosing
No cancels installation. Uninstall removes Conduit's firewall rule without
changing other applications' rules. Packaged files are unsigned unless the
release owner supplies a trusted certificate, so Windows may warn about an
unfamiliar publisher.

## Run from source

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run.py
```

A source launch can scope firewall access only to `python.exe`. Use the
installer when you want a Conduit-specific rule.

## Build a release

Install `requirements-release.txt` and official NSIS 3.12, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

The script requires a clean, correctly tagged release commit and removes old release outputs before compilation. Pass `-DevelopmentBuild` for an unsigned development build before tagging. It produces `Conduit.exe`, the installer,
corresponding source, notices, provenance, and SHA-256 checksums.

## Contributing and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a change. Report security
issues through [SECURITY.md](SECURITY.md).

Conduit is distributed under GPL-3.0-only. Corresponding source for this
release is available at https://github.com/parm2006/Conduit/tree/v6.0.1.
