@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "NEEDS_SETUP=0"
if not exist ".venv\Scripts\python.exe" set "NEEDS_SETUP=1"
if "%NEEDS_SETUP%"=="0" (
  ".venv\Scripts\python.exe" -c "import torch, torchaudio, edge_tts, transformers, sentencepiece, faster_whisper, av, imageio_ffmpeg, yt_dlp, cv2; from google import genai" >nul 2>&1
  if errorlevel 1 set "NEEDS_SETUP=1"
)
if "%NEEDS_SETUP%"=="1" (
  echo First-time setup is required.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  if errorlevel 1 (
    echo.
    echo Setup did not finish. Review the message above.
    pause
    exit /b 1
  )
)
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py"
if errorlevel 1 pause
