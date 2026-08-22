@echo off
REM Astrometrics Easy Launcher
REM Starts backend in WSL and frontend on Windows

echo ========================================
echo Starting Astrometrics...
echo ========================================
echo.

REM Ensure the script runs from the repository folder
cd /d "%~dp0"

REM Convert current Windows repo path to WSL path
for /f "delims=" %%i in ('wsl wslpath -a "%CD%"') do set "WSL_PATH=%%i"
if "%WSL_PATH%"=="" (
    echo ERROR: Could not convert repository path to WSL path.
    pause
    exit /b 1
)

echo ✓ WSL Repo Path: %WSL_PATH%

set "WSL_IP="
for /f "delims=" %%i in ('wsl bash -lc "hostname -I | cut -d\" \" -f1"') do set "WSL_IP=%%i"
if "%WSL_IP%"=="" (
    echo ERROR: Could not get WSL IP address.
    echo Make sure WSL is running and connected.
    pause
    exit /b 1
)

echo ✓ WSL IP: %WSL_IP%
echo.

REM Start backend in WSL and keep the shell open if it fails
echo Starting backend in WSL...
start "Astrometrics Backend" wsl bash -lc "cd '%WSL_PATH%' && ./build/linux/run_backend.sh install && ./build/linux/run_backend.sh --lan start; echo; echo Backend launch completed. Press enter to close this window.; read -p 'Press enter to close...'"

echo Waiting for backend to initialize...
timeout /t 10 /nobreak >nul

echo Backend startup command launched.

echo Setting frontend backend URL...
set "BACKEND_URL=http://%WSL_IP%:5000"
echo ✓ Backend URL: %BACKEND_URL%
echo.

REM Prevent Electron from spawning a second backend process
set "SKIP_BACKEND=1"

REM Start frontend (Electron app)
echo Starting frontend...
npm run start

echo.
echo Astrometrics is running!
echo Press Ctrl+C in the terminal to stop.
