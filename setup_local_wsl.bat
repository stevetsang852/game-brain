@echo off
setlocal
cd /d "%~dp0"
echo Building mGBA in Ubuntu WSL. Docker is not used.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_wsl.ps1" -Setup
if errorlevel 1 (
    echo ERROR: Local WSL setup failed. See the error above.
    pause
    exit /b 1
)
pause
endlocal
exit /b 0
