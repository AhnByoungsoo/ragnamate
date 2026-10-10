@echo off
setlocal
rem ASCII only. Share this one file: it downloads RagnaMate next to itself and starts it.
rem Run it again any time - once downloaded it only starts the program.
cd /d "%~dp0"
set RO_DIR=%~dp0ragnamate
set RO_NEED=

git --version >nul 2>nul
if errorlevel 1 (
    echo Git is not installed. Installing...
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
    set RO_NEED=1
)
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto python_ok
python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 goto python_ok
echo Python is not installed. Installing...
winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
set RO_NEED=1
:python_ok
if defined RO_NEED (
    echo.
    echo Installed what was missing. Close this window and run this file once more.
    echo If the install above failed, install Git ^(git-scm.com^) and Python ^(python.org^) yourself.
    pause
    exit /b 0
)

if exist "%RO_DIR%\.git" goto run
echo Downloading RagnaMate...
git clone https://github.com/AhnByoungsoo/ragnamate.git "%RO_DIR%"
if errorlevel 1 (
    echo.
    echo Download failed. Check the internet connection and try again.
    pause
    exit /b 1
)
:run
if defined RO_INSTALL_ONLY exit /b 0
call "%RO_DIR%\run_replay.bat" %*
