# Conduit v6.0.1

Conduit v6.0.1 fixes the Server cursor-return shortcut introduced in v6.0.

## Fixes

- Ctrl+Space, Space now works from the physical Server keyboard while the
  roaming cursor is on either Client, not only while it is on a Server screen.
- Recovery runs outside the suppressing Windows keyboard-hook callback, then
  releases forwarded keys and returns the cursor to the Server's primary
  display center.
- Partial shortcut sequences are cleared whenever remote keyboard capture
  stops.
- The README is shorter and documents setup, topology, hotkeys, firewall
  scope, and source use in a user-first order.

## Installation

Install `Conduit-v6.0.1-Setup.exe` on the Server and every Client. All machines
must run the same Conduit version.

## Source and licensing

- License: GPL-3.0-only
- Exact corresponding source: https://github.com/parm2006/Conduit/tree/v6.0.1
- Third-party licenses: `THIRD_PARTY_NOTICES.txt`
- Artifact hashes: `SHA256SUMS.txt`
- Build provenance: `RELEASE_MANIFEST.txt`
