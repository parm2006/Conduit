# Memo: Rule-origin metadata is absent from the selected COM API

**Verdict**: Detect every overlapping block through `INetFwPolicy2`, offer the
same consented Repair action, and treat clean post-repair reinspection as the
only success authority. Do not predict local versus managed repairability in
the read-only GUI.
**Informs**: Plans 005, 006, and 007 (amended)
**Written at**: revision `75e29dc`, 2026-08-04

## The fork

Plan 005 assumed each rule returned by `HNetCfg.FwPolicy2.Rules` exposed its
policy-store source. That would have let read-only inspection label local
conflicts repairable and managed conflicts help-only. During the first
test-driven implementation cycle, review of the documented COM interface
showed that this assumption was false.

The desired outcome remains unchanged: detect a block that overrides
DeskFlow's exact allow rule, obtain consent and UAC approval, modify only exact
conflicts, and never start Server mode until effective reinspection succeeds.

## Evidence

- `app/windows_firewall.py:20-28` uses `HNetCfg.FwPolicy2` and
  `HNetCfg.FWRule`; changing to another policy API would add a second Windows
  firewall boundary.
- Microsoft's documented
  [`INetFwRule` interface](https://learn.microsoft.com/en-us/windows/win32/api/netfw/nn-netfw-inetfwrule)
  exposes matching and mutation properties but no policy-store origin.
- Microsoft's documented
  [`INetFwRule2` interface](https://learn.microsoft.com/en-us/windows/win32/api/netfw/nn-netfw-inetfwrule2)
  adds edge-traversal options, not source metadata.
- `PolicyStoreSourceType` exists on the separate
  [`MSFT_NetFirewallRule` CIM class](https://learn.microsoft.com/en-us/windows/win32/fwp/wmi/wfascimprov/msft-netfirewallrule)
  and is populated for ActiveStore queries that trace source.
- `app/windows_firewall.py:130-168` already follows the safe pattern needed for
  the revised decision: mutate through a restricted backend, then reinspect
  and reject success when the effective state is not acceptable.

## Recommended shape

- Plan 005 reports one generic conflict state with a safe count. It does not
  carry a local/managed prediction or policy origin into the GUI.
- Plan 006's elevated transaction collects exact conflicting rules, snapshots
  them, and attempts removal. If Windows refuses any removal, it restores
  prior mutations and returns failure.
- Plan 007 offers Repair for the generic conflict after explicit consent and
  UAC. A failed helper or remaining block produces an administrator-help
  message and never starts Server mode.
- Successful clean reinspection is the authority. No helper exit code alone
  can make the GUI report Ready or start the server.
- Keep the current COM boundary. Do not add PowerShell, `netsh`, WMI/CIM, a
  network probe, Public access, or profile reclassification.

## Rejected alternatives

- **Add a second CIM/WMI provider solely for origin metadata**: rejected
  because it adds a second rule model and association/query boundary without
  improving final authority; post-mutation effective reinspection is still
  required.
- **Infer origin from display name or GUID format**: rejected because those
  strings are not a documented authorization boundary and can be localized or
  user-controlled.
- **Hide Repair unless origin is known**: rejected because the selected API
  never supplies origin, returning users to manual administrative commands.
