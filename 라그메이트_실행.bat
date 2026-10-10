@echo off
rem Keep this file ASCII-only: cmd misparses UTF-8 Korean text in .bat files.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install it from https://www.python.org
  pause
  exit /b 1
)

python -c "import PySide6, openpyxl, cv2, windows_capture, winrt.windows.media.ocr" >nul 2>nul
if errorlevel 1 (
  echo Installing required packages - first run only...
  python -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Package install failed.
    pause
    exit /b 1
  )
)

start "RagnaMate" pythonw main.py %*
