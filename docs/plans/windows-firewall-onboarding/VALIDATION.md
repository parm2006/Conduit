# DeskFlow Windows Firewall acceptance

Run this checklist on two Windows PCs connected to the same trusted Private
LAN. Call the intended host `SERVER_PC` and the other machine `CLIENT_PC`.
Do not disable Windows Firewall, antivirus, UAC, or network isolation.

Stop at the first failure. Record the exact step, DeskFlow status, and redacted
console output. Do not publish passwords, usernames, Wi-Fi names, or public IP
addresses.

## 0. Synchronize both PCs

Run on `SERVER_PC` and `CLIENT_PC`:

```powershell
git fetch origin
git switch firewallfix
git pull --ff-only
git rev-parse HEAD
git status --short

.\venv\Scripts\python.exe -m compileall -q app tests run.py
.\venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

Pass when both PCs print the same commit, both worktrees are clean, and the
suite ends with `OK`.

## 1. Clean installer refusal checks

Use the built installer on a PC without an existing packaged DeskFlow install.

1. Select **No - Cancel installation**. Confirm no DeskFlow program directory,
   shortcut, uninstall entry, or DeskFlow firewall rule was created.
2. Repeat and close the consent page. Confirm the same result.
3. Select **Yes**, then decline UAC. Confirm installation does not complete.
4. Select **Yes** and approve UAC. Confirm installation completes.

Pass when every refusal leaves no completed install and Yes creates the
packaged application.

## 2. Inspect `SERVER_PC` locally

Start the server on port `28903`, then run:

```powershell
Get-NetTCPConnection -State Listen |
    Where-Object LocalPort -in 28903,28904,28905 |
    Format-Table LocalAddress,LocalPort,State,OwningProcess

$listener = Get-NetTCPConnection -State Listen -LocalPort 28903 |
    Select-Object -First 1
$program = (Get-Process -Id $listener.OwningProcess).Path

$rule = Get-NetFirewallRule -DisplayName 'DeskFlow Server - Private LAN'
$rule | Format-List DisplayName,Enabled,Direction,Action,Profile
$rule | Get-NetFirewallPortFilter | Format-List Protocol,LocalPort
$rule | Get-NetFirewallAddressFilter | Format-List RemoteAddress
$rule | Get-NetFirewallApplicationFilter | Format-List Program
```

Pass when all three ports listen and the rule is enabled, inbound, allow, TCP
`28903-28905`, Private, `LocalSubnet`, and scoped to `$program`.

## 3. Connect from `CLIENT_PC`

On `CLIENT_PC`, enter the server's IPv4 address when prompted:

```powershell
$serverIp = Read-Host 'SERVER_PC IPv4 address'
28903,28904,28905 | ForEach-Object {
    Test-NetConnection $serverIp -Port $_ |
        Select-Object RemoteAddress,RemotePort,TcpTestSucceeded
}
```

Then connect through DeskFlow and approve pairing. Pass when all three TCP
checks succeed and mouse, keyboard, text clipboard, and a small file paste work
in both directions. DeskFlow itself does not run this reachability probe.

## 4. Validate conflict detection and consent

On `SERVER_PC`, close DeskFlow. Open an elevated PowerShell window and create
one disposable block for the exact test executable:

```powershell
$program = (Get-NetFirewallRule -DisplayName 'DeskFlow Server - Private LAN' |
    Get-NetFirewallApplicationFilter).Program

New-NetFirewallRule `
    -Name 'DeskFlow-Validation-Block' `
    -DisplayName 'DeskFlow Validation Block' `
    -Enabled True -Direction Inbound -Action Block `
    -Program $program -Protocol TCP -LocalPort 28903-28905 `
    -Profile Private -RemoteAddress LocalSubnet
```

1. Open DeskFlow. Confirm it reports **Connection blocked** without contacting
   `CLIENT_PC`.
2. Select Start Server, then **Cancel**. Confirm the server does not start and
   the validation block remains enabled.
3. Retry **Repair and start**, then decline UAC. Confirm the same result.
4. Retry, approve the explanation and UAC, and confirm Server starts.
5. Inspect the validation rule:

   ```powershell
   Get-NetFirewallRule -Name 'DeskFlow-Validation-Block' |
       Format-List DisplayName,Enabled,Direction,Action,Profile
   ```

Pass when Repair disables only the disposable block, preserves the restricted
DeskFlow allow rule, and the full three-lane session works afterward.

## 5. Confirm Public networks remain blocked

Do not reclassify a trusted network merely for this test. If either PC already
has a disposable Public-profile connection, open DeskFlow there and confirm it
shows **Blocked on Public network**, offers no conflict repair, and does not
start Server mode. A consented install may create a Private-only rule for later
use, but it must not add a Public exception.

Record `NOT RUN` when no safe Public-profile environment is available.

## 6. Uninstall and clean up

Uninstall packaged DeskFlow. Pass when files, shortcut, registry metadata, and
the DeskFlow-owned allow rule are removed. If Windows policy prevents firewall
cleanup, the uninstaller may warn but must still remove the application.

Remove only the disposable validation rule created in Step 4:

```powershell
Get-NetFirewallRule -Name 'DeskFlow-Validation-Block' `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
```

## Results

```text
SERVER_PC commit:
CLIENT_PC commit:

0 Synchronization and automated gate: PASS/FAIL/NOT RUN
1 Installer No/close/UAC/Yes: PASS/FAIL/NOT RUN
2 Exact rule and listeners: PASS/FAIL/NOT RUN
3 Three lanes and DeskFlow behavior: PASS/FAIL/NOT RUN
4 Conflict Cancel/UAC/Repair: PASS/FAIL/NOT RUN
5 Public-profile restriction: PASS/FAIL/NOT RUN
6 Uninstall and cleanup: PASS/FAIL/NOT RUN

First failure:
Expected:
Observed:
Relevant redacted console lines:
```
