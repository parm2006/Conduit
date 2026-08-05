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

## 1. Fresh installer consent and UAC checks

Use the built installer on a PC without an existing packaged DeskFlow install.

1. Launch setup and decline the initial Windows UAC prompt. Confirm the wizard
   never opens and nothing is installed.
2. Launch again, approve UAC, then select **No - Cancel installation**. Confirm
   the wizard closes immediately, with no second confirmation and no opportunity
   to select **Yes** in that installer run. Confirm no DeskFlow program
   directory, shortcut, uninstall entry, or DeskFlow firewall rule was created.
3. Repeat, approve UAC, and close the firewall-consent page. Confirm the same
   result.
4. Launch again, approve UAC, select **Yes**, and confirm installation
   completes.

Pass when every refusal leaves no completed install and Yes creates the
packaged application.

## 2. Automatic upgrade and installer firewall repair

Run this on `SERVER_PC` with packaged DeskFlow installed. Open DeskFlow once so
it creates or loads identity and preferences, then close it before recording
state:

```powershell
$setup = Resolve-Path .\dist\DeskFlow-4.3s-Setup.exe
$installDir = Join-Path $env:ProgramFiles 'DeskFlow'
$identityPointer = Join-Path $env:LOCALAPPDATA 'DeskFlow\identity\current.json'
$preferences = Join-Path $env:LOCALAPPDATA 'DeskFlow\preferences.json'

$identityHashBefore = (Get-FileHash $identityPointer).Hash
$preferencesHashBefore = if (Test-Path $preferences) {
    (Get-FileHash $preferences).Hash
} else {
    'ABSENT'
}
$installedHashBefore = (Get-FileHash (Join-Path $installDir 'DeskFlow.exe')).Hash
```

1. Run setup, approve UAC, then select **No**. Confirm the installed executable
   still exists and its hash still equals `$installedHashBefore`.
2. Start the installed DeskFlow application. Run setup again, approve UAC and
   firewall consent. Confirm setup stops and asks you to close DeskFlow without
   removing the installed executable. Close DeskFlow completely.
3. In elevated PowerShell, create one disposable exact-program block:

   ```powershell
   $program = Join-Path $installDir 'DeskFlow.exe'
   New-NetFirewallRule `
       -Name 'DeskFlow-Installer-Validation-Block' `
       -DisplayName 'DeskFlow Installer Validation Block' `
       -Enabled True -Direction Inbound -Action Block `
       -Program $program -Protocol TCP -LocalPort 28903-28905 `
       -Profile Private -RemoteAddress LocalSubnet
   ```

4. Run setup again, approve UAC and select **Yes**. Confirm it automatically
   removes the valid older packaged install, installs the fresh executable,
   and finishes instead of reporting firewall failure.
5. In PowerShell, verify preserved user state and effective repair:

   ```powershell
   (Get-FileHash $identityPointer).Hash -eq $identityHashBefore
   if ($preferencesHashBefore -ne 'ABSENT') {
       (Get-FileHash $preferences).Hash -eq $preferencesHashBefore
   }
   Get-NetFirewallRule -Name 'DeskFlow-Installer-Validation-Block' |
       Format-List DisplayName,Enabled,Direction,Action,Profile
   Get-NetFirewallRule -DisplayName 'DeskFlow Server - Private LAN' |
       Format-List DisplayName,Enabled,Direction,Action,Profile
   ```

Pass when the identity/preference comparisons are `True`, the disposable block
is disabled, and the restricted DeskFlow allow rule is enabled. This verifies
the installer invoked effective repair from the newly packaged executable.
If the pre-install DeskFlow rule targeted `python.exe` from an earlier source
launch, do not remove it manually: the completed install must retarget that
same DeskFlow-owned rule to `C:\Program Files\DeskFlow\DeskFlow.exe`.

## 3. Partial recovery and unknown-content refusal

Run these destructive installation-state checks only on the designated test PC.
They remove the packaged app, but never `%LOCALAPPDATA%\DeskFlow`.

1. Uninstall DeskFlow normally. In elevated PowerShell, create one allowlisted
   partial remnant, then run setup and approve both decisions:

   ```powershell
   $installDir = Join-Path $env:ProgramFiles 'DeskFlow'
   New-Item -ItemType Directory -Force -Path $installDir | Out-Null
   Set-Content -LiteralPath (Join-Path $installDir 'LICENSE') `
       -Value 'partial validation remnant'
   ```

   Pass when setup replaces the remnant and completes.
2. Uninstall again. In elevated PowerShell, plant an unknown file:

   ```powershell
   New-Item -ItemType Directory -Force -Path $installDir | Out-Null
   Set-Content -LiteralPath (Join-Path $installDir 'do-not-delete.txt') `
       -Value 'must survive refused setup'
   ```

   Run setup. Pass when it stops before consent and the file still exists.
   Remove only that validation file afterward.
3. Create an empty subdirectory named `do-not-delete`, run setup, and confirm
   setup again stops without removing it:

   ```powershell
   $unknownDir = Join-Path $installDir 'do-not-delete'
   New-Item -ItemType Directory -Force -Path $unknownDir | Out-Null
   Test-Path $unknownDir
   ```

   Remove only the validation directory afterward, then rerun setup and leave
   DeskFlow installed for the remaining checks.

## 4. Inspect `SERVER_PC` locally

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

## 5. Connect from `CLIENT_PC`

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

## 6. Validate in-app conflict detection and consent

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

## 7. Confirm Public networks remain blocked

Do not reclassify a trusted network merely for this test. If either PC already
has a disposable Public-profile connection, open DeskFlow there and confirm it
shows **Blocked on Public network**, offers no conflict repair, and does not
start Server mode. A consented install may create a Private-only rule for later
use, but it must not add a Public exception.

Record `NOT RUN` when no safe Public-profile environment is available.

## 8. Uninstall and clean up

Uninstall packaged DeskFlow. Pass when files, shortcut, registry metadata, and
the DeskFlow-owned allow rule are removed. If Windows policy prevents firewall
cleanup, the uninstaller may warn but must still remove the application.

Remove only the two disposable validation rules created in Steps 2 and 6:

```powershell
Get-NetFirewallRule -Name 'DeskFlow-Validation-Block' `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

Get-NetFirewallRule -Name 'DeskFlow-Installer-Validation-Block' `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
```

## Results

```text
SERVER_PC commit:
CLIENT_PC commit:

0 Synchronization and automated gate: PASS/FAIL/NOT RUN
1 Fresh installer UAC/No/close/Yes: PASS/FAIL/NOT RUN
2 Automatic upgrade and installer repair: PASS/FAIL/NOT RUN
3 Partial recovery and unknown refusal: PASS/FAIL/NOT RUN
4 Exact rule and listeners: PASS/FAIL/NOT RUN
5 Three lanes and DeskFlow behavior: PASS/FAIL/NOT RUN
6 In-app conflict Cancel/UAC/Repair: PASS/FAIL/NOT RUN
7 Public-profile restriction: PASS/FAIL/NOT RUN
8 Uninstall and cleanup: PASS/FAIL/NOT RUN

First failure:
Expected:
Observed:
Relevant redacted console lines:
```
