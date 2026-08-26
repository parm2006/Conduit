# Plan 008: Prove the three-PC system and release contracts

> **Executor instructions:** This is an integration and acceptance plan, not an architecture plan. Add system evidence, repair only defects exposed by that evidence, and run every gate. Stop and write a handback if a failure requires a new product or architecture decision.
>
> **Drift check (run first):** `git -c safe.directory=C:/Users/parth/Projects/Conduit diff 3d76acb -- tests/test_multi_client_system.py tests/test_firewall.py tests/test_windows_firewall.py tests/test_ports.py tests/test_release_packaging.py tests/test_security_error_redaction.py app/display_topology.py app/windows_displays.py app/topology_service.py app/topology_editor.py app/topology_toast.py app/session.py app/network.py app/input_router.py app/input_handler.py app/clipboard_hub.py app/clipboard_handler.py app/latest_wins_sender.py app/file_transfer/cluster_router.py app/file_transfer/paste_coordinator.py app/file_transfer/controller.py app/file_transfer/transport.py app/file_transfer/toast.py app/server.py app/client.py app/global_hotkey.py app/gui.py app/preferences.py Conduit.spec README.md CONTRIBUTING.md scripts/build_release.ps1 docs/plans/multi-client-topology/VALIDATION.md docs/plans/multi-client-topology/README.md`
>
> Expected dependency drift: Plans 002–007 add new application modules and tests but should not broaden ports/firewall or release policy. Inspect any changes to the listed release/security paths and stop if they pre-empt this plan without evidence.

## Status

- **Effort:** L
- **Risk:** HIGH
- **Depends on:** `002-land-single-client-topology.md`, `003-land-two-client-sessions.md`, `004-land-graph-input-routing.md`, `005-land-global-clipboard.md`, `006-land-file-relay-and-cluster-commands.md`, `007-land-atomic-apply.md`
- **Planned at:** revision `3d76acb3daa28e5dbc5331af4da93ca427317795`, 2026-08-24

## Why this matters

Unit tests cannot prove that three packaged Windows processes, real TLS sockets, native displays, input injection, clipboard formats, Explorer paste, and firewall rules work together. This plan adds a localhost system seam, packages the new modules, documents the final workflow, and records a physical one-Server/two-Client acceptance matrix. It may fix integration defects, but it must not use acceptance as an excuse to redesign the feature.

## Current state

- `CONTRIBUTING.md` defines the mandatory compileall, full unittest, and whitespace gates.
- `scripts/build_release.ps1` runs those gates before PyInstaller and NSIS, then smoke-tests the packaged firewall helper.
- `README.md` documents one Server/one Client and TCP ports 28903–28905.
- `app/ports.py` reserves exactly three consecutive lanes.
- `app/windows_firewall.py` and `app/firewall.py` enforce executable-specific, Private-network, local-subnet scope.
- `tests/test_security_full_session.py` is the current real-TLS one-session exemplar.
- `tests/test_release_packaging.py` treats release script order, packaged inputs, firewall helper behavior, and documentation as contracts.
- `Conduit.spec` defines the windowed one-file executable and hidden imports/assets.

