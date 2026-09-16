param(
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Source = Join-Path $RepoRoot "extension\ConduitBrowserHandoff"
$BuildRoot = Join-Path $RepoRoot "build"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $BuildRoot "browser-handoff-extension"
}
$ZipPath = Join-Path $BuildRoot "ConduitBrowserHandoff-dev.zip"
$ShippingFiles = @(
    "manifest.json",
    "background.js",
    "open_window.js",
    "protocol.js",
    "snapshot.js",
    "url_policy.js",
    "popup.html",
    "popup.js"
)
# This allowlist deliberately excludes tests, the diagnostic probe, and
# node_modules; manifest.json is copied directly at the unpacked-folder root.

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "The ConduitBrowserHandoff source directory is missing."
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
foreach ($Name in $ShippingFiles) {
    $Input = Join-Path $Source $Name
    if (-not (Test-Path -LiteralPath $Input -PathType Leaf)) {
        throw "Required shipping file is missing: $Name"
    }
    Copy-Item -LiteralPath $Input -Destination (Join-Path $OutputDirectory $Name) -Force
}

$Inventory = @(Get-ChildItem -LiteralPath $OutputDirectory -File | ForEach-Object Name | Sort-Object)
if (Compare-Object -ReferenceObject ($ShippingFiles | Sort-Object) -DifferenceObject $Inventory) {
    throw "The developer extension folder contains stale or missing files. Delete only this folder and rebuild."
}
if (-not (Test-Path -LiteralPath $BuildRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $BuildRoot | Out-Null
}
Compress-Archive -Path (Join-Path $OutputDirectory "*") -DestinationPath $ZipPath -Force

Write-Host "Unpacked folder: $OutputDirectory"
Write-Host "Portable ZIP (not for Load unpacked): $ZipPath"
