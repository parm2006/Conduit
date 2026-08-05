# DeskFlow automatic installer upgrades

## Goal

Running a newer DeskFlow installer should replace a valid older packaged
installation without requiring the user to find and launch `Uninstall.exe`.
The upgrade must preserve DeskFlow identity, peer trust, and preferences under
`%LOCALAPPDATA%\DeskFlow`. It must not close a running DeskFlow process,
overwrite unknown files, weaken firewall consent, or embed a stale executable.

This design accepts one tradeoff: after the old uninstaller completes, a
failure in the new installation does not restore the old application. The new
installer still removes its own partial files and rolls back any incomplete
firewall repair.

## Installation states

The fixed install directory remains `C:\Program Files\DeskFlow`.

At startup, the installer classifies that location without changing it:

1. **Empty or absent:** continue as a fresh installation.
2. **Valid DeskFlow installation:** `DeskFlow.exe` and `Uninstall.exe` exist,
   and the DeskFlow uninstall registry entry points to that exact directory.
   Mark the run as an upgrade and continue to the normal consent pages.
3. **Unknown or partial contents:** stop with recovery instructions. Never
   execute an arbitrary uninstaller or overwrite the directory.

Selecting No, closing the consent page, requesting silent installation, or
declining the installer's UAC prompt leaves the old installation untouched.

## Running-process preflight

After firewall consent, but before invoking the old uninstaller, an upgrade
checks whether the exact installed `DeskFlow.exe` is locked:

1. Refuse the upgrade if the reserved temporary name already exists.
2. Rename `DeskFlow.exe` to a fixed temporary name in the same directory.
3. Immediately rename it back.
4. If either rename fails, stop before uninstalling and tell the user to close
   DeskFlow and retry. If the first rename succeeds but restoration fails,
   report the exact recovery path and do not continue.

This check targets one exact Program Files path. It does not enumerate or
terminate processes and cannot affect an unrelated executable with the same
filename.

## Upgrade sequence

For a valid, unlocked installation:

1. Run the exact existing `Uninstall.exe` silently from the fixed install
   directory. The new installer is already elevated, so Windows should not
   require another UAC prompt.
2. Wait for the uninstaller to finish.
3. Require a successful exit and verify that the old installation directory no
   longer contains files. If cleanup is incomplete, stop before copying the new
   version and tell the user what remains.
4. Install the new executable, license, notices, shortcut, uninstaller, and
   registry metadata through the existing transaction.
5. Run the fixed, consented firewall repair as the final fallible step.
6. Mark installation complete only after effective firewall verification.

The old uninstaller removes only packaged files, its shortcut and registry
metadata, and the DeskFlow-owned firewall allow rule. It does not remove
`%LOCALAPPDATA%\DeskFlow`, so identity, pairing, preferences, and user data
survive the upgrade.

If the new install fails after old-version removal, cleanup removes only files
written by the new transaction. Firewall repair retains its existing
exact-object rollback contract. The accepted design does not restore the old
binary.

## UAC behavior

`RequestExecutionLevel admin` remains on the installer. A normal interactive
launch should produce one Windows elevation decision before the wizard runs.
The old uninstaller and firewall helper inherit that elevated installer
context, so Windows normally shows no second UAC prompt. Launching from an
already elevated process may show no prompt.

DeskFlow does not bypass UAC, disable security controls, or accept silent
firewall consent.

## Fresh-build enforcement

The supported release entry point remains:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

The build script will delete prior executable and installer outputs, build and
smoke-test a fresh `dist\DeskFlow.exe`, then invoke NSIS with a private
compile-time define. `installer\DeskFlow.nsi` will fail compilation when that
define is absent. This prevents a direct `makensis` invocation from silently
embedding an older executable after application code changes.

The finished artifacts remain untracked build outputs:

- `dist\DeskFlow.exe` — standalone packaged application;
- `dist\DeskFlow-4.3s-Setup.exe` — installer and automatic upgrader.

Release binaries belong in a GitHub Release or another artifact channel, not
in Git history.

## Error handling

- **Unknown Program Files contents:** stop; never overwrite or execute them.
- **DeskFlow running or executable locked:** leave the old install intact and
  ask the user to close DeskFlow.
- **Temporary rename cannot be restored:** stop and show the temporary and
  expected paths for manual recovery.
- **Old uninstaller fails or leaves files:** stop before new file copy.
- **New file transaction fails:** remove only the new partial installation.
- **Firewall repair fails:** use its internal rollback, remove the new partial
  installation, and report failure.
- **Uninstall firewall cleanup denied:** warn and continue removing the app, as
  already required by the firewall design.

## Verification

Automated tests will statically verify:

- fresh install remains supported;
- a valid exact-path installation enters upgrade mode;
- unknown or partial directories remain blocked;
- consent refusal occurs before old-version mutation;
- the exact executable lock check precedes uninstall;
- the existing uninstaller is the only executable launched for removal;
- old cleanup completes before new files are written;
- Local AppData is never deleted;
- new-install rollback remains ordered correctly;
- direct NSIS compilation without the build define fails;
- the release script removes stale outputs and passes the required define;
- the full test and packaging gates remain green.

Physical Windows validation will cover fresh install, same-version reinstall,
upgrade with DeskFlow closed, upgrade refusal while DeskFlow runs, No/close/UAC
refusal preserving the old version, preferences and pairing preservation,
firewall rule replacement, and uninstall after upgrade.