After dependencies, all feature behavior should already pass focused and full unit tests. This plan proves cross-service and physical claims.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| System/security tests | `.\venv\Scripts\python.exe -m unittest tests.test_multi_client_system tests.test_firewall tests.test_windows_firewall tests.test_ports tests.test_release_packaging tests.test_security_error_redaction -q` | All pass |
| Compile | `.\venv\Scripts\python.exe -m compileall -q app tests run.py` | Exit 0 |
| Full suite | `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Entire suite passes |
| Whitespace | `git -c safe.directory=C:/Users/parth/Projects/Conduit diff --check` | Exit 0 |
| Development package | `powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -DevelopmentBuild` | Executable, installer, source, manifest, notices, and checksums are produced; packaged helper smoke passes |

## Scope

**In scope:**

- `tests/test_multi_client_system.py` (new)
- `tests/test_firewall.py`
- `tests/test_windows_firewall.py`
- `tests/test_ports.py`
- `tests/test_release_packaging.py`
- `tests/test_security_error_redaction.py`
- `app/display_topology.py`
- `app/windows_displays.py`
- `app/topology_service.py`
- `app/topology_editor.py`
- `app/topology_toast.py`
- `app/session.py`
- `app/network.py`
- `app/input_router.py`
- `app/input_handler.py`
- `app/clipboard_hub.py`
- `app/clipboard_handler.py`
- `app/latest_wins_sender.py`
- `app/file_transfer/cluster_router.py`
- `app/file_transfer/paste_coordinator.py`
- `app/file_transfer/controller.py`
- `app/file_transfer/transport.py`
- `app/file_transfer/toast.py`
- `app/server.py`
- `app/client.py`
- `app/global_hotkey.py`
- `app/gui.py`
- `app/preferences.py`
- `Conduit.spec`
- `README.md`
- `CONTRIBUTING.md` if the final manual workflow needs it
- `scripts/build_release.ps1` only if packaging cannot discover the new runtime through existing behavior
- `docs/plans/multi-client-topology/VALIDATION.md` (new)
- `docs/plans/multi-client-topology/README.md`

**Out of scope:**

- New features, more than two Clients, WAN access, direct Client sockets, per-Client passwords, or clipboard history.
- Broad performance rewrites without measured failure.
- Changing firewall scope, port count, release signing policy, or installer consent behavior.
- Treating manual observations as a substitute for reproducible automated tests when a deterministic seam exists.

## Steps

### Step 1: Build a real localhost three-process/session system test

Add `tests/test_multi_client_system.py` using real localhost TLS listeners and two complete simulated Client bundles. Reuse identity/protector setup from `tests/test_security_full_session.py`; avoid mocking the network path under test.

Cover:

- both Clients authenticate/bind all lanes on the same three listeners;
- a third candidate reject and deterministic 15-second timeout;
- automatic draft placement, valid Apply distribution, acknowledgement, and rollback;
- Server→Client and logical Client1→Client2 input messages with isolated disconnect;
- three-source clipboard races and late-Client newest sync;
- Client1→Client2 file manifest/frame relay while ordinary copies continue;
- cluster reload, shutdown, and background commands;
- no test leaves listeners, threads, sockets, temporary identity, or staging resources alive.

Use fake native input/display/clipboard/file adapters at their real boundaries; keep actual TLS/session/lane code real.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_multi_client_system -q` → all system cases pass with no resource warnings or hanging threads.

### Step 2: Prove the unchanged firewall and port boundary

Extend port/firewall tests to assert that two Clients share the existing base, base+1, and base+2 listeners. Inspect and repair still means exactly one executable-specific allow rule, Private profiles only, local subnet only, TCP only, and the three-port range. A second Client must require no new rule.

Add a negative source scan that fails if production adds a fourth listener or a broad firewall range. Preserve current development-mode warning that source launches can scope only to `python.exe`.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_firewall tests.test_windows_firewall tests.test_ports -q` → all scope and no-fourth-port assertions pass.

### Step 3: Audit privacy-safe diagnostics under three-PC failures

Exercise wrong password, pairing decline, replacement timeout, stale acknowledgement, invalid topology, clipboard rejection, and file cancellation. Logs may contain stable machine/session/revision/job/phase identifiers and safe error categories. They must omit passwords, tokens, clipboard/file contents, private paths, certificates, and user-facing development IPs.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_security_error_redaction tests.test_multi_client_system -q` → all redaction assertions pass.

### Step 4: Package the new runtime without broadening release policy

Run PyInstaller discovery against the new modules. Change `Conduit.spec` only when imports or Windows resources are genuinely missing from the executable. Preserve one windowed executable, current icon/assets, firewall helper dispatch, source archive, notices, signing hooks, NSIS consent, and exact product version behavior.

