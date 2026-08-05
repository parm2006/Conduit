# DeskFlow Effective Firewall Inspection and Repair Design

Date: 2026-08-04
Branch baseline: `firewallfix` at `727aae2`
Extends: `2026-07-28-windows-firewall-installer-design.md`
Status: approved for specification

## Goal

Make the Server tab report whether Windows Firewall will permit DeskFlow's
three inbound TCP lanes, not merely whether DeskFlow's named allow rule looks
correct. Detect explicit block rules that override the allow rule and provide
an informed, elevated repair path. Perform all checks against local Windows
Firewall policy; do not ping, probe, or contact another computer.

The feature keeps the existing security boundary: TCP ports 28903-28905 by
default, the exact running executable, Private networks only, and
`LocalSubnet` only. DeskFlow must not disable Windows Firewall, enable Public
access, or turn a Public network into a Private network.

## Security model

Windows Firewall decides whether an inbound connection may reach the running
process. It can match the executable, protocol, ports, network profile, and
remote address. It cannot prove that the peer is DeskFlow. After Windows
admits the TCP connection, DeskFlow's TLS, password, pairing, trusted identity,
and session-lane authentication decide whether the peer may use the app.

The rule therefore admits local-subnet TCP candidates to the exact DeskFlow
process on its three ports. DeskFlow rejects candidates that fail application
authentication. Public-network traffic remains outside the firewall rule.

## User experience

The Server tab retains its current firewall row and adds two effective-policy
states:

- **Connection blocked** — an enabled inbound block rule overlaps the running
  executable, Private profile, TCP, and at least one DeskFlow port. The action
  is **Repair**. Read-only inspection does not guess whether Windows policy
  will permit modification; verified post-repair inspection is authoritative.
- **Blocked on Public network** — the machine has no active Private profile.
  The action is **View help**. DeskFlow will not change the network category or
  add a Public exception.

Existing states remain: Ready, Setup required, Repair required, Development
rule, Managed by administrator, and Unavailable. **Ready** and **Development
rule** mean both that the named allow rule matches and that no detected block
rule overrides it on an active Private profile.

When a conflict exists and the user starts Server mode, DeskFlow shows:

> Windows is blocking incoming DeskFlow connections.
>
> Repair will disable the conflicting TCP block for this executable and restore
> DeskFlow's restricted Private-network rule for ports 28903-28905. Public
> networks remain blocked.

The choices are **Repair and start** and **Cancel**. Cancel changes nothing and
does not start the server. DeskFlow does not offer **Start without setup** for
a confirmed block because that path is known not to accept connections.

Source mode adds this warning:

> This development build runs through Python. Disabling the conflicting rule
> affects this Python executable, which other Python applications may share.
> Packaged DeskFlow releases use a DeskFlow-specific executable.

The user must accept the dialog and approve UAC. Declining either step changes
nothing and does not start the server.

## Effective inspection

`app/firewall.py` will add states for a conflicting block and a Public-only
active network. The platform-independent model will represent the small set of
firewall fields needed to evaluate conflicts without exposing COM objects to
the GUI.

`app/windows_firewall.py` will inspect the active Windows Firewall policy in
this order:

1. Read and compare the stable DeskFlow allow rule against the existing exact
   rule contract.
2. Read the active profile mask. If no Private profile is active, report the
   Public-network state after retaining any more serious managed/unavailable
   error.
3. Enumerate enabled inbound block rules from the active policy store.
4. Identify rules whose application path equals the running executable after
   Windows path normalization.
5. Treat TCP and protocol `Any` as potentially overlapping TCP.
6. Parse comma-separated ports and inclusive ranges. Treat blank, `*`, or
   `Any` as all ports. A candidate conflicts when it contains any of the three
   derived DeskFlow ports.
7. Require the rule's profile mask to include Private or all profiles.
8. Require its remote scope to include `LocalSubnet`, `*`, `Any`, or another
   scope that cannot safely be proven disjoint from local-subnet clients.

Inspection is conservative. If a matching block rule has an unreadable port,
profile, or remote-address expression, DeskFlow reports **Unavailable** rather
than claiming Ready. It never tests reachability over the network.

The inspection result will include only safe metadata needed for behavior:
state, reason code, and the number of conflicts. Normal GUI text and logs will
not expose rule GUIDs, usernames, private paths, or full policy dumps.

## Repair attempt rules

A conflicting rule is eligible for the consented repair attempt only when all
conditions hold:

- it is enabled, inbound, and block;
- it targets the exact running executable;
- it applies to TCP or all protocols;
- it overlaps DeskFlow's three ports;
- it applies to the Private profile; and
- it is represented by the exact COM rule object enumerated for the repair.

The selected COM rule interface does not expose policy-store origin. DeskFlow
therefore does not predict whether Windows will permit disabling. If Group
Policy, MDM, or another managed source prevents the change, the elevated
operation fails or the conflict remains; DeskFlow restores prior mutations,
does not start Server mode, and directs the user to their administrator.

