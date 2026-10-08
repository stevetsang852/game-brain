# mGBA bridge (`--adapter mgba`)

`game_brain.adapters.gba_mgba.MgbaFireRedAdapter` implements `Adapter` (see
`adapter-interface.md`) on mGBA's own Python bindings (cffi), headless, no Lua.

## Setup (Windows, without Docker)

`start_local.bat` uses native Python 3.10+ if its mGBA bindings import successfully.
The mGBA desktop application does not include these bindings, and the PyPI `mgba`
package does not provide a Windows wheel. Otherwise the launcher uses an installed
Ubuntu WSL distribution, not Docker Desktop's distribution. It prefers a distribution
named `Ubuntu`, or uses the only registered `Ubuntu-*` distribution (for example
`Ubuntu-F`); it does not install WSL automatically.

1. If Ubuntu WSL is not installed, install it on F: from Administrator PowerShell:
   `wsl --install -d Ubuntu --location F:\WSL\Ubuntu`. Restart if requested.
2. Run `setup_local_wsl.bat` from this repository. It installs Ubuntu build
   dependencies and builds mGBA 0.10.5 with Python bindings and FFmpeg.
3. Run `start_local.bat`, then open http://127.0.0.1:8765/ in your Windows browser.
   Ctrl-C in the launcher window stops the game and writes the final save.

Setup creates a dedicated, unprivileged `game-brain` Linux user. Build dependencies,
the mGBA build, and the Python virtual environment live inside the selected WSL
distribution. When installed with the F: location above, this Linux environment is
stored on F:. Setup does not change Ubuntu's default user or your Windows Python
installation. Re-running setup rebuilds the same pinned mGBA revision.

The launcher remembers the Windows ROM path in `%USERPROFILE%\.game-brain\rom-path.txt`
and translates it for WSL; the WSL ROM cache lives inside the selected distribution.
Saves, run logs, and experience memory default to
`/home/game-brain/.game-brain/{saves,runs,memory}` inside that distribution, avoiding
slow or blocking per-step writes to a Windows/OneDrive-mounted repository. These WSL
paths are stored on F: when the distribution itself is installed there, separately
from native Windows data and Docker volumes. Existing databases are not migrated or
overwritten. Explicit `GAME_BRAIN_SAVE_DIR`, `GAME_BRAIN_MEMORY_DIR`, and
`GAME_BRAIN_START_STATE` Windows paths are translated for WSL; `GAME_BRAIN_STARTER`
is also forwarded.
ROMs selected through the dashboard are remembered inside Ubuntu's
`/home/game-brain/.game-brain/` configuration and take precedence on subsequent WSL
dashboard runs.

Keep the `.sh` scripts with LF line endings (`.gitattributes` enforces this on checkout).
Ubuntu 26.04 / Python 3.14 requires the GCC pointer-warning flags for both the CMake
build and the Python extension build (`CFLAGS`); the setup script supplies both.

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

## `controls_locked` (verified on the supplied ROM)

`ram["controls_locked"]` is the byte at `0x03000F9C`, pokefirered's `sScriptContext2Enabled`
(script.c, set by `ScriptContext2_Enable`). It is 1 while the player's field controls are locked
and is reported in every observation. Stuck detection (`game_brain/stuck.py`) uses it to skip
dialogue, menu and warp steps.

How it was found and checked:

* **Finding it.** The run started from the goal-16 milestone save (Oak's lab, Oak's speech still
  running) and did 800 path-brain steps. IWRAM (`0x03000000`-`0x03007FFF`) was dumped every step.
  - "Locked" steps were 282 direction presses that changed neither the position nor the facing,
    all during Oak's speech.
  - "Free" steps were 389 direction presses that moved or turned the player.
  - Only three bytes were 1 on every locked step and 0 on at least 99.7 % of free steps:
    `0x03000F9C`, `0x03005078` and `0x0300510C`.
  - `0x03000F9C` is the one that matches `sScriptContext2Enabled`; the other two take other values
    (`0x03005078` is 16 in a warp and 4 in the menu).
* **Oak's speech.** 1 on every step until the speech ends, then 0 from the first step the player
  can turn.
* **Door warp.** Leaving the lab by pressing DOWN on the mat at (6,12): 1 from the press through
  the fade and the auto-walk out of the Pallet door ((16,13) to (16,14)), then 0 again.
