# Conduit 6.0

Conduit 6.0 expands one Server and one Client into one Server with up to two
simultaneous Client PCs. Each machine may use multiple Windows displays, and
the Server arranges the detected machine groups in a compact topology editor.

## Highlights

- Connect two Client PCs to one Server through the existing three-port TLS
  transport.
- Detect each machine's physical displays and preserve its Windows monitor
  arrangement as one movable topology group.
- Route the Server-controlled mouse and keyboard across Server-to-Client,
  Client-to-Server, and Client-to-Client boundaries.
- Stop routing safely when a Client disconnects and require an authoritative
  topology reset before roaming resumes.
- Share the newest clipboard item among all three machines.
- Relay Explorer file and folder pastes between either Client through the
  Server without permanent Server storage.
- Return the roaming cursor to the Server's primary-display center by holding
  Ctrl and tapping Space twice within 750 milliseconds.
- Use compact `✓` and `✕` topology controls with Apply/Reset and Cancel hover
  descriptions.

## Installation

Install `Conduit-v6.0-Setup.exe` on the Server and every Client. All machines
must run Conduit 6.0 for the multi-client topology and routing protocol.

The Server still uses one shared password and TCP ports 28903–28905 on private
local networks. The installer updates the executable-specific Windows Firewall
rule after explicit consent.

## Source and licensing

- License: GPL-3.0-only
- Exact corresponding source: https://github.com/parm2006/Conduit/tree/v6.0
- Third-party licenses: `THIRD_PARTY_NOTICES.txt`
- Artifact hashes: `SHA256SUMS.txt`
- Build provenance: `RELEASE_MANIFEST.txt`
