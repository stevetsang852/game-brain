@echo off
setlocal

cd /d "%~dp0"

set "GAME_BRAIN_CONFIG_DIR=%USERPROFILE%\.game-brain"
set "GAME_BRAIN_ROM_PATH_FILE=%GAME_BRAIN_CONFIG_DIR%\rom-path.txt"
if not exist "%GAME_BRAIN_CONFIG_DIR%" mkdir "%GAME_BRAIN_CONFIG_DIR%"

if not defined GAME_BRAIN_ROM_FILE if exist "%GAME_BRAIN_ROM_PATH_FILE%" (
    set /p "GAME_BRAIN_ROM_FILE="<"%GAME_BRAIN_ROM_PATH_FILE%"
)

if "%GAME_BRAIN_ROM_FILE%"=="" goto prompt_rom
if exist "%GAME_BRAIN_ROM_FILE%" goto rom_path_valid
echo Remembered ROM file not found. Please choose it again.
set "GAME_BRAIN_ROM_FILE="
goto prompt_rom

:prompt_rom
set /p "GAME_BRAIN_ROM_FILE= PATH? : "

:rom_path_valid
if "%GAME_BRAIN_ROM_FILE%"=="" (
    echo ERROR: No ROM file path was provided.
    pause
    exit /b 1
)
if not exist "%GAME_BRAIN_ROM_FILE%" (
    echo ERROR: ROM file not found:
    echo "%GAME_BRAIN_ROM_FILE%"
    pause
    exit /b 1
)
echo Using ROM: "%GAME_BRAIN_ROM_FILE%"

powershell -NoProfile -Command "[System.IO.File]::WriteAllText($env:GAME_BRAIN_ROM_PATH_FILE, $env:GAME_BRAIN_ROM_FILE + [Environment]::NewLine)"
if errorlevel 1 (
    echo ERROR: Could not save the ROM path:
    echo "%GAME_BRAIN_ROM_PATH_FILE%"
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