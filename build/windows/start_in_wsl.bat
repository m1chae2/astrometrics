@echo off
REM Astrometrics WSL Launcher for Windows
REM This script automatically starts Astrometrics (backend + GUI) using WSL2
REM Usage: start_in_wsl.bat [--install]
REM   --install    Reinstall dependencies before starting

setlocal enabledelayedexpansion

echo ========================================
echo Astrometrics - WSL Launcher for Windows
echo ========================================
echo.

REM Check if WSL is installed
wsl --list --verbose >nul 2>&1
if errorlevel 1 (
    echo ERROR: WSL2 does not appear to be installed or enabled.
    echo.
    echo To set up WSL2 on your Windows system:
    echo 1. Open PowerShell as Administrator
    echo 2. Run: wsl --install
    echo 3. Restart your computer
    echo 4. Run this script again
    echo.
    echo For detailed instructions, see: WSL_SETUP.md
    pause
    exit /b 1
)

echo ✓ WSL2 detected
echo.

REM Get the current directory in Windows and convert to WSL path
for /f "tokens=*" %%i in ('wsl wslpath -a "%CD%"') do set WSL_PATH=%%i

REM Parse arguments
set INSTALL_ARG=
if "%1"=="--install" set INSTALL_ARG=--install

echo Starting Astrometrics in WSL...
echo WSL Path: %WSL_PATH%
echo.
echo Backend:  http://127.0.0.1:5000
echo Frontend: http://127.0.0.1:5173
echo.
echo Press Ctrl+C to stop the application.
echo.
pause

REM Execute start_full.sh in WSL
wsl -d Ubuntu bash -c "cd %WSL_PATH% && bash scripts/start_full.sh %INSTALL_ARG%"
