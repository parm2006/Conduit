param(
    [string]$SourcePath,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $SourcePath) {
    $SourcePath = Join-Path $RepoRoot "app\assets\app_icon_source.png"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $RepoRoot "app\assets"
}
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw "The project-owned icon source PNG was not found."
}
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
}

Add-Type -AssemblyName System.Drawing

function New-IconPng {
    param(
        [System.Drawing.Bitmap]$Source,
        [System.Drawing.Rectangle]$Crop,
        [int]$Size
    )

    $canvas = New-Object System.Drawing.Bitmap(
        $Size,
        $Size,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [System.Drawing.Graphics]::FromImage($canvas)
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality

        $padding = [Math]::Max(1, [Math]::Round($Size * 0.08))
        $available = $Size - (2 * $padding)
        $scale = [Math]::Min(
            $available / $Crop.Width,
            $available / $Crop.Height
        )
        $width = [Math]::Max(1, [Math]::Round($Crop.Width * $scale))
        $height = [Math]::Max(1, [Math]::Round($Crop.Height * $scale))
        $destination = New-Object System.Drawing.Rectangle(
            [Math]::Floor(($Size - $width) / 2),
            [Math]::Floor(($Size - $height) / 2),
            $width,
            $height
        )
        $graphics.DrawImage(
            $Source,
            $destination,
            $Crop.X,
            $Crop.Y,
            $Crop.Width,
            $Crop.Height,
            [System.Drawing.GraphicsUnit]::Pixel
        )
    }
    finally {
        $graphics.Dispose()
    }

    $stream = New-Object System.IO.MemoryStream
    try {
        $canvas.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        return $stream.ToArray()
    }
    finally {
        $stream.Dispose()
        $canvas.Dispose()
    }
}

$source = [System.Drawing.Bitmap]::FromFile((Resolve-Path -LiteralPath $SourcePath))
try {
    $left = $source.Width
    $top = $source.Height
    $right = -1
    $bottom = -1
    for ($y = 0; $y -lt $source.Height; $y++) {
        for ($x = 0; $x -lt $source.Width; $x++) {
            if ($source.GetPixel($x, $y).A -gt 8) {
                if ($x -lt $left) { $left = $x }
                if ($x -gt $right) { $right = $x }
                if ($y -lt $top) { $top = $y }
                if ($y -gt $bottom) { $bottom = $y }
            }
        }
    }
    if ($right -lt $left -or $bottom -lt $top) {
        throw "The icon source PNG contains no visible pixels."
    }
    $crop = New-Object System.Drawing.Rectangle(
        $left,
        $top,
        ($right - $left + 1),
        ($bottom - $top + 1)
    )

    $sizes = @(16, 24, 32, 48, 64, 128, 256)
    $images = @()
    foreach ($size in $sizes) {
        $images += [PSCustomObject]@{
            Size = $size
            Bytes = New-IconPng -Source $source -Crop $crop -Size $size
        }
    }
}
finally {
    $source.Dispose()
}

[System.IO.File]::WriteAllBytes(
    (Join-Path $OutputDirectory "app_icon.png"),
    $images[-1].Bytes
)

$iconPath = Join-Path $OutputDirectory "app_icon.ico"
$stream = [System.IO.File]::Open(
    $iconPath,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::Write
)
$writer = New-Object System.IO.BinaryWriter($stream)
try {
    $writer.Write([UInt16]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]$images.Count)
    $offset = 6 + (16 * $images.Count)
    foreach ($image in $images) {
        $dimension = if ($image.Size -eq 256) { 0 } else { $image.Size }
        $writer.Write([Byte]$dimension)
        $writer.Write([Byte]$dimension)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$image.Bytes.Length)
        $writer.Write([UInt32]$offset)
        $offset += $image.Bytes.Length
    }
    foreach ($image in $images) {
        $writer.Write([Byte[]]$image.Bytes)
    }
}
finally {
    $writer.Dispose()
    $stream.Dispose()
}