For packaged `DeskFlow.exe`, explicit consent authorizes disabling all local
conflicts satisfying this contract. For source-mode Python, the same repair is
available only after the additional shared-executable warning. This is never a
silent background action.

## Transactional elevated repair

The restricted helper adds one fixed operation:

```text
repair --base-port <integer>
```

As with existing helper operations, the caller cannot supply an executable
path, rule name, profile, address, protocol, or command string. The helper
derives the executable from its own Windows process image and derives all
three ports from the validated base port.

The elevated backend will:

1. Reinspect and collect exact conflicts to prevent a stale GUI decision from
   authorizing different rules.
2. Retain the exact enumerated COM objects and their enabled state for rollback.
3. Disable only those exact objects; never delete a block by friendly name.
4. Preserve an already-correct DeskFlow allow rule, or create the stable
   restricted rule when it is missing.
5. Reinspect the complete effective state.
6. Return success only for Ready or Development rule with no remaining
   conflict.
7. If any step fails, remove any newly created DeskFlow allow rule and re-enable
   every block disabled by the transaction. Report failure if restoration is not
   complete.

This rollback preserves the user's previous protection if DeskFlow cannot
finish the repair. UAC cancellation occurs before mutation.

## GUI coordination

`app/firewall_onboarding.py` will translate the new states into compact labels,
explanations, and actions. Configuration for missing/stale rules continues to
use the existing install helper operation. Confirmed conflicts use the repair
operation and conflict-specific consent text.

After the helper exits, the coordinator reinspects local policy. It invokes
the Start Server continuation only when the result is Ready or Development
rule. A helper success code without a matching reinspection is failure.

The Client tab remains unchanged because clients initiate outbound
connections and do not need the inbound rule.

## Public networks

DeskFlow does not open inbound access on a Public profile. On café, airport,
hotel, or other untrusted Wi-Fi, the GUI explains that Server mode is blocked
by design. It does not offer to reclassify the network or broaden the rule.

Users who need DeskFlow away from home should use a trusted private LAN, a
private phone hotspot, or a separately secured private network whose Windows
profile and routing meet DeskFlow's existing local-network assumptions. The
firewall feature does not configure those networks.

## Failure handling

- **No active Private profile:** show Blocked on Public network; do not mutate.
- **Repair consent declined:** leave all rules unchanged and do not start.
- **UAC declined:** leave all rules unchanged and do not start.
- **Policy-rejected conflict:** keep the conflict visible, show administrator
  help, and do not start Server mode.
- **Unreadable policy or expression:** show Unavailable rather than Ready.
- **Repair verification failed:** roll back disabled blocks and partial
  DeskFlow state; do not start.
- **Rollback incomplete:** report a safe failure and direct the user to
  Windows Firewall settings; never claim the firewall is ready.
- **Port changed during repair:** the completion path reinspects the current
  requested base port and refuses to start on a stale result.

## Testing

No automated test may read or change the developer's live firewall. Tests use
fake COM rules, fake policy stores, and injected elevation runners.

Test-first coverage will include:

- exact executable matching with case and path normalization;
- TCP, Any-protocol, Any-port, single-port, list, and range overlap;
- no conflict for UDP-only, disabled, outbound, nonoverlapping port, different
  executable, or non-Private rule;
- conservative failure for malformed matching rule expressions;
- exact conflict reported without guessing its policy-store origin;
- policy-rejected repair reported safely after attempted mutation;
- Ready/Development suppressed when an overriding block exists;
- Public-only active profile reported without adding a Public rule;
- conflict consent, cancellation, UAC decline, success, and failed
  reinspection;
- source-mode shared-Python warning;
- helper rejection of arbitrary paths, rule identities, operations, and port
  ranges;
- exact conflict disabling with unrelated rules preserved;
- exact-object restoration after disable, allow-install, or verification failure;
- Start Server continuation only after clean effective reinspection;
- static checks proving no ping, socket probe, PowerShell, `netsh`, firewall
  disablement, network reclassification, or Public exception was added.

Verification commands:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

Physical acceptance uses one Server and one Client. It confirms all three
ports, a complete secure session, cursor switching, and clipboard behavior.
A green GUI label or one reachable TCP port is not sufficient acceptance.

## Acceptance criteria

- Inspection uses only local Windows Firewall policy and performs no network
  probe.
- Ready means the exact allow rule exists, a Private profile is active, and no
  detected explicit block overrides the three DeskFlow TCP ports.
- A conflict produces clear consent and a UAC-gated repair action.
- Cancel or UAC decline leaves firewall state unchanged and does not start the
  server.
- Repair disables only exact conflicts for the running executable and
  overlapping DeskFlow ports that Windows permits the local API to modify.
- Managed rules and unrelated rules are never disabled or removed.
- Failed repair re-enables disabled block rules and does not claim success.
- Source mode warns that Python may be shared.
- Public networks remain blocked and cannot be enabled from this flow.
- Successful reinspection is required before Server mode starts.
- Automated tests, compilation, whitespace checks, and the controlled two-PC
  acceptance pass.
