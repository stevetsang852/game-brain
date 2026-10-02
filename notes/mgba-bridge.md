# mGBA bridge (`--adapter mgba`)

`game_brain.adapters.gba_mgba.MgbaFireRedAdapter` implements `Adapter` (see
`adapter-interface.md`) on mGBA's own Python bindings (cffi), headless, no Lua.

## Setup (Linux; what the shared box uses)

mGBA 0.10.5 built from source with Python bindings:

```sh
git clone --branch 0.10.5 https://github.com/mgba-emu/mgba && cd mgba && mkdir build && cd build
# Debian/Ubuntu deps: cmake g++ pkg-config libpng-dev zlib1g-dev python3-dev python3-cffi libffi-dev
#   libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libswresample-dev libavfilter-dev
cmake .. -DBUILD_PYTHON=ON -DUSE_FFMPEG=ON -DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_GL=OFF -DENABLE_SCRIPTING=OFF \
         -DCMAKE_C_FLAGS="-Wno-error=incompatible-pointer-types -Wno-incompatible-pointer-types"
make -j8
pip install cached_property
```

GCC 14 needs the `incompatible-pointer-types` flags. `USE_FFMPEG=ON` is required: the Python
bindings reference the e-Reader scan functions (`EReaderScanLoadImageA`), which 0.10.5 only
compiles under `USE_FFMPEG`; without it `import mgba.core` fails with an undefined symbol.

Run:

```sh
export PYTHONPATH=<build>/python/lib.linux-x86_64-cpython-3XX LD_LIBRARY_PATH=<build>
export GAME_BRAIN_ROM=/path/to/firered.gba      # never commit ROMs / saves / screenshots
python -m game_brain.demo --adapter mgba --steps 700 --mode auto
python -m pytest -q                              # real-ROM tests run only when both are set
```

Optional `GAME_BRAIN_START_STATE=<file>` loads a raw mGBA state on every `reset()` instead of
power-on (local file; `*.state` is gitignored).

## Determinism

`reset()` is power-on with no battery save, keys cleared, frame counter 0. `act()` sets the key
mask before every `run_frame()`, with no wall-clock sleeps. FireRed has no RTC. Tested: two
independent runs of the same 250 actions give identical (frame, RAM) traces, and
`runlog.replay()` of a 150-step demo log returns no mismatches.

## RAM map (`firered.py`) and how each address was verified

The supplied ROM is a modified image (SHA1 `e0194282c427689768f8e618a285552f264524a4`, not clean
1.0 `41cb23d8…`), so nothing is taken on trust from pokefirered. Each address was checked on this
ROM headless, mashing A through the intro (8 frames A, 8 frames released):

| ram key | source | check |
|---|---|---|
| `vblank_counter` | `gMain.vblankCounter2` `0x030030F0+0x24` | increases by exactly N over N frames once the game runs (it starts ~13 frames after power-on) |
| `held_keys` | `gMain.heldKeys` `+0x2C` | B bit set while B is held |
| `callback2`, `scene` | `gMain.callback2` `+0x04` | changes per scene; equals `0x080565B5` (CB2_Overworld) from the moment the player appears in their room |
| `player_x`, `player_y` | `*gSaveBlock1Ptr` (`0x03005008`) `+0/+2` | arrive at (6,6); DOWN gives (6,7), LEFT (5,7), UP (5,6), RIGHT (6,6); the player object's coords move in step (+7 map offset) |
| `map_bank`, `map_id` | `*gSaveBlock1Ptr +4/+5` | 4/1 (Pallet Town, player's house 2F) on arrival |
| `facing` | `gObjectEvents[gPlayerAvatar.objectEventId].facingDirection` (`0x02036E38`, `0x02037078+5`, low nibble of `+0x18`) | 1/2/3/4 = DOWN/UP/LEFT/RIGHT after each walk above |

`player_x/y`, `map_*` and `facing` are only emitted while `callback2 == CB2_Overworld`, per the
interface (no position outside the overworld).

**Not included yet, because not verified on this ROM:** `in_battle` and `party_count`. Both need a
state past getting the starter / a first battle; a follow-up PR will add them with the same kind
of check.
