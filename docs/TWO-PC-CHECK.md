# DeskFlow two-PC check

Use `28903` as the port on both PCs. DeskFlow also uses `28904` and `28905`
for its other two connection lanes.

## Server PC

Open PowerShell in the DeskFlow folder and run:

```powershell
git pull
.\run.bat
```

In DeskFlow, select **Server**, set the port to `28903`, enter a password,
then choose **Start Server**. If DeskFlow offers firewall setup, choose
**Configure and start** and approve the Windows prompt. If it reports
**Connection blocked**, choose **Repair and start** only after confirming the
executable and ports shown in the consent dialog.

After the server starts, run these in a second PowerShell window:

```powershell
Get-NetTCPConnection -State Listen |
    Where-Object { $_.LocalPort -in 28903,28904,28905 } |
    Select-Object LocalAddress, LocalPort, State, OwningProcess

$rule = Get-NetFirewallRule -DisplayName 'DeskFlow Server - Private LAN' `
    -ErrorAction SilentlyContinue

if ($null -eq $rule) {
    'No DeskFlow firewall rule found. In DeskFlow, choose Configure and start.'
}

$rule |
    Select-Object DisplayName, Enabled, Direction, Action, Profile

$rule |
    Get-NetFirewallPortFilter |
    Select-Object Protocol, LocalPort

$rule |
    Get-NetFirewallApplicationFilter |
    Select-Object Program

$rule |
    Get-NetFirewallAddressFilter |
    Select-Object RemoteAddress
```

Expected: all three ports are listening. The rule is enabled, inbound, allow,
Private, TCP `28903-28905`, scoped to the DeskFlow Python/executable path, and
has remote address `LocalSubnet`.

To inspect relevant enabled block rules without changing anything or probing
another PC, run:

```powershell
$listener = Get-NetTCPConnection -State Listen -LocalPort 28903 |
    Select-Object -First 1
$program = if ($null -ne $listener) {
    (Get-Process -Id $listener.OwningProcess).Path
}

Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True `
    -Direction Inbound -Action Block |
    ForEach-Object {
        $app = $_ | Get-NetFirewallApplicationFilter
        $port = $_ | Get-NetFirewallPortFilter
        if ($app.Program -ieq $program) {
            [PSCustomObject]@{
                DisplayName = $_.DisplayName
                Profile = $_.Profile
                Program = $app.Program
                Protocol = $port.Protocol
                LocalPort = $port.LocalPort
            }
        }
    } | Format-List
```

This command is local and read-only. DeskFlow itself does not ping or probe a
remote PC to decide whether the firewall is configured.

## Client PC

Open PowerShell in the DeskFlow folder and run:

```powershell
git pull
.\run.bat
```

In DeskFlow, select **Client**, enter the Server PC's IPv4 address, use port
`28903`, enter the same password, and select **Connect**. Approve the pairing
code on both PCs.

Before connecting, enter the Server PC's IPv4 address when prompted and run:

```powershell
$serverIp = Read-Host 'Server PC IPv4 address'
28903,28904,28905 | ForEach-Object {
    Test-NetConnection $serverIp -Port $_ |
        Select-Object ComputerName, RemotePort, TcpTestSucceeded
}
```

Expected: every `TcpTestSucceeded` value is `True` while the Server is running.
This is an owner-run TCP acceptance check, not application behavior; DeskFlow
does not run this probe automatically.

## Quick behavior check

1. Move the cursor across the configured edge and back.
2. Copy and paste text in both directions.
3. Copy and paste a small file in both directions.
4. Stop the Server, then rerun the Server check. The listener rows should be
   gone. The firewall rule remains until you remove it or uninstall DeskFlow.
