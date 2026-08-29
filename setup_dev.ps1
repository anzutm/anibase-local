[CmdletBinding()]
param(
    [switch]$SkipFfmpegInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location $ProjectRoot

function Test-CommandAvailable {
    param([Parameter(Mandatory)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "[1/5] Checking Python..."
if (-not (Test-CommandAvailable "python")) {
    throw "Python was not found. Install Python 3.12.x, then run this script again."
}

$PythonVersion = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
& python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python $PythonVersion is not supported. AniBase developer setup requires Python 3.12.x."
}
Write-Host "      Python $PythonVersion"

Write-Host "[2/5] Preparing virtual environment..."
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv."
    }
}

$VenvPythonVersion = & $VenvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
& $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw ".venv uses Python $VenvPythonVersion. Remove .venv and rerun setup_dev.ps1 with Python 3.12.x."
}
Write-Host "      .venv Python $VenvPythonVersion"

Write-Host "[3/5] Installing Python dependencies..."
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }
& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements.txt." }

Write-Host "[4/5] Checking FFmpeg and FFprobe..."
$FfmpegReady = (Test-CommandAvailable "ffmpeg") -and (Test-CommandAvailable "ffprobe")
if (-not $FfmpegReady -and -not $SkipFfmpegInstall) {
    if (-not (Test-CommandAvailable "winget")) {
        throw "FFmpeg/FFprobe are missing and winget is unavailable. Install the Gyan.FFmpeg package manually."
    }

    Write-Host "      Installing Gyan.FFmpeg with winget..."
    & winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install FFmpeg."
    }

    Write-Warning "FFmpeg was installed. Close this terminal, open a new PowerShell window, and run setup_dev.ps1 again so PATH is refreshed."
    exit 0
}

if (-not $FfmpegReady) {
    throw "FFmpeg and FFprobe are required for developer mode but were not found on PATH."
}

& ffmpeg -version 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "      $_" }
& ffprobe -version 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "      $_" }

Write-Host "[5/5] Validating installed dependencies..."
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Python dependency validation failed." }

Write-Host ""
Write-Host "AniBase developer environment is ready." -ForegroundColor Green
Write-Host "Run the application with:"
Write-Host "  .\.venv\Scripts\pythonw.exe tray_ui.py"
