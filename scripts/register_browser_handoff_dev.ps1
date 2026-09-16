param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-p]{32}$")]
    [string]$ExtensionId,
    [ValidateSet("Chrome", "Edge", "All")]
    [string]$Browser = "All",
    [ValidateSet("Register", "Unregister")]
    [string]$Mode = "Register",
    [string]$HostPath
)

$ErrorActionPreference = "Stop"
$HostName = "com.conduit.browser_handoff"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $HostPath) {
    $HostPath = Join-Path $RepoRoot "dist\ConduitBrowserHost.exe"
}
$StateDirectory = Join-Path $env:LOCALAPPDATA "Conduit\BrowserHandoff"
$ManifestPath = Join-Path $StateDirectory "$HostName.json"
$Origins = @("chrome-extension://$ExtensionId/")
$Roots = switch ($Browser) {
    "Chrome" { @("HKCU:\Software\Google\Chrome\NativeMessagingHosts") }
    "Edge" { @("HKCU:\Software\Microsoft\Edge\NativeMessagingHosts") }
    "All" { @(
        "HKCU:\Software\Google\Chrome\NativeMessagingHosts",
        "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts"
    ) }
}

if ($Mode -eq "Register") {
    if (-not (Test-Path -LiteralPath $HostPath -PathType Leaf)) {
        throw "Dedicated host executable not found: $HostPath. Build ConduitBrowserHost.spec first."
    }
    New-Item -ItemType Directory -Force -Path $StateDirectory | Out-Null
    @{
        name = $HostName
        description = "Conduit local browser-handoff host (development)"
        path = (Resolve-Path -LiteralPath $HostPath).Path
        type = "stdio"
        allowed_origins = $Origins
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    foreach ($Root in $Roots) {
        $Key = Join-Path $Root $HostName
        New-Item -Path $Key -Force | Out-Null
        Set-ItemProperty -LiteralPath $Key -Name "(default)" -Value $ManifestPath
    }
    Write-Host "Registered development host for $Browser using the exact extension origin $Origins[0]."
    Write-Host "Manifest: $ManifestPath"
    exit 0
}

foreach ($Root in $Roots) {
    $Key = Join-Path $Root $HostName
    if (Test-Path -LiteralPath $Key) {
        $Current = (Get-ItemProperty -LiteralPath $Key -Name "(default)")."(default)"
        if ($Current -eq $ManifestPath) {
            Remove-Item -LiteralPath $Key -Force
        } else {
            Write-Warning "Left an unowned native-host registration untouched: $Key"
        }
    }
}
if (Test-Path -LiteralPath $ManifestPath) {
    Remove-Item -LiteralPath $ManifestPath -Force
}
Write-Host "Removed this user's Conduit development host registration for $Browser."
