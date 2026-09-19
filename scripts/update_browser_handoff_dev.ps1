param([switch]$BuildOnly)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $RepoRoot "venv\Scripts\python.exe"
$ManifestPath = Join-Path $env:LOCALAPPDATA "Conduit\BrowserHandoff\com.conduit.browser_handoff.json"
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Run run.bat once to set up this checkout's virtual environment."
}
if (-not $BuildOnly) {
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "No existing development registration found. Register your extension with scripts\register_browser_handoff_dev.ps1 first."
    }
    $OriginalManifest = Get-Content -LiteralPath $ManifestPath -Raw
    $Manifest = $OriginalManifest | ConvertFrom-Json
    if ($Manifest.name -ne "com.conduit.browser_handoff" -or $Manifest.type -ne "stdio") {
        throw "Refusing to change an unrelated native-host manifest."
    }
    $Origins = @($Manifest.allowed_origins)
    if ($Origins.Count -eq 0 -or @($Origins | Where-Object { $_ -notmatch '^chrome-extension://[a-p]{32}/$' }).Count -ne 0) {
        throw "Existing registration must contain only exact extension origins."
    }
}

# Native-host code is frozen into the EXE: git pull does not update that binary.
# A unique output directory avoids overwriting a host still used by Chrome.
$BuildId = [guid]::NewGuid().ToString("N")
$OutputDirectory = Join-Path $RepoRoot "dist\browser-host-$BuildId"
$WorkDirectory = Join-Path $RepoRoot "build\browser-host-$BuildId"
Push-Location $RepoRoot
try {
    & $PythonPath -m PyInstaller --noconfirm --distpath $OutputDirectory --workpath $WorkDirectory ConduitBrowserHost.spec
    if ($LASTEXITCODE -ne 0) { throw "Native-host build failed; registration was not changed." }
    & (Join-Path $PSScriptRoot "build_browser_extension.ps1")
} finally {
    Pop-Location
}
$NewHost = Join-Path $OutputDirectory "ConduitBrowserHost.exe"
if (-not (Test-Path -LiteralPath $NewHost -PathType Leaf)) { throw "Native-host output is missing." }
Write-Host "Built native host: $NewHost"
if ($BuildOnly) {
    Write-Host "Build-only: no registration or running process was changed."
    exit 0
}

# Preserve the existing extension IDs, browser registry keys and all other
# manifest settings. Keep a recoverable backup and replace only this manifest.
if ((Get-Content -LiteralPath $ManifestPath -Raw) -ne $OriginalManifest) {
    throw "Registration changed during the build; it was left untouched. Run the update again."
}
$BackupPath = "$ManifestPath.$BuildId.backup"
$TemporaryPath = "$ManifestPath.$BuildId.tmp"
Copy-Item -LiteralPath $ManifestPath -Destination $BackupPath
$Manifest.path = $NewHost
[System.IO.File]::WriteAllText($TemporaryPath, ($Manifest | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $TemporaryPath -Destination $ManifestPath -Force
Write-Host "Updated the existing native-host registration; extension IDs were preserved."
Write-Host "Backup: $BackupPath"
Write-Host "Fully restart Conduit from this checkout, then reload the Conduit unpacked extension in chrome://extensions on this PC."
