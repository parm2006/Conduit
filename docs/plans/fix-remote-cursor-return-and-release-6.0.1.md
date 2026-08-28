# Fix remote cursor return and release Conduit 6.0.1

> **Drift check:** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 70ec81f -- app/global_hotkey.py app/input_handler.py app/server.py tests/test_global_hotkey.py tests/test_emergency_release.py app/version.py installer/Conduit.nsi tests/test_release_packaging.py tests/test_gui_preferences.py README.md RELEASE_NOTES_6.0.1.md`

## Status

- **Effort:** M
- **Risk:** MED
- **Depends on:** Conduit 6.0
- **Planned at:** `70ec81f`, 2026-08-28
- **Design:** `docs/superpowers/specs/2026-08-28-remote-cursor-return-hotkey-fix-design.md`

## Steps

### 1. Prove the missing capture path

Add a failing test that drives Ctrl+Space, Space through
`InputHandler._on_key_press/_on_key_release`, asserts both taps are forwarded,
and expects one asynchronous recovery callback. Add a reset-on-capture-stop
case.

### 2. Share detection with Server capture

Extract the deterministic detector from `GlobalHotkeyMonitor`, preserving all
existing timing and cancellation tests. Give `InputHandler` a capture-only
return callback and register it from `ConduitServer`. Dispatch outside the
hook callback and reset the detector when capture stops.

### 3. Prepare 6.0.1 and shorten the README

Update metadata tests first, then product/installer metadata and release notes.
Rewrite README.md as a concise user guide and document the corrected shortcut.

### 4. Verify, publish, and package

Run focused tests, compileall, all tests, and scoped whitespace checks. Commit
and push main, tag `v6.0.1`, build the official six-file artifact set in a
clean worktree, and publish the same five GitHub assets used by v6.0.

## Done criteria

- [ ] Physical Server Ctrl+Space, Space is detected through remote capture.
- [ ] Forwarded keys release and cursor recovery remain ordered.
- [ ] README is concise and documents all hotkeys.
- [ ] Product version is 6.0.1 and official artifacts pass checksums.
- [ ] GitHub release v6.0.1 is public and latest.

## STOP conditions

Stop if the fix requires Client protocol changes, suppresses ordinary
Ctrl+Space use, runs recovery synchronously inside the Windows hook callback,
or the focused/full/release gates fail twice after a narrow correction.