* **START menu.** In Pallet it goes 0 → START → 1 (menu open; LEFT doesn't turn the player) → B → 0.
  Opening the Pokédex from the menu: 1, with no position. Closing it: 0.
* **Talking to an NPC.** With the Pallet NPC at (11,17), standing at (12,17) facing LEFT: 0 before
  pressing A, 1 after A (text box; LEFT doesn't move the player).
* **Free steps.** One free step in 389 read 1: a single step right after arriving in Pallet,
  probably a map script.
* **Not verified.** A wild battle; `in_battle` covers battles anyway.

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

* **Stale values at the start of a battle.** `gBattleMons` and `gBattleOutcome` keep the
  previous battle's values. Test: before the rival battle starts, I wrote outcome = 1 and other
  species/HP into both slots. `in_battle` turned True at step 19, with `callback2` `0x08010509`.
  Raw outcome was still 1 until step 23, when `callback2` became `0x08011101` (battle main
  loop). The mons were refreshed one step later, at step 24. So the adapter only reports
  `player`, `opponent` and `outcome` once the current battle has shown its first action or move
  menu. Before that they are None. In the rival battle that menu first appears at step 279, so
  those keys are None during the intro text. The flag resets when `in_battle` goes False and on
  `reset()`.
* B advances battle text, the same as A.
* Gen 3 remembers the cursor between turns. Pressing past an edge does not wrap.
* **Losing the rival battle does not stop the story.** After a loss the player is back in the
  lab (4/3) at (7,8), the same as after a win: party_count 1, the rival is gone (8 -> 7 NPCs), and
  the player can walk to the exit mat (7,12). Of the 12 runs above, 6 were won and 6 lost.
* In this ROM both starters only know METRONOME, so the rival battle's result is random.
* Not verified yet, so not exposed in `ram["battle"]`: status, max_pp, battle type, turn, bag. The party
  (with status and max_pp per move) is now `ram["party"]`, see notes/party-and-icons.md.

### Wild battles (M3, Route 1)

Checked by Fullstack while doing M3; **Backend, please re-check**.

* In the first wild battle on Route 1 (PIDGEY Lv2/3), `menu` stayed `"other"` the whole time, so the adapter
  never marked the battle ready, and `player`/`opponent` stayed None. The reason:
  `gBattlerControllerFuncs[0]` (`0x03004FE0`) holds **different code addresses** than in the rival battle.
  * Action menu `0x0802E439`: the screen shows "What will BULBASAUR do?" with FIGHT/BAG/POKéMON/RUN.
  * Move menu `0x0802EA11`: shown after A on FIGHT ("METRONOME", PP 40/40, TYPE/NORMAL). B goes back
    to `0x0802E439`.
  * The two values in the rival battle (`0x080E763D`/`0x080E7989`) are probably FireRed's Oak/old-man
    tutorial controller, which the first battle uses. That is a guess from the address range; it is
    not checked against a symbol file.
* `gActionSelectionCursor[0]` `0x02023FF8` in the wild action menu: start 0; RIGHT 1, DOWN 3, LEFT 2,
  UP 0. That matches the 2×2 layout. `gMoveSelectionCursor[0]` was 0 with one move.
* `firered_battle.py` now maps both pairs: `MENU_BY_CTRL`, with `CTRL_CHOOSE_ACTION_WILD` and
  `CTRL_CHOOSE_MOVE_WILD`. With that, RuleBattleBrain gets mon data after the first menu and fights.
  In the boot run there were two wild battles (PIDGEY Lv3 and RATTATA Lv3), both won, and replay had
  0 mismatches.
* Still open for Backend: trainer battles other than the rival, and double battles.
* `outcome` raw **3** was seen when both mons fainted (METRONOME → SELFDESTRUCT; screenshot). The adapter
  reports `"unknown"`. In pokefirered, 3 is `B_OUTCOME_DREW`. **Backend:** please confirm it and map it to `"draw"`.
  It whites out the same as a loss (notes/nav.md "Whiteout"). Is the
  controller address the same for all normal battles?

## Pokédex owned/seen, and badges that are not verified

`ram["pokedex_owned"]` and `ram["pokedex_seen"]` (Kanto species ids) come from
`gSaveBlock2Ptr` (`0x0300500C`). `FLAG_SYS_POKEDEX_GET` was checked on the same saves and is
not a separate ram key. The eight Kanto badge bits were read as `0x00` and are **not** exposed:
no save has earned a badge, so a 0-to-1 was never seen. Measurements, raw bytes, and which
saves proved each field: `notes/badge-pokedex-ram.md`.
