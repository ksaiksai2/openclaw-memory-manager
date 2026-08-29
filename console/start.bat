@echo off
REM OMM Console Launcher

cd /d "%~dp0"

REM Check if node_modules exists
if not exist "node_modules" (
    echo Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo Failed to install dependencies
        pause
        exit /b 1
    )
)

REM Start Electron app
echo Starting OMM Console...
start "" npx electron .
