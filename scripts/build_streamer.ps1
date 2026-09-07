param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$NativeSrc = Join-Path $RepoRoot "native\conduit_streamer"
$BinDir = Join-Path $RepoRoot "app\bin"

if (-not (Test-Path -LiteralPath $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir | Out-Null
}

$VcvarsCandidates = @(
    "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files (x86)\Microsoft Visual Studio\2019\Community\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)

$Vcvars = $null
foreach ($cand in $VcvarsCandidates) {
    if (Test-Path -LiteralPath $cand -PathType Leaf) {
        $Vcvars = $cand
        break
    }
}

if (-not $Vcvars) {
    throw "Visual C++ build environment (vcvars64.bat) was not found."
}

Write-Host "Using MSVC environment from: $Vcvars"

$Sources = Get-ChildItem -Path $NativeSrc -Filter "*.cpp" | Select-Object -ExpandProperty FullName
$SourceArgs = ($Sources | ForEach-Object { "`"$_`"" }) -join " "

$OutputFile = Join-Path $BinDir "conduit_streamer.dll"

# Invoke cmd.exe to setup MSVC env and run cl.exe
$BuildCmd = @"
call "$Vcvars" >nul
cl.exe /nologo /O2 /Oi /guard:cf /std:c++17 /EHsc /W4 /LD /DCONDUIT_STREAMER_EXPORTS /DWIN32_LEAN_AND_MEAN /DUNICODE /D_UNICODE /I"$NativeSrc" $SourceArgs /Fe"$OutputFile" /link /GUARD:CF /DYNAMICBASE /NXCOMPAT /OPT:REF /OPT:ICF d3d11.lib dxgi.lib mfplat.lib mfreadwrite.lib mfuuid.lib wmcodecdspuuid.lib ws2_32.lib bcrypt.lib user32.lib gdi32.lib ole32.lib
"@

$TempBat = Join-Path $NativeSrc "build_temp.bat"
Set-Content -Path $TempBat -Value $BuildCmd -Encoding ASCII

try {
    Write-Host "Compiling conduit_streamer.dll..."
    cmd.exe /c "$TempBat"
    if ($LASTEXITCODE -ne 0) {
        throw "Compilation failed with exit code $LASTEXITCODE"
    }
    Write-Host "Successfully compiled: $OutputFile"
} finally {
    if (Test-Path -LiteralPath $TempBat) {
        Remove-Item -LiteralPath $TempBat -Force
    }
    # Clean up intermediate obj files
    Get-ChildItem -Path $RepoRoot -Filter "*.obj" | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $RepoRoot -Filter "*.exp" | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $RepoRoot -Filter "*.lib" | Where-Object { $_.DirectoryName -eq $RepoRoot -or $_.DirectoryName -eq $BinDir } | Remove-Item -Force -ErrorAction SilentlyContinue
}

# Scan with Windows Defender
$MpCmdRun = Join-Path $env:ProgramFiles "Windows Defender\MpCmdRun.exe"
if (Test-Path -LiteralPath $MpCmdRun) {
    Write-Host "Scanning $OutputFile with Windows Defender..."
    & $MpCmdRun -Scan -ScanType 3 -File $OutputFile
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Defender detected an issue with the compiled binary!"
    }
    Write-Host "Windows Defender scan clean (0 threats)."
}
