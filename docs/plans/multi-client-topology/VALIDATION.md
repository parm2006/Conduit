# Two-Client Multi-Monitor Validation

This is the sanitized acceptance record for the multi-client topology release.
Do not add network addresses, passwords, pairing data, private paths, real
clipboard contents, or private filenames.

## Automated evidence

| Environment | Topology | Action | Expected outcome | Observed outcome | Evidence | Status |
|---|---|---|---|---|---|---|
| Windows development runtime | Server + two simulated Clients | Bind control, clipboard-data, and file lanes through real TLS listeners | Both session bundles share exactly three listeners and remain isolated | Both bundles authenticated and bound; timed-out third candidate left both active | `tests/test_multi_client_system.py` | PASS |
| Windows development runtime | Server between two simulated Clients | Distribute and commit one topology version | Both exact participants acknowledge before persistence and activation | Candidate committed and both current sessions became routable | `tests/test_multi_client_system.py` | PASS |
| Windows development runtime | Three simulated clipboard sources | Submit rapid ordinary clipboard items | Server receive order selects one newest item without blocking new copies | Revision increased monotonically and endpoints converged on the newest item | `tests/test_multi_client_system.py`, `tests/test_clipboard_hub.py` | PASS |
| Windows development runtime | Client source to Client destination | Request manifest and relay an encrypted file frame | Server targets only the two involved sessions without staging the payload | Manifest handshake and frame crossed real control/file TLS lanes | `tests/test_multi_client_system.py`, `tests/test_cluster_file_routing.py` | PASS |
| Windows development runtime | Server + two simulated Clients | Broadcast reload, shutdown, and background commands | Both ready sessions receive every best-effort cluster command | All three command types reached both sessions | `tests/test_multi_client_system.py` | PASS |
| Source and packaging contract | Two-Client Server | Inspect ports and firewall policy | Exactly three TCP ports; executable-specific, Private, local-subnet rule | Automated scope checks pass | `tests/test_ports.py`, `tests/test_firewall.py`, `tests/test_windows_firewall.py` | PASS |
| Windows display monitor | Server and Client display inventories | Change a detected physical-monitor group while routing is active | Draft updates and Server warns; active graph remains unchanged until Apply | Polling monitor retries discovery failures, sends authenticated change inventory, and updates only draft state | `tests/test_windows_displays.py`, `tests/test_topology_protocol.py`, `tests/test_gui_connection_lifecycle.py`, `tests/test_topology_toast.py` | PASS |

## Development package evidence

| Environment | Topology | Action | Expected outcome | Observed outcome | Evidence | Status |
|---|---|---|---|---|---|---|
| Local development checkout at `c895dcc` plus feature changes | Server startup without a peer | Build development package and run packaged helper/startup smoke | Executable, installer, source, manifest, notices, checksums; startup discovery remains stable | Build completed with 717 tests, packaged restricted-firewall helper smoke, NSIS assembly, and a four-second packaged startup/display-monitor smoke; standalone EXE SHA-256 is `EC4C38174466A4F5FA60B806F8A1F885E509B28AE675F57CE8CD252D334A751D` | `Conduit-v5.1.1.exe`, `Conduit-v5.1.1-Setup.exe`, `Conduit-v5.1.1-source.zip`, `RELEASE_MANIFEST.txt`, `SHA256SUMS.txt` | PASS |

These are development-only artifacts from a dirty feature checkout. The source
archive records committed `c895dcc`, while the executable also contains the
local feature changes; therefore this artifact set must not be published as a
public release. A public build still requires a clean, tagged commit so binary
and corresponding source are exact.

## Physical Windows matrix

| Environment | Topology | Action | Expected outcome | Observed outcome | Evidence | Status |
|---|---|---|---|---|---|---|
| One Server + two physical Clients | One Client, then two Clients | Connect, pair once, reconnect | Windows names/colors are stable; reconnect restores draft placement but requires Apply | Not run yet | User observation | PENDING |
| One Server + two physical Clients | Two active Clients + third candidate | Reject, allow timeout, then replace each slot | Purple candidate closes after 15 seconds or inherits chosen slot color only after Apply | Not run yet | User observation | PENDING |
| Mixed physical displays | Single/multiple monitors, mixed DPI, negative coordinates, rotation | Connect and rescan HDMI/DisplayPort layouts | Each PC appears as one accurate, immovable physical-monitor group | Not run yet | User observation | PENDING |
| Mixed physical displays | Valid and invalid drafts | Try gaps, overlap, corner/partial contact, cursor-away Cancel, Apply, failed acknowledgement | Invalid drafts stay inactive/red; Cancel and failure preserve old routing; success activates atomically | Not run yet | User observation | PENDING |
| One Server + two physical Clients | Server↔Client and Client↔Client edges | Cross every configured full edge with held modifiers/buttons and compare speed | One Server-owned cursor transitions directly, scales consistently, and releases held input safely | Not run yet | User observation | PENDING |
| One Server + two physical Clients | Local Client mouse while Server cursor is elsewhere | Move/copy locally | Local pointer remains independent; clipboard still updates globally | Not run yet | User observation | PENDING |
| One Server + two physical Clients | Copies originating on every PC | Copy text, HTML/RTF, rapid screenshots, and identical content | Newest Server-received item becomes pasteable everywhere without blocking capture | Not run yet | User observation | PENDING |
| One Server + two physical Clients | Client 1↔Client 2 basic Explorer paste | Paste a file and folder in both Client directions using `run.bat` | Each destination receives the selected file/folder through the Server relay | User confirmed both Client directions work perfectly on `614823c` | User observation, 2026-08-28 | PASS |
| One Server + two physical Clients | Every directed source/destination pair | Paste Explorer files/folders, copy during transfer, cancel, disconnect endpoint | Bytes start on paste, only source/destination show status, cancellation is scoped and safe | Not run yet | User observation | PENDING |
| One Server + two physical Clients | Cluster commands and toasts | Reload, shutdown, background; inspect topology/transfer toasts | Commands affect all machines; identification toast stays on Client primary until Apply/Cancel | Not run yet | User observation | PENDING |
| One Server + one physical Client | Regression topology | Repeat connect, Apply, cursor, clipboard, file paste, commands | Existing one-Client workflow remains functional | Not run yet | User observation | PENDING |

Plan 008 remains incomplete until the development package row and every
physical row are PASS or a concrete defect/blocker is recorded.
