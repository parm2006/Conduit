param([switch]$Probe)
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$NativeRoot = Join-Path $RepoRoot 'native\conduit_streamer'
$BuildDir = Join-Path $RepoRoot 'build\video-stage1'
$Candidates = @(
    'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat',
    'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat',
    'C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat',
    'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat'
)
$Vcvars = $Candidates | Where-Object {Test-Path -LiteralPath $_ -PathType Leaf} | Select-Object -First 1
if (-not $Vcvars) { throw 'Visual C++ build environment (vcvars64.bat) was not found.' }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$BatchPath = Join-Path $BuildDir ('compile-components-' + [guid]::NewGuid().ToString('N') + '.cmd')
$TestsSource = Join-Path $NativeRoot 'tests\pipeline_tests.cpp'
$ProbeSource = Join-Path $NativeRoot 'tools\mft_probe.cpp'
$Batch = @"
@call "$Vcvars" >nul
@cl /nologo /std:c++17 /EHsc /W4 /I"$NativeRoot" "$TestsSource" /Fe:pipeline_tests.exe /Fo:pipeline_tests.obj ole32.lib
@if errorlevel 1 exit /b 1
@pipeline_tests.exe
@if errorlevel 1 exit /b 1
@cl /nologo /std:c++17 /EHsc /W4 "$ProbeSource" /Fe:mft_probe.exe /Fo:mft_probe.obj /link mfplat.lib mfuuid.lib strmiids.lib ole32.lib
@if errorlevel 1 exit /b 1
"@
# The unittest wrapper and explicit developer runs may overlap. Serialize the
# shared executable outputs; unique command paths also prevent cleanup races.
$BuildMutex = [System.Threading.Mutex]::new($false, 'Local\ConduitVideoStage1Components')
$Acquired = $false
try {
    try { $Acquired = $BuildMutex.WaitOne(60000) }
    catch [System.Threading.AbandonedMutexException] { $Acquired = $true }
    if (-not $Acquired) { throw 'Timed out waiting for the Stage 1 component build.' }
    Set-Content -LiteralPath $BatchPath -Value $Batch -Encoding ASCII
    Push-Location -LiteralPath $BuildDir
    try {
        & cmd.exe /c $BatchPath
        if ($LASTEXITCODE -ne 0) { throw "Stage 1 compilation/tests failed (exit $LASTEXITCODE)." }
        if ($Probe) {
            & (Join-Path $BuildDir 'mft_probe.exe')
            if ($LASTEXITCODE -ne 0) { throw "MFT enumeration failed (exit $LASTEXITCODE)." }
        }
    } finally {
        Pop-Location
    }
} finally {
    if (Test-Path -LiteralPath $BatchPath) { Remove-Item -LiteralPath $BatchPath -Force }
    if ($Acquired) { $BuildMutex.ReleaseMutex() }
    $BuildMutex.Dispose()
}
Write-Host "Stage 1 tests passed; probe executable: $(Join-Path $BuildDir 'mft_probe.exe')"
