param(
    [ValidateSet("Baseline", "Decoder", "Sender", "Receiver", "Full")]
    [string]$Mode = "Baseline",
    [switch]$Build,
    [string]$PythonPath,
    [string]$LogDirectory
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $LogDirectory) {
    $LogDirectory = Join-Path $RepoRoot "build\video-stage1"
}
$LogDirectory = [System.IO.Path]::GetFullPath($LogDirectory)
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

if ($Build) {
    & (Join-Path $PSScriptRoot "build_streamer.ps1")
    if (-not $?) { throw "Stage 1 native build failed." }
}

if ($PythonPath) {
    $PythonExe = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $PythonCandidates = @(
        "C:\Users\parth\Projects\Conduit\venv\Scripts\python.exe",
        (Join-Path $RepoRoot "venv\Scripts\python.exe")
    )
    $PythonExe = $PythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $PythonExe) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCommand) { throw "Python was not found. Pass -PythonPath with an interpreter that has Conduit's dependencies." }
        $PythonExe = $PythonCommand.Source
    }
}

$Dll = Join-Path $RepoRoot "app\bin\conduit_streamer.dll"
if (-not (Test-Path -LiteralPath $Dll -PathType Leaf)) {
    throw "Native DLL is missing. Run again with -Build after installing MSVC Build Tools."
}

$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$Prefix = Join-Path $LogDirectory ("{0}-{1}" -f $Mode.ToLowerInvariant(), $RunStamp)
$StdoutLog = $Prefix + ".stdout.log"
$StderrLog = $Prefix + ".stderr.log"
$PreviousMode = [Environment]::GetEnvironmentVariable("CONDUIT_VIDEO_STAGE1", "Process")
$Process = $null
try {
    [Environment]::SetEnvironmentVariable("CONDUIT_VIDEO_STAGE1", $Mode.ToLowerInvariant(), "Process")
    Write-Host "Starting isolated video test mode: $Mode"
    Write-Host "Source: $RepoRoot"
    Write-Host "Native output: $StdoutLog"
    Write-Host "Python diagnostics: $StderrLog"
    # A hidden console still allows the application's own Tk window to appear.
    # Separate redirections avoid PowerShell interpreting ordinary Python logging
    # on stderr as a terminating NativeCommandError.
    $RunPy = Join-Path $RepoRoot "run.py"
    $Process = Start-Process -FilePath $PythonExe -ArgumentList @("-u", ('"' + $RunPy + '"')) `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog -PassThru
    $Process.WaitForExit()
    if ($Process.ExitCode -ne 0) {
        throw "Conduit exited with code $($Process.ExitCode). See $StderrLog"
    }
} finally {
    [Environment]::SetEnvironmentVariable("CONDUIT_VIDEO_STAGE1", $PreviousMode, "Process")
    if ($null -ne $Process) { $Process.Dispose() }
}
