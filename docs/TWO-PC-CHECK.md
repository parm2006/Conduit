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
**Configure and start** and approve the Windows prompt.

After the server starts, run these in a second PowerShell window:

```powershell
Get-NetTCPConnection -State Listen |
    Where-Object { $_.LocalPort -in 28903,28904,28905 } |
    Select-Object LocalAddress, LocalPort, State, OwningProcess

Get-NetFirewallRule -Name 'DeskFlow Server - Private LAN' |
    Select-Object DisplayName, Enabled, Direction, Action, Profile

Get-NetFirewallRule -Name 'DeskFlow Server - Private LAN' |
    Get-NetFirewallPortFilter |
    Select-Object Protocol, LocalPort

Get-NetFirewallRule -Name 'DeskFlow Server - Private LAN' |
    Get-NetFirewallApplicationFilter |
    Select-Object Program

Get-NetFirewallRule -Name 'DeskFlow Server - Private LAN' |
    Get-NetFirewallAddressFilter |
    Select-Object RemoteAddress
```

Expected: all three ports are listening. The rule is enabled, inbound, allow,
Private, TCP `28903-28905`, scoped to the DeskFlow Python/executable path, and
has remote address `LocalSubnet`.

## Client PC

Open PowerShell in the DeskFlow folder and run:

```powershell
git pull
.\run.bat
```

In DeskFlow, select **Client**, enter the Server PC's IPv4 address, use port
`28903`, enter the same password, and select **Connect**. Approve the pairing
code on both PCs.

Before connecting, replace the address below with the Server PC's IPv4
address and run:

```powershell
$serverIp = '192.168.86.87'
28903,28904,28905 | ForEach-Object {
    Test-NetConnection $serverIp -Port $_ |
        Select-Object ComputerName, RemotePort, TcpTestSucceeded
}
```

Expected: every `TcpTestSucceeded` value is `True` while the Server is running.

## Quick behavior check

1. Move the cursor across the configured edge and back.
2. Copy and paste text in both directions.
3. Copy and paste a small file in both directions.
4. Stop the Server, then rerun the Server check. The listener rows should be
   gone. The firewall rule remains until you remove it or uninstall DeskFlow.
