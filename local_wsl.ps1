param([switch]$Setup)

$ErrorActionPreference = 'Stop'
try {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw 'WSL is required. Run "wsl --install -d Ubuntu" in Administrator PowerShell first.'
    }
    $repo = & wsl.exe -d Ubuntu -u root --exec wslpath -a -u $PSScriptRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Ubuntu WSL is not ready. Run "wsl --install -d Ubuntu", then setup_local_wsl.bat.'
    }
    $repo = $repo.Trim()
    if ($Setup) {
        & wsl.exe -d Ubuntu -u root --exec bash "$repo/setup_local_wsl.sh"
    } else {
        $env:GAME_BRAIN_WSL_REPO = $repo
        $env:WSLENV = ($env:WSLENV, 'GAME_BRAIN_WSL_REPO', 'GAME_BRAIN_ROM/p',
            'GAME_BRAIN_SAVE_DIR/p', 'GAME_BRAIN_MEMORY_DIR/p', 'GAME_BRAIN_STARTER',
            'GAME_BRAIN_START_STATE/p' | Where-Object { $_ }) -join ':'
        & wsl.exe -d Ubuntu -u game-brain --exec bash "$repo/start_local_wsl.sh"
    }
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
