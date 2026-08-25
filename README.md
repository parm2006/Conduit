# Conduit v5.1.1

Conduit is a Windows wireless KVM for sharing a mouse, keyboard, rich
clipboard, and Explorer file pastes between two PCs on the same local network.

## Features

- Mouse roaming across any configured screen edge, with resolution scaling.
- Keyboard forwarding for modifiers, navigation keys, and numpad keys.
- Plain text, HTML, RTF, PNG/DIB, and browser clipboard formats.
- On-demand Explorer file pastes with resumable encrypted staging.
- TLS identity, interactive pairing approval, and session-bound lane tokens.
- Synchronized background mode and connection recovery hotkeys.
- Windows Firewall status, repair, and informed Server-mode setup.

## Use Conduit

1. Run `Conduit.exe` on both Windows 10 or 11 PCs.
2. On the host, choose the client screen edge and select **Start Server**.
3. On the client, enter the host IP, port, and displayed password, then select
   **Connect**.
4. Compare and approve the pairing code on both PCs.

The default Server uses TCP ports 28903-28905. Its managed firewall rule applies
only to the exact Conduit executable, on Private networks, from the local
subnet. It does not enable Public-network access or change the Windows network
profile.

When starting a Server without a matching rule, Conduit offers **Configure
and start**, **Start without setup**, and **Cancel**. Starting without setup
keeps a warning visible because Windows may block other PCs.

## Installer behavior

The installer requires administrator approval and a separate, informed
firewall decision before it copies files. Choosing No cancels installation.
Closing the consent page, declining elevation, using silent mode, or failing
firewall verification also cancels and rolls back the installation.

Running a new installer automatically replaces a complete packaged Conduit
installation after consent. Conduit must be closed first. Installer-owned
partial remnants can be cleaned and replaced, while unknown files or folders
in `C:\Program Files\Conduit` stop setup without being overwritten. Local
identity, peer trust, and preferences under `%LOCALAPPDATA%\Conduit` are
preserved.

The installed rule is restricted to TCP ports 28903-28905, Private networks,
the local subnet, and the installed `Conduit.exe`. Uninstall removes the
Conduit firewall rule before removing the executable. It never removes other
applications' firewall rules.

Local artifacts are unsigned unless the release owner supplies a trusted
signing tool and certificate. Treat such an artifact as an unsigned development build;
Windows may show an unfamiliar-publisher warning.

## Run from source

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run.py
```

If firewall setup is requested from a source launch, Windows can scope the
rule only to `python.exe`, not to this script alone. Conduit shows that
development-mode limitation before requesting elevation. Packaged releases
use a Conduit-specific executable rule.

## Build a Windows release

Install the exact release dependencies and official NSIS 3.12. A public
release build must run from a clean commit tagged for the canonical product
version:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-release.txt
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

For local verification before creating the tag, pass `-DevelopmentBuild`.
That explicit mode records the commit but disables only the clean-worktree and
exact-tag release gates.

The build removes old release outputs before compilation so a failed build
cannot leave a stale executable or installer looking current. It then runs all
tests and whitespace checks, generates dependency notices, builds the one-file
`Conduit.exe`, smoke-tests the restricted firewall helper, and builds the NSIS
installer. It signs the inner executable before installer assembly when signing
is configured. It also produces exact corresponding source, a release manifest,
third-party notices, and SHA-256 checksums. The NSIS source rejects direct
compilation, so a new installer cannot silently embed a stale executable. The
script does not download NSIS or other tools. You can pass `-MakensisPath` or
`-PythonPath` when those tools are not at their default locations.

Optional signing accepts an explicit `-SigningToolPath` and
`-SigningArguments`; the repository contains no certificate or secret.

## License and source

Conduit is distributed under GPL-3.0-only. The installer includes `LICENSE`,
generated third-party notices, and a source link. Corresponding source is
available at https://github.com/parm2006/Conduit/tree/v5.1.1.

The generated notices use the installed distributions' own metadata and
license files and stop the build when required license information is missing.
This release process supports compliance review but is not formal legal
advice.

## Contributing

Bug reports, feature ideas, documentation improvements, tests, and code
contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before
opening an issue or pull request. Report suspected vulnerabilities privately
as described in [SECURITY.md](SECURITY.md).

## Hotkeys

- `Ctrl + Alt + Shift + B`: toggle synchronized background mode.
- `Ctrl + Alt + Shift + R`: reconnect and restore local control.
- `Ctrl + Alt + Shift + Escape`: close Conduit on both connected machines.
