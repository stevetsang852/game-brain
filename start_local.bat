@echo off
setlocal

cd /d "%~dp0"

set "GAME_BRAIN_CONFIG_DIR=%USERPROFILE%\.game-brain"
set "GAME_BRAIN_ROM_PATH_FILE=%GAME_BRAIN_CONFIG_DIR%\rom-path.txt"
if not exist "%GAME_BRAIN_CONFIG_DIR%" mkdir "%GAME_BRAIN_CONFIG_DIR%"
if errorlevel 1 goto config_error

if not defined GAME_BRAIN_ROM_FILE if exist "%GAME_BRAIN_ROM_PATH_FILE%" (
    set /p "GAME_BRAIN_ROM_FILE="<"%GAME_BRAIN_ROM_PATH_FILE%"
)

if "%GAME_BRAIN_ROM_FILE%"=="" goto prompt_rom
if exist "%GAME_BRAIN_ROM_FILE%" goto rom_path_valid
echo Remembered ROM file not found. Please choose it again.
set "GAME_BRAIN_ROM_FILE="
goto prompt_rom

:prompt_rom
set /p "GAME_BRAIN_ROM_FILE= FireRed ROM path: "

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

set "GAME_BRAIN_ROM=%GAME_BRAIN_ROM_FILE%"
if not defined GAME_BRAIN_SAVE_DIR set "GAME_BRAIN_SAVE_DIR=%GAME_BRAIN_CONFIG_DIR%\saves"
if not exist "%GAME_BRAIN_SAVE_DIR%" mkdir "%GAME_BRAIN_SAVE_DIR%"
if errorlevel 1 goto data_dir_error

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Native Python 3.10 or newer is unavailable; trying Ubuntu WSL instead.
    goto start_wsl
)
python -c "import mgba.core, mgba.image, mgba.log" >nul 2>&1
if errorlevel 1 (
    echo Native mGBA bindings are unavailable; trying Ubuntu WSL instead.
    goto start_wsl
)

if not defined GAME_BRAIN_MEMORY_DIR set "GAME_BRAIN_MEMORY_DIR=%GAME_BRAIN_CONFIG_DIR%\memory"
if not exist "%GAME_BRAIN_MEMORY_DIR%" mkdir "%GAME_BRAIN_MEMORY_DIR%"
if errorlevel 1 goto data_dir_error

echo.
echo Starting the local dashboard at http://127.0.0.1:8765/
echo Press Ctrl-C in this window to stop.
python -m game_brain.dashboard --adapter mgba --brains battle,path,rule --save-dir "%GAME_BRAIN_SAVE_DIR%" --memory-dir "%GAME_BRAIN_MEMORY_DIR%"
if errorlevel 1 (
    echo.
    echo ERROR: The local dashboard stopped with an error.
    pause
    exit /b 1
)

endlocal
exit /b 0

:start_wsl
echo Starting the local dashboard at http://127.0.0.1:8765/
echo Press Ctrl-C in this window to stop.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_wsl.ps1"
if errorlevel 1 (
    echo.
    echo ERROR: The WSL dashboard failed. Run setup_local_wsl.bat if setup is incomplete.
    pause
    exit /b 1
)
endlocal
exit /b 0

:config_error
echo ERROR: Could not create "%GAME_BRAIN_CONFIG_DIR%".
pause
exit /b 1

:data_dir_error
echo ERROR: Could not create the save or memory directory.
pause
exit /b 1
