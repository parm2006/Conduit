param(
    [string]$MakensisPath,
    [string]$PythonPath,
    [string]$SigningToolPath,
    [string[]]$SigningArguments = @(),
    [switch]$DevelopmentBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DefaultPythonPath = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not $PythonPath) {
    $PythonPath = $DefaultPythonPath
}
$InstallerScript = Join-Path $RepoRoot "installer\Conduit.nsi"
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
    throw "Conduit virtual-environment Python was not found."
}
Push-Location $RepoRoot
try {
    $ProductVersion = (& $PythonPath -c (
        "from app.version import PRODUCT_VERSION; print(PRODUCT_VERSION)"
    )).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ProductVersion) {
        throw "Could not read the canonical Conduit product version."
    }
    $ExpectedTag = "v$ProductVersion"
    $ConduitExecutable = Join-Path $RepoRoot "dist\Conduit-v$ProductVersion.exe"
    $InstallerExecutable = Join-Path $RepoRoot "dist\Conduit-v$ProductVersion-Setup.exe"
    $SourceArchive = Join-Path $RepoRoot "dist\Conduit-v$ProductVersion-source.zip"
    $ReleaseNotices = Join-Path $RepoRoot "dist\THIRD_PARTY_NOTICES.txt"
    $ReleaseManifest = Join-Path $RepoRoot "dist\RELEASE_MANIFEST.txt"
    $Checksums = Join-Path $RepoRoot "dist\SHA256SUMS.txt"

    # git status --porcelain
    $GitStatus = & git -c "safe.directory=$GitSafeDirectory" `
        status --porcelain --untracked-files=all
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the Git worktree."
    }
    if (-not $DevelopmentBuild -and $GitStatus) {
        throw "Release builds require a clean Git worktree."
    }
    $CurrentCommit = (& git -c "safe.directory=$GitSafeDirectory" `
        rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $CurrentCommit) {
        throw "Could not identify the release commit."
    }
    # git tag --points-at HEAD
    $PointingTags = @(& git -c "safe.directory=$GitSafeDirectory" `
        tag --points-at HEAD)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the release tag."
    }
    $CurrentTag = @(
        $PointingTags | Where-Object { $_ -eq $ExpectedTag }
    ) | Select-Object -First 1
    if (-not $DevelopmentBuild -and $CurrentTag -ne $ExpectedTag) {
        throw "Release HEAD must be tagged exactly $ExpectedTag."
    }
    if ($DevelopmentBuild) {
        Write-Warning "Development build: clean-worktree and exact-tag gates are disabled."
    }

    $ResolvedMakensis = Resolve-Makensis
    $NsisLicense = Join-Path (Split-Path $ResolvedMakensis -Parent) "COPYING"
    if (-not (Test-Path -LiteralPath $NsisLicense -PathType Leaf)) {
        throw "The installed NSIS COPYING file was not found."
    }

    # remove stale release outputs
    if (Test-Path -LiteralPath $ConduitExecutable) {
        Remove-Item -LiteralPath $ConduitExecutable -Force
    }
    if (Test-Path -LiteralPath $InstallerExecutable) {
        Remove-Item -LiteralPath $InstallerExecutable -Force
    }
    foreach ($Output in @(
        $SourceArchive,
        $ReleaseNotices,
        $ReleaseManifest,
        $Checksums
    )) {
        if (Test-Path -LiteralPath $Output) {
            Remove-Item -LiteralPath $Output -Force
        }
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
        "build\THIRD_PARTY_NOTICES.txt",
        "--nsis-license",
        $NsisLicense
    )
    $notices = Join-Path $RepoRoot "build\THIRD_PARTY_NOTICES.txt"
    if (
        -not (Test-Path -LiteralPath $notices -PathType Leaf) -or
        (Get-Item -LiteralPath $notices).Length -eq 0
    ) {
        throw "Third-party notices were not generated."
    }
    Copy-Item -LiteralPath $notices -Destination $ReleaseNotices

    # PyInstaller
    Invoke-Checked -Description "Conduit executable build" -FilePath $PythonPath -ArgumentList @(
        "-m", "PyInstaller", "--clean", "--noconfirm", "Conduit.spec"
    )
    if (-not (Test-Path -LiteralPath $ConduitExecutable -PathType Leaf)) {
        throw "Conduit.exe was not produced."
    }

    # --conduit-firewall-helper
    $smoke = Start-Process `
        -FilePath $ConduitExecutable `
        -ArgumentList @(
            "--conduit-firewall-helper",
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

    if ($SigningToolPath) {
        if (-not (Test-Path -LiteralPath $SigningToolPath -PathType Leaf)) {
            throw "The supplied SigningToolPath does not exist."
        }
        Invoke-Checked -Description "Conduit executable signing" -FilePath $SigningToolPath -ArgumentList (
            @($SigningArguments) + @($ConduitExecutable)
        )
    }

    # makensis
    Invoke-Checked -Description "NSIS installer build" -FilePath $ResolvedMakensis -ArgumentList @(
        "/V3", "/DCONDUIT_RELEASE_BUILD=1", $InstallerScript
    )
    if (-not (Test-Path -LiteralPath $InstallerExecutable -PathType Leaf)) {
        throw "The Conduit installer was not produced."
    }

    if ($SigningToolPath) {
        Invoke-Checked -Description "Conduit installer signing" -FilePath $SigningToolPath -ArgumentList (
            @($SigningArguments) + @($InstallerExecutable)
        )
    }

    # git archive
    Invoke-Checked -Description "Corresponding source archive" -FilePath "git" -ArgumentList @(
        "-c",
        "safe.directory=$GitSafeDirectory",
        "archive",
        "--format=zip",
        "--prefix=Conduit-v$ProductVersion/",
        "--output=$SourceArchive",
        $CurrentCommit
    )

    $pyInstallerVersion = & $PythonPath -m PyInstaller "--version"
    $nsisVersion = & $ResolvedMakensis "/VERSION"
    $pythonVersion = & $PythonPath --version
    $dependencies = & $PythonPath -m pip freeze --all
    if ($LASTEXITCODE -ne 0) {
        throw "Could not record the release dependency graph."
    }
    $buildKind = if ($DevelopmentBuild) { "development" } else { "release" }
    @(
        "Conduit release manifest",
        "Version: $ProductVersion",
        "Build kind: $buildKind",
        "Git commit: $CurrentCommit",
        "Expected tag: $ExpectedTag",
        "Observed tag: $CurrentTag",
        "Python: $pythonVersion",
        "PyInstaller: $pyInstallerVersion",
        "NSIS: $nsisVersion",
        "",
        "Installed Python distributions:",
        $dependencies
    ) | Set-Content -LiteralPath $ReleaseManifest -Encoding utf8

    $HashedArtifacts = @(
        $ConduitExecutable,
        $InstallerExecutable,
        $SourceArchive,
        $ReleaseNotices,
        $ReleaseManifest
    )
    $HashLines = foreach ($Artifact in $HashedArtifacts) {
        $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact
        "$($Hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($Artifact))"
    }
    $HashLines | Set-Content -LiteralPath $Checksums -Encoding ascii

    Write-Host "PyInstaller: $pyInstallerVersion"
    Write-Host "NSIS: $nsisVersion"
    Write-Host "Commit: $CurrentCommit"
    Write-Host "Executable: $ConduitExecutable"
    Write-Host "Installer: $InstallerExecutable"
    Write-Host "Source: $SourceArchive"
    Write-Host "Manifest: $ReleaseManifest"
    Write-Host "Checksums: $Checksums"
}
finally {
    Pop-Location
}
