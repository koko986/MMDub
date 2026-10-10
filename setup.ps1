$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

Write-Host ""
Write-Host "MMDub setup" -ForegroundColor Cyan
Write-Host "This creates a private Python environment inside the app folder."
Write-Host "Speech uses Microsoft Edge online voices; transcription stays local."
Write-Host ""

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher was not found. Install Python 3.12, then run setup again."
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Creating the Python 3.12 environment..." -ForegroundColor Yellow
    & py -3.12 -m venv $Venv
}

Write-Host "Updating installation tools..." -ForegroundColor Yellow
& $Python -m pip install --upgrade pip wheel setuptools

Write-Host "Installing NVIDIA GPU support (this is the largest download)..." -ForegroundColor Yellow
# CUDA 12.1 is supported by the installed NVIDIA driver and saves roughly 1 GB
# compared with the CUDA 12.8 wheel while remaining compatible with local Whisper.
& $Python -m pip install torch==2.5.1+cu121 torchaudio==2.5.1+cu121 --extra-index-url https://download.pytorch.org/whl/cu121

Write-Host "Installing online Burmese speech, private transcription, video tools, and the local studio..." -ForegroundColor Yellow
& $Python -m pip uninstall -y f5-myanmar-tts 2>$null
& $Python -m pip install -r (Join-Path $Root "requirements.txt")

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Double-click start.cmd to open MMDub."