Update packaging tests before changing the spec or build script. Do not add downloads or machine-specific paths.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q` → package/release contract tests pass.

### Step 5: Update user and validation documentation

Update `README.md` from the one-Client edge-selector workflow to:

- start one Server with its Windows machine name and shared password;
- connect up to two Clients with the same password and approve each physical device once;
- let physical displays appear automatically;
- arrange Client groups in the Server grid and select Apply;
- explain the two-Client replacement prompt and 15-second timeout;
- describe global newest clipboard and on-paste file relay;
- retain exact ports/firewall, Windows 10/11, and LAN-only limitations.

Create `VALIDATION.md` with sanitized rows for environment, topology, action, expected outcome, observed outcome, evidence, and status. Do not include IPs, credentials, pairing data, private filenames, or clipboard content.

**Verify:** `.\venv\Scripts\python.exe -m unittest tests.test_release_packaging -q` → documentation contract tests pass.

### Step 6: Run automated and development-package gates

Run system/security tests, compileall, the full suite, and whitespace check. Then run the development package command from a suitable worktree with NSIS available. Launch the packaged firewall-helper smoke path and one packaged app instance; confirm startup display discovery does not crash without a peer.

If build outputs change tracked or user files unexpectedly, stop. Do not clean unknown files with broad deletion.

**Verify:** every command in “Commands you will need” succeeds. Record artifact names and commit in `VALIDATION.md`, not full logs.

### Step 7: Execute the physical Windows matrix

Use one Server and two physical Client PCs. Record each result in `VALIDATION.md`:

- one and two Client connection, duplicate/initial colors, third-candidate reject/timeout/replacement;
- real single/multi-monitor groups with mixed DPI, negative coordinates, rotation, and HDMI/DisplayPort;
- valid/invalid drafts, cursor-away Cancel, successful Apply, failed acknowledgement rollback, reconnect, and display unplug before Apply;
- Server↔Client and Client1↔Client2 cursor transitions, equal perceived speed, local Client mouse isolation, and release of held modifiers/buttons;
- copy from every PC using text, HTML/RTF, rapid screenshots, and repeat-identical content;
- Explorer paste for every source/destination pair, newer copy during transfer, cancel, and endpoint disconnect;
- reload, shutdown, background mode, Server-only warnings, Client-primary identification toasts, and source/destination-only transfer toasts;
- one-Server/one-Client regression path.

For any failure, add or tighten an automated test through the nearest real seam before the narrow fix. Repeat the failed row and all related rows.

**Verify:** every required matrix row is PASS, or the plan remains incomplete with a clear BLOCKED row and evidence.

## Test plan

- One real-TLS system file covers orchestration without real hardware.
- Existing focused suites remain the regression net for each service.
- Firewall/ports/package tests pin unchanged security and release boundaries.
- The physical matrix proves native Windows effects that fakes cannot: display discovery, input injection, clipboard formats, Explorer paste, and packaged GUI/toasts.

## Done criteria

- [x] System/security focused tests pass without leaks or hangs.
- [x] Compileall, full suite, and whitespace checks pass.
- [x] Development package build and packaged helper smoke pass.
- [x] Firewall remains exact three-port, Private, local-subnet, executable-specific TCP scope.
- [x] README describes the final workflow without development-only IPs.
- [ ] Every required physical matrix row passes on one Server and two Clients.
- [ ] One-Client regression path passes.
- [ ] No unresolved BLOCKED validation row remains.
- [ ] No file outside scope changed.

## STOP conditions

Stop and write a handback if:

- any fix requires a fourth port, broader firewall, direct Client socket, or weaker authentication;
- the packaged app needs a new dependency not approved in the accepted design;
- a physical failure reveals an unresolved product/architecture choice rather than an implementation defect;
- release scripts would need to download tools, bypass consent, or weaken clean/tag/signing gates;
- sensitive real-machine data appears in tests, logs, screenshots, or validation docs;
- NSIS or required physical machines are unavailable: mark the exact gate pending rather than claiming completion;
- verification fails twice after a narrow fix or scope expands materially.

## Maintenance notes

Keep `VALIDATION.md` as the acceptance record for this feature. Future changes to session capacity, display discovery, routing, clipboard order, file jobs, ports, or firewall scope should identify which matrix rows and system tests they must rerun. Do not treat a packaged build alone as physical three-PC acceptance.
