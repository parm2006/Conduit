# DeskFlow Windows Firewall and Installer Design

Date: 2026-07-28  
Branch baseline: `firewallfix` at `bafacdb`  
Tracking issue: [#10](https://github.com/parm2006/DeskFlow/issues/10)  
Status: approved for implementation

## Goal

Make DeskFlow Server work on a normal Windows private network without asking
users to diagnose silent inbound firewall blocks. Ship a packaged
`DeskFlow.exe`, an installer-managed secure firewall default, and an in-app
status and repair path.

The feature must preserve Windows Firewall's default inbound protection.
DeskFlow will request informed consent, open only the three configured TCP
ports for the exact packaged executable, accept traffic only from the local
subnet on Private networks, and remove its rule during uninstall.

## User experience

The Server tab will include a compact firewall status below the port field:

- **Ready** — the expected rule exists and matches the executable and ports.
- **Setup required** — no DeskFlow rule exists.
- **Repair required** — a DeskFlow rule exists but its executable, ports,
  profile, protocol, direction, action, or remote scope is stale.
- **Development rule** — the rule targets `python.exe` because DeskFlow is
  running from source.
- **Managed by administrator** — Windows policy rejected a requested change.
- **Unavailable** — firewall state could not be read safely.

The adjacent action will read **Configure**, **Repair**, or **View help** as
appropriate. Ready state needs no action.

When the user starts Server mode with a missing or stale rule, DeskFlow will
show one intentional consent dialog:

> Allow DeskFlow Server on private local networks?
>
> Windows will allow this DeskFlow executable to receive TCP connections on
> ports 5000-5002 from devices on your local network. Public networks remain
> blocked.

The choices will be **Configure and start**, **Start without setup**, and
**Cancel**. The first choice requests UAC, refreshes firewall status, and starts
the server only after the rule matches. The second starts the listener and
keeps a visible warning; it never claims remote connections will work.

Source mode will add a warning before elevation:

> This development build runs through Python. Windows can restrict the rule to
> this Python executable, but not to the DeskFlow script alone. Packaged
> releases use a DeskFlow-specific rule.

The Client tab will not request inbound firewall access.

## Release artifacts

The repository will add three release inputs:

- `DeskFlow.spec` — a checked-in PyInstaller specification for one packaged
  `DeskFlow.exe`, the application icon, required assets, and Windows/pywin32
  hidden imports.
- `installer/DeskFlow.nsi` — an NSIS definition that installs the
  packaged executable and assets, requires explicit firewall consent, and
  removes the DeskFlow rule during uninstall.
- `scripts/build_release.ps1` — a deterministic entry point that runs the
  automated gate, builds the executable with the repository virtual
  environment, validates the artifact, and invokes NSIS when `makensis.exe`
  is available.

NSIS is selected because its official zlib/libpng-based licensing explicitly
permits commercial use. PyInstaller's documented GPL exception permits
shipping bundled applications under the application's own license, subject to
the licenses of bundled dependencies. The release checklist will record the
versions and official license links used for each build.

Before copying files, the installer will show a dedicated consent page:

> Allow DeskFlow Server on private local networks (TCP ports 5000-5002).

**Yes** continues installation. **No**, closing the consent page, or declining
the installer's required elevation cancels installation. Cancellation leaves
no installed files, shortcuts, startup entries, or DeskFlow firewall rules.
The installer already runs elevated after consent, so it can call the installed
executable's restricted firewall helper without another elevation prompt.

The checked-in build supports an optional signing command when a signing
certificate is supplied. This work cannot produce a trusted signature without
the owner's certificate; unsigned local builds must identify themselves as
development builds.

## Distribution compliance

DeskFlow's repository license is GPL-3.0. The installer will display and
install the repository `LICENSE`, identify the public source location, and
keep the corresponding release source available beside each distributed
binary release. The release output will include a generated
`THIRD_PARTY_NOTICES.txt` based on the versions and installed package metadata
used for the build; the build must stop rather than invent a license when
metadata is missing.

PyInstaller's documented GPL exception permits shipping bundled applications
under the application's license, subject to bundled dependency licenses. NSIS
is licensed for any use, including commercial applications. The build uses
unmodified released build tools and will retain any notices required by the
dependencies actually included in the package.

This work does not claim formal legal review. It creates an auditable license
inventory, carries the project's GPL terms and source link, and avoids the
known commercial-license expectation attached to the rejected Inno Setup
alternative.

## Firewall rule contract

DeskFlow owns one stable inbound rule with a stable internal name and group.
Its display description will identify the current port range and installation
path. The rule must match all of these conditions:

| Property | Required value |
|---|---|
| Enabled | true |
| Direction | inbound |
| Action | allow |
| Protocol | TCP |
| Local ports | configured base port through base port + 2 |
| Application | normalized absolute path of the running executable |
| Profiles | Private only |
| Remote addresses | LocalSubnet |
| Edge traversal | blocked |

The base port must be an integer from 1 through 65533. DeskFlow will derive the
three-port range; no caller may supply an arbitrary range.

The display name does not authorize a process. Application path, ports,
profile, and remote scope enforce the boundary. A packaged build uses
`DeskFlow.exe`; a source build necessarily uses its current `python.exe`.

DeskFlow will never:

- disable Windows Firewall;
- change the current network from Public to Private;
- add a Public-profile exception;
- add an unrestricted port-only rule in a packaged build;
- accept an arbitrary executable path or shell command from the helper CLI;
- remove rules outside its stable internal rule identity.

## Components

### Rule model

`app/firewall.py` will contain platform-independent types:

- `FirewallRuleSpec` — executable path and validated base-port-derived values;
- `FirewallState` — ready, missing, stale, development, managed, unavailable;
- `FirewallInspection` — state plus safe user-facing reason;
- `FirewallBackend` — inspect, install/replace, and remove operations.

This module will not import GUI or Windows COM packages. Tests will exercise
rule comparison and state transitions with real model objects.

### Windows backend

`app/windows_firewall.py` will implement `FirewallBackend` with Windows
Firewall's `HNetCfg.FwPolicy2` and `HNetCfg.FWRule` COM interfaces through
pywin32. It will construct COM properties directly rather than compose a
PowerShell or `netsh` command.

Inspection does not require elevation. Mutation requires elevation. COM errors
will be mapped to a small safe result set; raw policy, account, registry, or
installation details remain in debug logs only.

Replacing a stale rule will remove only the stable DeskFlow-owned rule, then
add the complete expected rule. If creation fails after removal, the operation
returns failure and the UI remains **Setup required**; it must not report a
partially configured state.

### Elevated helper

`app/firewall_helper.py` will expose only these internal operations:

- `install --base-port <integer>`
- `remove`
- `inspect --base-port <integer>`

The helper derives its executable path from the running process. It does not
accept a program path, profile, remote address, protocol, rule name, or command
string from the caller.

`run.py` will dispatch the reserved helper arguments before starting the GUI.
The GUI will elevate the same executable with Windows `runas` and wait for its
exit status through a small injected runner. Packaged mode therefore elevates
`DeskFlow.exe`; source mode elevates the current Python executable with the
fixed DeskFlow entry point and validated helper arguments.

UAC cancellation is a normal declined outcome. It creates no rule, does not
start Server mode when **Configure and start** was selected, and returns focus
to the main window.

### GUI coordinator

`app/firewall_onboarding.py` will translate backend inspection into display
state and coordinate consent, elevation, refresh, and start continuation. The
GUI will depend on this coordinator rather than on COM or subprocess details.

The coordinator will refresh:

- when the Server tab is first shown;
- after the base port changes and validates;
- after configuration or repair finishes;
- after returning from installer or external firewall settings.

Changing the base port makes the previous rule stale. DeskFlow will not mutate
the rule while the user types. Repair occurs only after an explicit action or
the **Configure and start** choice.

## Installer lifecycle

Installation order:

1. Show the mandatory firewall consent page and its exact scope.
2. Cancel without system changes if the user chooses **No**, closes the page,
   or declines required elevation.
3. Copy the packaged executable and required files.
4. Run installed `DeskFlow.exe` in firewall-helper install mode with the
   default base port.
5. Verify the resulting rule against the complete rule contract.
6. If rule creation or verification fails, show a safe explanation and roll
   back the installation and any partially created DeskFlow rule.

Uninstall order:

1. Run installed `DeskFlow.exe` in firewall-helper remove mode while the file
   still exists and the uninstaller is elevated.
2. Continue uninstall even if policy already removed or blocked the rule.
3. Delete application files.

Upgrade replaces the old application files and repairs the stable rule to the
new executable path. It does not accumulate versioned firewall rules.

## Failure handling

- **UAC cancelled:** show `Firewall setup was cancelled. DeskFlow Server was
  not started.` when configuration was required for that start action.
- **Policy-managed machine:** show `Windows policy did not allow DeskFlow to
  change the firewall. Ask your administrator to allow this app on the private
  local network.`
- **Public network:** leave the rule Private-only and show `DeskFlow firewall
  access is disabled on this Public network. Change it only if this is a
  trusted home or work network.`
- **Inspection unavailable:** allow Server mode with a warning and provide
  manual help; do not guess that the firewall is ready.
- **Installer consent declined:** cancel installation before copying files or
  changing the firewall.
- **Installer rule failure:** return a nonzero helper result, remove any
  partially created DeskFlow rule, and roll back the installation.
- **Port conflict:** preserve the existing server-start error. Firewall repair
  does not imply that the port is available to bind.

Normal logs may record state names and operation results. They must not record
usernames, full private paths, firewall policy dumps, or command lines.

## Testing

No automated test may alter the developer's live firewall. Unit tests will use
fake COM rules, fake backends, and injected elevation runners.

Test coverage will include:

- valid and invalid base ports, including the 65533 upper bound;
- exact three-port range derivation;
- case-insensitive normalized executable comparison;
- every required firewall property;
- missing, ready, stale, development, managed, and unavailable states;
- stale executable, port, profile, remote scope, protocol, action, direction,
  and enabled flag;
- helper argument rejection for arbitrary paths, commands, profiles, and port
  ranges;
- installer **No**, close, and elevation-decline paths leaving no installed
  files or firewall state;
- UAC success, cancellation, failure, and refresh;
- Start Server continuation only after successful matching configuration;
- Start without setup warning behavior;
- port edits producing stale state without automatic mutation;
- installer consent, rollback, uninstall cleanup, and stable rule identity;
- PyInstaller import and asset smoke checks.
- installed GPL license, source link, and generated third-party notice
  completeness.

Verification will run:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

Release validation will additionally build `DeskFlow.exe`, launch its helper in
inspect mode without changing state, and build the installer when NSIS is
present.

The physical two-PC check will:

1. remove any prior DeskFlow rule;
2. install with firewall consent;
3. confirm the rule is executable-, port-, Private-, and LocalSubnet-scoped;
4. confirm desktop-to-laptop TCP access on all three ports;
5. confirm normal pairing and all three secure lanes;
6. change the base port and repair the stale rule;
7. confirm Public-profile access remains disabled;
8. uninstall and confirm the DeskFlow-owned rule is gone.

## Acceptance criteria

The feature is complete when:

- a fresh packaged installation requires informed firewall consent;
- choosing **No**, closing consent, or declining required installer elevation
  cancels without leaving installed files or DeskFlow firewall state;
- a consenting user can accept a client connection without manual firewall
  commands;
- the installed rule matches every property in the rule contract;
- missing and stale rules are visible and repairable from Server mode;
- declined in-app elevation changes nothing and gives a clear next action;
- source mode clearly identifies its broader `python.exe` scope;
- port changes never silently broaden access;
- uninstall removes only DeskFlow-owned firewall state;
- Public networks remain blocked;
- release artifacts include the GPL license, source location, and
  third-party notices derived from the packaged dependency versions;
- the complete automated suite and physical two-PC validation pass.
