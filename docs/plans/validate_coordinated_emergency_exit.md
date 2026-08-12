# Validate: DeskFlow 5.1 coordinated emergency exit

## Build under test

- Executable: `dist\DeskFlow-v5.1.exe`
- Size: 18,470,190 bytes
- SHA-256:
  `E3AD3BFE6CE86C416BD729353169FD63C6FE6351FCFEF3B4A5D9C8CE95FB237A`
- Installer: `dist\DeskFlow-v5.1-Setup.exe`
- Installer size: 18,207,533 bytes
- Installer SHA-256:
  `08DD660B8D0F99AA8618995B413C25FCC557B87EF6CC35528245CB23FD42F42F`
- Automated gate: 20 focused tests and all 548 tests passed. Python
  compilation, whitespace validation, PyInstaller, the packaged firewall
  helper smoke test, and NSIS passed.

Use the exact same `DeskFlow-v5.1.exe` on both machines. Close every older
DeskFlow process first. The laptop does not need the repository or `run.bat`.

Verify the executable on both machines:

```powershell
Get-FileHash .\DeskFlow-v5.1.exe -Algorithm SHA256
```

The result must match the executable hash above.

## Expected behavior

`Ctrl+Shift+Alt+Esc` has one purpose: close DeskFlow on both machines in the
active connection. Both DeskFlow windows must disappear, the server must stop,
and neither machine should retain a DeskFlow process. If there is no connected
peer, the hotkey closes the local DeskFlow application.

## Validation matrix

| # | Test | Result | Notes |
|---|---|---|---|
| 1 | Start DeskFlow on both machines, make the development PC the Server, connect the laptop as Client, and press `Ctrl+Shift+Alt+Esc` on the Server. Both DeskFlow apps close promptly. | | |
| 2 | Restart both apps with the same roles and press the hotkey physically on the Client laptop. Both DeskFlow apps close promptly. | | |
| 3 | Restart both apps, hide one GUI with `Ctrl+Shift+Alt+B`, then press the emergency hotkey on the visible machine. Both the visible and hidden DeskFlow instances close. | | |
| 4 | Start DeskFlow on only one machine without connecting. Press the emergency hotkey. The local app closes. | | |
| 5 | After rows 1–4, restart both apps, connect normally, cross control in both directions, and transfer one small harmless file. Everything works without repair or reset. | | |

Use `PASS` or `FAIL`. For a failure, report the row and which of these remained:
the local window, remote window, local process, remote process, or listening
server. Do not send passwords, pairing codes, clipboard contents, file paths,
or file bytes.

## Stop conditions

Stop and report immediately if:

- only one app closes while the connection was healthy;
- either app freezes instead of closing;
- input remains captured after one app closes;
- DeskFlow appears closed but remains in Task Manager; or
- restarting requires firewall repair, preference deletion, or a machine
  reboot.

Do not begin another feature until these results are reconciled.
