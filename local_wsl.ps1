param([switch]$Setup)

$ErrorActionPreference = 'Stop'
try {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw 'WSL is required. Install an Ubuntu distribution on F: before running this launcher.'
    }

    $registered = @(& wsl.exe --list --quiet 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not list WSL distributions. Check that WSL is installed and initialized.'
    }
    $distros = @($registered | ForEach-Object {
        ($_ -replace "`0", '').Trim()
    } | Where-Object { $_ -and $_ -match '^Ubuntu(?:-|$)' })
    if ($distros -contains 'Ubuntu') {
        $distro = 'Ubuntu'
    } elseif ($distros.Count -eq 1) {
        $distro = $distros[0]
    } elseif ($distros.Count -eq 0) {
        throw 'No Ubuntu WSL distribution is installed. To install on F:, run "wsl --install -d Ubuntu --location F:\WSL\Ubuntu", then setup_local_wsl.bat.'
    } else {
        throw "More than one Ubuntu WSL distribution is installed ($($distros -join ', ')); keep one named Ubuntu or ask to select one."
    }
    Write-Host "Using WSL distribution: $distro"

    $lxss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    $registration = Get-ChildItem $lxss -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty $_.PSPath } |
        Where-Object { $_.DistributionName -eq $distro } |
        Select-Object -First 1
    if (-not $registration -or -not $registration.BasePath) {
        throw "Could not verify where WSL distribution '$distro' is stored; refusing to install dependencies."
    }
    $basePath = [Environment]::ExpandEnvironmentVariables($registration.BasePath)
    if ([IO.Path]::GetPathRoot($basePath) -ine 'F:\') {
        throw "WSL distribution '$distro' is stored at '$basePath', not F:. Move/reinstall it on F: before running setup_local_wsl.bat."
    }
    Write-Host "WSL storage is on F: ($basePath)"

    $repo = & wsl.exe -d $distro -u root --exec wslpath -a -u $PSScriptRoot
    if ($LASTEXITCODE -ne 0) {
        throw "WSL distribution '$distro' is not ready. Start it once from a terminal, then run setup_local_wsl.bat."
    }
    $repo = $repo.Trim()
    if ($Setup) {
        & wsl.exe -d $distro -u root --exec bash "$repo/setup_local_wsl.sh"
    } else {
        $env:GAME_BRAIN_WSL_REPO = $repo
        $env:WSLENV = ($env:WSLENV, 'GAME_BRAIN_WSL_REPO', 'GAME_BRAIN_ROM/p',
            'GAME_BRAIN_SAVE_DIR/p', 'GAME_BRAIN_MEMORY_DIR/p', 'GAME_BRAIN_STARTER',
            'GAME_BRAIN_START_STATE/p' | Where-Object { $_ }) -join ':'
        & wsl.exe -d $distro -u game-brain --exec bash "$repo/start_local_wsl.sh"
    }
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
