# Downloads the pinned uv for Windows into <Root>\.uv\bin\uv.exe, checking its SHA-256 against the value below.
# Run by install.cmd:  powershell -NoProfile -ExecutionPolicy Bypass -File tools\get-uv.ps1 -Root <folder> -Arch <AMD64|ARM64>
# Exit codes: 0 = uv.exe is ready, 2 = checksum mismatch (do NOT fall back), 1 = anything else (install.cmd falls back).
# Testing only: ATF_UV_BASE replaces https://github.com/astral-sh/uv/releases/download
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Arch
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Windows PowerShell 5.1 downloads are very slow with the progress bar

$Version = '0.13.6'
# Hard-coded on purpose: never trust a checksum fetched at install time. From the release's .sha256 files.
$Hashes = @{
    'uv-x86_64-pc-windows-msvc.zip'  = 'a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f'
    'uv-aarch64-pc-windows-msvc.zip' = 'c2426c0c8e28b53d8c440e47a18d96ad4db51e159866c0e81435b196d7f0081c'
}

$bin = Join-Path $Root '.uv\bin'
$tmp = Join-Path $Root '.uv\tmp'
try {
    switch ($Arch.ToUpper()) {
        'AMD64' { $asset = 'uv-x86_64-pc-windows-msvc.zip' }
        'ARM64' { $asset = 'uv-aarch64-pc-windows-msvc.zip' }
        default { Write-Host "uv has no download for this processor ($Arch)."; exit 1 }
    }
    $base = $env:ATF_UV_BASE
    if (-not $base) { $base = 'https://github.com/astral-sh/uv/releases/download' }
    $url = "$($base.TrimEnd('/'))/$Version/$asset"

    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp $asset
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip

    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
    if ($got -ne $Hashes[$asset]) {
        Write-Host "The uv download does not match its expected SHA-256 checksum, so it was not used."
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $bin 'uv.exe') -Force -ErrorAction SilentlyContinue
        exit 2
    }

    $out = Join-Path $tmp 'x'
    Expand-Archive -LiteralPath $zip -DestinationPath $out -Force
    $exe = Join-Path $out 'uv.exe'   # the zip holds uv.exe at its root
    if (-not (Test-Path -LiteralPath $exe)) { throw 'uv.exe is not in the download.' }
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    Copy-Item -LiteralPath $exe -Destination (Join-Path $bin 'uv.exe') -Force
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}
catch {
    Write-Host ("Could not get uv: " + $_.Exception.Message)
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
