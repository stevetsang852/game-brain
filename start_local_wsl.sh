#!/usr/bin/env bash
set -euo pipefail

root="$HOME/.game-brain"
build_dir="$root/mgba-src/build"
if [[ ! -x "$root/venv/bin/python" || ! -d "$build_dir/python" ]]; then
    echo "ERROR: Run setup_local_wsl.bat first to build mGBA in Ubuntu WSL." >&2
    exit 1
fi
bindings=$(find "$build_dir/python" -maxdepth 1 -type d -name 'lib.*' -print -quit)
if [[ -z "$bindings" ]]; then
    echo "ERROR: Missing mGBA bindings. Run setup_local_wsl.bat again." >&2
    exit 1
fi
export PYTHONPATH="$bindings${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$build_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$root/venv/bin/python" -c 'import mgba.core, mgba.image, mgba.log, mgba.vfs'
cd "$GAME_BRAIN_WSL_REPO"
exec "$root/venv/bin/python" -m game_brain.dashboard \
    --adapter mgba --brains battle,path,rule \
    --save-dir "$GAME_BRAIN_SAVE_DIR" --memory-dir "${GAME_BRAIN_MEMORY_DIR:-$root/memory}"
