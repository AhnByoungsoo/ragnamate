@echo off
setlocal
rem ASCII only. User data and the existing settings/DB are never removed.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" goto ready
if defined RO_PYTHON goto custom_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    py -3.12 -m venv .venv
    goto created
)
python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 (
    python -m venv .venv
    goto created
)
echo Python 3.12 is required. Install Python, or set RO_PYTHON to python.exe.
pause
exit /b 1
:custom_python
"%RO_PYTHON%" -m venv .venv
:created
if not exist ".venv\Scripts\python.exe" (
    echo Could not create the local Python environment.
    pause
    exit /b 1
)
:ready
".venv\Scripts\python.exe" -c "import PySide6, cv2, numpy, openpyxl, PIL, requests" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements-replay.txt
    if errorlevel 1 (
        echo Dependency installation failed. See the message above.
        pause
        exit /b 1
    )
)
rem Optional: screen capture + Windows OCR for Passive Live. Failure here is not fatal.
".venv\Scripts\python.exe" -c "import windows_capture, winrt.windows.media.ocr" >nul 2>nul
if errorlevel 1 (
    echo Installing optional Live packages - screen capture and Windows OCR...
    ".venv\Scripts\python.exe" -m pip install -r requirements-live.txt
    if errorlevel 1 echo [WARN] Live screen recognition stays off. Replay and storage prices still work.
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
