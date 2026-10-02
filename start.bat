@echo off
setlocal

cd /d "%~dp0"

set /p "GAME_BRAIN_ROM_FILE= PATH? : "

if not exist "%GAME_BRAIN_ROM_FILE%" (
    echo ERROR: ROM file not found:
    echo "%GAME_BRAIN_ROM_FILE%"
    pause
    exit /b 1
)

rem save states live outside the repo (compose.yaml mounts this folder as /saves)
if not defined GAME_BRAIN_SAVE_DIR_HOST set "GAME_BRAIN_SAVE_DIR_HOST=%USERPROFILE%\.game-brain\saves"
if not exist "%GAME_BRAIN_SAVE_DIR_HOST%" mkdir "%GAME_BRAIN_SAVE_DIR_HOST%"

docker compose up -d --build
if errorlevel 1 (
    echo.
    echo ERROR: Docker Compose failed. Check that Docker Desktop is running.
    pause
    exit /b 1
)

echo.
echo Dashboard is running at http://127.0.0.1:8765/
endlocal