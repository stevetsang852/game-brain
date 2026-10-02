# game-brain + mGBA 0.10.5 (Python bindings, USE_FFMPEG) in one local image.
# ROMs and save states are NEVER copied in: mount them read-only at run time (-v ...:ro).
# This image is for local use only; do not push it to a registry.

# ---------- stage 1: build mGBA from source ----------
FROM debian:trixie-slim AS mgba-build
ARG MGBA_VERSION=0.10.5
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git cmake g++ make pkg-config \
        libpng-dev zlib1g-dev python3-dev python3-cffi python3-setuptools python3-pytest-runner libffi-dev \
        libavcodec-dev libavfilter-dev libavformat-dev libavutil-dev libswscale-dev libswresample-dev \
    && rm -rf /var/lib/apt/lists/*
# A git checkout (not a release tarball): the Python bindings' setup.py asks
# version.cmake for GIT_TAG, which needs `git describe` to work.
RUN git clone --depth 1 --branch "${MGBA_VERSION}" https://github.com/mgba-emu/mgba.git /opt/mgba
WORKDIR /opt/mgba
# USE_FFMPEG: without it libmgba lacks the EReaderScan symbols the Python bindings link against.
# GCC 14 turned incompatible-pointer-types into an error; mGBA 0.10.5 still trips it.
# CFLAGS too: the cffi extension is compiled by setup.py, which ignores CMAKE_C_FLAGS.
ENV CFLAGS="-Wno-error=incompatible-pointer-types -Wno-incompatible-pointer-types"
RUN mkdir build && cd build && cmake .. \
        -DBUILD_PYTHON=ON -DUSE_FFMPEG=ON \
        -DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_GL=OFF -DBUILD_GLES2=OFF -DBUILD_GLES3=OFF \
        -DENABLE_SCRIPTING=OFF -DUSE_DISCORD_RPC=OFF \
        -DCMAKE_C_FLAGS="-Wno-error=incompatible-pointer-types -Wno-incompatible-pointer-types" \
    && make -j"$(nproc)" \
    && mkdir -p /opt/mgba-dist/lib /opt/mgba-dist/python \
    && cp -a libmgba.so* /opt/mgba-dist/lib/ \
    && cp -a python/lib.linux-*/mgba /opt/mgba-dist/python/

# ---------- stage 2: runtime ----------
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-cffi python3-cached-property python3-pytest \
        libpng16-16t64 zlib1g \
        libavcodec61 libavfilter10 libavformat61 libavutil59 libswscale8 libswresample5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=mgba-build /opt/mgba-dist/lib/ /opt/mgba/lib/
COPY --from=mgba-build /opt/mgba-dist/python/ /opt/mgba/python/
WORKDIR /app
COPY game_brain/ game_brain/
COPY tests/ tests/
COPY examples/ examples/
COPY notes/ notes/
COPY pyproject.toml requirements.txt README.md ./
RUN useradd --create-home --uid 1000 brain && mkdir -p /app/runs && chown brain /app/runs
USER brain
# GAME_BRAIN_IN_CONTAINER=1 lets the dashboard bind 0.0.0.0 *inside* the container
# (needed for -p to reach it). Publish it on the host as 127.0.0.1:8765:8765 only.
ENV PYTHONPATH=/app:/opt/mgba/python \
    LD_LIBRARY_PATH=/opt/mgba/lib \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider" \
    GAME_BRAIN_IN_CONTAINER=1 \
    GAME_BRAIN_ROM=/data/rom.gba
EXPOSE 8765
CMD ["python3", "-m", "game_brain.dashboard", "--adapter", "mgba", "--host", "0.0.0.0", "--port", "8765"]
