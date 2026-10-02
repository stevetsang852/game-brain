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

| `map_w`, `map_h`, `collision` | `gMapHeader` (`0x02036DFC`) `->mapLayout` width/height; collision bits 10-11 of each metatile in `gBackupMapLayout` (`0x03005040`, coords +7) | `collision` is rows of `#` (blocked) / `.` (free) over the map layout. In the player's room, 8 moves were predicted from the grid and compared with whether the player actually moved: 8/8 match |
| `warps` | `gMapHeader->events` (`+4`): warpCount `+1`, warps `+8`, 8 bytes each (`x, y, elevation, warpId, mapNum, mapGroup`); `behavior` = metatile behavior of the warp tile (tileset attributes, bits 0-8) | each warp: `x, y, dest_bank, dest_map, behavior, enter`. Walked with BFS over `collision` only: 2F stairs (10,2) + LEFT goes to 1F (4/0); 1F mat (4,8) + DOWN goes to Pallet Town (3/0) at (6,8); Pallet door (6,7) + UP goes back to 4/0 |

`enter` is the button that triggers the warp from (or, for doors, into) its tile, and is only set for
behaviors verified above: `0x65` south arrow mat = DOWN, `0x69` door = UP (walk into it from the tile
below), `0x6F` the house stairs = LEFT. Other warps keep `enter: null`. Note the event table can
list warps that do not trigger: on 1F, (5,8) and (3,9) are listed but have behavior 0 and pressing
DOWN there does nothing; only (4,8) works.

Not handled yet: NPCs/objects blocking tiles, ledges (one-way jumps) and elevation, other warp
behaviors. `collision` and `warps` are re-read on every observe (a few hundred reads, under 1 ms) so
they are never stale during a map transition.

`player_x/y`, `map_*` and `facing` are only emitted while `callback2 == CB2_Overworld`, per the
interface (no position outside the overworld).

**Not included yet, because not verified on this ROM:** `in_battle` and `party_count`. Both need a
state past getting the starter / a first battle; a follow-up PR will add them with the same kind
of check.

## `in_battle` (verified on the supplied ROM)

`ram["in_battle"]` = bit 1 of the byte at `gMain + 0x439` (`0x03003529`), the `inBattle` bitfield
in pokefirered's `struct Main`. Bit 0 of that byte is `oamLoadDisabled`, so only bit 1 is used.
It is reported in every observation, not only in the overworld.

How it was checked:

* A `path,rule` run from boot to the starter (3000 steps, through the Oak cutscene, the lab and
  `party_count` 0 -> 1 at step 963) has `in_battle == False` on every step.
* Starting from a local save state taken right after the starter, I walked to the lab exit. The
  rival stops you at (7,8) and the battle starts. Pressing A until it ended:
  - About 20 steps of screen transition (`callback2` `0x080565A9`, then `0x0800FD9D`) with
    `in_battle == False`.
  - Then `in_battle == True` for 874 observations, with `callback2` `0x08010509` and then
    `0x08011101` during the battle itself.
  - At the end `callback2` is `0x08056809`, `in_battle` goes back to False, and two steps later
    it is the overworld at (7,8) on 4/3.
* `gBattleTypeFlags` (`0x02022B4C`) went 0 -> `0x18` -> `0x1c`, but it **stays `0x1c` after the
  battle**, so it is not usable as "in a battle" and is not exposed.
* Only a trainer battle (the rival) has been seen. A wild battle is not verified yet.

Takeover check for `firered_extra.py` (written by the M2 PR):

* `party_count`: 0 on every step before "received the BULBASAUR" (step 963 of the run
  above), then 1.
* `npcs`: from the post-starter state, pushing RIGHT into the NPC at (10,5), which stands on a
  walkable `.` tile, leaves the player at (9,5) (blocked). The other 7 lab objects either stand
  on `#` tiles or could not be reached before the rival script triggers, so this check covers 1
  of 8. The M2 PR's own check bumped into each NPC.

## `ram["battle"]` (verified on the supplied ROM)

Read by `game_brain/adapters/gba_mgba/firered_battle.py`, present only while `in_battle` is True.
All checks were done in the rival battle in Oak's lab, starting from a local save state taken
right before it (not in the repo).

| key | address | how it was checked |
|---|---|---|
| `player` / `opponent` | `gBattleMons` `0x02023BE4`, 0x58 bytes per battler (opponent `0x02023C3C`); species `+0x00`, moves u16[4] `+0x0C`, pp u8[4] `+0x24`, hp `+0x28`, level `+0x2A`, max_hp `+0x2C` | Raw dump matched the screen: BULBASAUR Lv5 22/22 and CHARMANDER Lv5. The nickname at `+0x30` decodes to the species name, and ability/types (`+0x20..0x22`) are Overgrow, Grass/Poison and Blaze, Fire. The only move, 118, is shown on screen as "Foe CHARMANDER used METRONOME!". PP goes 40 -> 39 after a turn. HP drops match the HP bars, and Lv5 -> Lv6 with max_hp 22 -> 25 after the win. Writing 4 test moves into the slots showed "METRONOME TACKLE / GROWL SCRATCH" and "PP 30/35" in the move menu. |
| `menu` | `gBattlerControllerFuncs[0]` `0x03004FE0` | I compared u32 snapshots of EWRAM and IWRAM across text boxes, the action menu (3 snapshots) and the move menu (2 snapshots). This is the only word that is constant in each menu and differs between them. Action menu = `0x080E763D`, which first appears at the same step the screen first shows "What will BULBASAUR do?". Move menu = `0x080E7989`. These are code addresses in this ROM. Anything else is reported as `"other"`. |
| `cursor` (action) | `gActionSelectionCursor[0]` `0x02023FF8` | Diff of none / RIGHT / DOWN / RIGHT+DOWN gave 0/1/2/3, the only byte with that pattern. Screen: FIGHT, BAG / POKéMON, RUN. LEFT or UP at 0 stays 0, and RIGHT twice stays 1, so it does not wrap. |
| `cursor` (move) | `gMoveSelectionCursor[0]` `0x02023FFC` | Same diff in the move menu (with 4 test moves written in) gave 0/1/2/3, the only byte with that pattern. RIGHT+DOWN showed the arrow on slot 3. |
| `outcome` | `gBattleOutcome` `0x02023E8A` | 0 during the battle. Over 12 runs with different waits before the first action, it was 1 in every run where the opponent's HP hit 0 and 2 in every run where ours did. It is **not cleared** after the battle, so it is only reported inside `battle`. |

Other things found while checking:

* For the first ~5 observations after `in_battle` turns True, `gBattleMons` is still all 0, so
  `player` and `opponent` are None.
* B advances battle text, the same as A.
* Gen 3 remembers the cursor between turns. Pressing past an edge does not wrap.
* **Losing the rival battle does not stop the story.** After a loss the player is back in the
  lab (4/3) at (7,8), the same as after a win: party_count 1, the rival is gone (8 -> 7 NPCs), and
  the player can walk to the exit mat (7,12). Of the 12 runs above, 6 were won and 6 lost.
* In this ROM both starters only know METRONOME, so the rival battle's result is random.
* Not verified yet, so not exposed: status, max_pp, battle type, turn, party, bag, wild battles.
