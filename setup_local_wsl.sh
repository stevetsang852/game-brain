#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--build" ]]; then
    if [[ "$EUID" != 0 ]]; then
        echo "ERROR: Run setup through setup_local_wsl.bat." >&2
        exit 1
    fi
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        build-essential cmake pkg-config git python3-dev python3-venv \
        libffi-dev libpng-dev zlib1g-dev libavcodec-dev libavformat-dev \
        libavutil-dev libswscale-dev libswresample-dev libavfilter-dev
    if ! id game-brain >/dev/null 2>&1; then
        useradd --create-home --shell /bin/bash game-brain
    fi
    exec runuser -u game-brain -- bash "$0" --build
fi

root="$HOME/.game-brain"
source_dir="$root/mgba-src"
build_dir="$source_dir/build"
mkdir -p "$root"
if [[ ! -x "$root/venv/bin/python" ]]; then
    python3 -m venv "$root/venv"
fi
"$root/venv/bin/python" -m pip install --quiet cffi cached-property setuptools pytest-runner
if [[ ! -d "$source_dir" ]]; then
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch 0.10.5 \
        https://github.com/mgba-emu/mgba.git "$source_dir"
fi
if [[ "$(git -C "$source_dir" rev-parse HEAD)" != "26b7884bc25a5933960f3cdcd98bac1ae14d42e2" ]]; then
    echo "ERROR: Expected the mGBA 0.10.5 source revision in $source_dir." >&2
    exit 1
fi
cmake -S "$source_dir" -B "$build_dir" \
    -DBUILD_PYTHON=ON -DUSE_FFMPEG=ON -DBUILD_QT=OFF -DBUILD_SDL=OFF \
    -DBUILD_GL=OFF -DENABLE_SCRIPTING=OFF \
    -DPYTHON_EXECUTABLE="$root/venv/bin/python" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_C_FLAGS="-Wno-error=incompatible-pointer-types -Wno-incompatible-pointer-types"
export CFLAGS="-Wno-error=incompatible-pointer-types -Wno-incompatible-pointer-types"
cmake --build "$build_dir" -j 4
bindings=$(find "$build_dir/python" -maxdepth 1 -type d -name 'lib.*' -print -quit)
if [[ -z "$bindings" ]]; then
    echo "ERROR: No built mGBA Python bindings found." >&2
    exit 1
fi
PYTHONPATH="$bindings" LD_LIBRARY_PATH="$build_dir" \
    "$root/venv/bin/python" -c 'import mgba.core, mgba.image, mgba.log, mgba.vfs'
echo "Local WSL setup complete. Run start_local.bat."
