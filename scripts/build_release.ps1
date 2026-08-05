param(
    [string]$MakensisPath,
    [string]$SigningToolPath,
    [string[]]$SigningArguments = @()
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $RepoRoot "venv\Scripts\python.exe"
$PyInstallerPath = Join-Path $RepoRoot "venv\Scripts\pyinstaller.exe"
$DeskFlowExecutable = Join-Path $RepoRoot "dist\DeskFlow.exe"
$InstallerScript = Join-Path $RepoRoot "installer\DeskFlow.nsi"
$InstallerExecutable = Join-Path $RepoRoot "dist\DeskFlow-4.3s-Setup.exe"
$GitSafeDirectory = $RepoRoot -replace "\\", "/"

function Invoke-Checked {
    param(
        [string]$Description,
        [string]$FilePath,
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Resolve-Makensis {
    if ($MakensisPath) {
        if (-not (Test-Path -LiteralPath $MakensisPath -PathType Leaf)) {
            throw "The supplied MakensisPath does not exist."
        }
        return (Resolve-Path -LiteralPath $MakensisPath).Path
    }

    $command = Get-Command "makensis.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"),
        (Join-Path $env:ProgramFiles "NSIS\makensis.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }

    throw (
        "makensis.exe was not found. Install official NSIS first or pass " +
        "-MakensisPath. This script does not download build tools."
    )
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "DeskFlow virtual-environment Python was not found."
}
if (-not (Test-Path -LiteralPath $PyInstallerPath -PathType Leaf)) {
    throw "PyInstaller was not found in the DeskFlow virtual environment."
}

Push-Location $RepoRoot
try {
    # remove stale release outputs
    if (Test-Path -LiteralPath $DeskFlowExecutable) {
        Remove-Item -LiteralPath $DeskFlowExecutable -Force
    }
    if (Test-Path -LiteralPath $InstallerExecutable) {
        Remove-Item -LiteralPath $InstallerExecutable -Force
    }

    # compileall
    Invoke-Checked -Description "Python compilation" -FilePath $PythonPath -ArgumentList @(
        "-m", "compileall", "-q", "app", "tests", "run.py"
    )

    # unittest
    Invoke-Checked -Description "Automated test suite" -FilePath $PythonPath -ArgumentList @(
        "-m", "unittest", "discover", "-s", "tests", "-q"
    )

    # git diff --check
    Invoke-Checked -Description "Git whitespace check" -FilePath "git" -ArgumentList @(
        "-c", "safe.directory=$GitSafeDirectory", "diff", "--check"
    )

    # generate_third_party_notices.py
    Invoke-Checked -Description "Third-party notice generation" -FilePath $PythonPath -ArgumentList @(
        "scripts\generate_third_party_notices.py",
        "--output",
        "build\THIRD_PARTY_NOTICES.txt"
    )
    $notices = Join-Path $RepoRoot "build\THIRD_PARTY_NOTICES.txt"
    if (
        -not (Test-Path -LiteralPath $notices -PathType Leaf) -or
        (Get-Item -LiteralPath $notices).Length -eq 0
    ) {
        throw "Third-party notices were not generated."
    }

    # pyinstaller.exe
    Invoke-Checked -Description "DeskFlow executable build" -FilePath $PyInstallerPath -ArgumentList @(
        "--clean", "--noconfirm", "DeskFlow.spec"
    )
    if (-not (Test-Path -LiteralPath $DeskFlowExecutable -PathType Leaf)) {
        throw "DeskFlow.exe was not produced."
    }

    # --deskflow-firewall-helper
    $smoke = Start-Process `
        -FilePath $DeskFlowExecutable `
        -ArgumentList @(
            "--deskflow-firewall-helper",
            "inspect",
            "--base-port",
            "28903"
        ) `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($smoke.ExitCode -notin @(0, 3, 4)) {
        throw "Packaged helper exit code $($smoke.ExitCode) is invalid."
    }

    # makensis
    $ResolvedMakensis = Resolve-Makensis
    Invoke-Checked -Description "NSIS installer build" -FilePath $ResolvedMakensis -ArgumentList @(
        "/V3", "/DDESKFLOW_RELEASE_BUILD=1", $InstallerScript
    )
    if (-not (Test-Path -LiteralPath $InstallerExecutable -PathType Leaf)) {
        throw "The DeskFlow installer was not produced."
    }

    if ($SigningToolPath) {
        if (
            -not (Test-Path -LiteralPath $SigningToolPath -PathType Leaf)
        ) {
            throw "The supplied SigningToolPath does not exist."
        }
        Invoke-Checked -Description "DeskFlow executable signing" -FilePath $SigningToolPath -ArgumentList (
            @($SigningArguments) + @($DeskFlowExecutable)
        )
        Invoke-Checked -Description "DeskFlow installer signing" -FilePath $SigningToolPath -ArgumentList (
            @($SigningArguments) + @($InstallerExecutable)
        )
    }

    $pyInstallerVersion = & $PyInstallerPath "--version"
    $nsisVersion = & $ResolvedMakensis "/VERSION"
    Write-Host "PyInstaller: $pyInstallerVersion"
    Write-Host "NSIS: $nsisVersion"
    Write-Host "Executable: $DeskFlowExecutable"
    Write-Host "Installer: $InstallerExecutable"
}
finally {
    Pop-Location
}
