# Remote cursor-return hotkey fix

**Status:** Approved

**Date:** 2026-08-28

**Planned against:** `70ec81f`

## Problem

Ctrl+Space, Space returns the cursor while it is on the Server, but not while
the Server owns a remote Client cursor. Remote ownership installs
`InputHandler`'s suppressing Windows keyboard hook. That hook forwards the
physical keys and can prevent the independent global listener from observing
them.

## Design

Extract the existing 750 ms, two-distinct-tap state machine into a reusable
detector. Keep it in the global listener for local ownership and also run it
inside the Server keyboard-capture path. The capture path forwards the second
Space press before dispatching recovery on a daemon thread. Router recovery
then releases the forwarded Ctrl/Space state, returns ownership, centers the
cursor, and safely stops capture outside the hook callback.

Reset capture's partial sequence whenever keyboard capture stops. Register the
capture-only callback on the Server; Clients do not gain a roaming-cursor
shortcut. Do not change topology, network messages, or shortcut timing.

## Release and documentation

Ship the correction as Conduit `6.0.1` with Windows file version `6.0.1.0` and
tag `v6.0.1`. Replace the long README with a concise user-first guide covering
installation, three-PC setup, topology, hotkeys, firewall scope, source use,
contributing, and licensing. Document that the physical Server keyboard can
invoke Ctrl+Space, Space while the roaming cursor is on any connected screen.

## Verification

Use deterministic detector tests and a capture-path regression proving the
callback fires only after the second forwarded Space press. Verify capture
stop resets partial state and existing global shortcuts remain green. Run the
complete suite, build from a clean `v6.0.1` tag, and publish the GitHub release
in the same five-asset format as v6.0.
