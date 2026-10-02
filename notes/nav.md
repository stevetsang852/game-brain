# Navigation: MapProvider contract + PathBrain

## Contract: map data in `Observation.ram`

PathBrain is game-agnostic. It reads the current map from the observation through
`game_brain.nav.RamMapProvider`, which needs these `ram` keys (the FireRed adapter emits
them since PR #7; `MockHouseAdapter` emits the same):

| key | type | meaning |
|---|---|---|
| `map_bank`, `map_id` | int | current map |
| `player_x`, `player_y` | int | player tile, same coordinates as `collision` |
| `facing` | str | `UP` / `DOWN` / `LEFT` / `RIGHT` (used for turn-before-move) |
| `map_w`, `map_h` | int | map size in tiles |
| `collision` | list[str] | `map_h` rows of `map_w` chars: `#` blocked, `.` free |
| `warps` | list[dict] | `{x, y, dest_bank, dest_map, behavior, enter}` |

`enter` is the button that triggers the warp, or `null` if unknown or untriggerable. Warps with
`enter: null` are ignored; FireRed's event table lists some that never fire, e.g. 1F (5,8) and
(3,9). PathBrain derives the warp kind from `enter` and the warp tile's collision:

* **push**: the warp tile is `.`. Stand on it, then press `enter` (door mats, stairs).
* **door**: the warp tile is `#`. From the tile behind it (warp − `enter` direction), walk
  `enter` into it.

Missing keys, or rows that don't match `map_w`/`map_h`, mean "map unknown" (`current_map() → None`).
PathBrain then reports itself unavailable and the next brain acts.

Other games/emulators can either emit the same keys or implement `MapProvider.current_map() ->
MapGrid` directly (`MapGrid`: `map_bank, map_id, width, height, walkable[y][x], warps`).

**Caching:** `RamMapProvider` keeps one parsed `MapGrid` per `(map_bank, map_id)` and only
re-parses when that map's rows/warps change. Each step costs a cheap equality check on the
row strings, not a rebuild. The run log still stores `collision` every step (about 544 KB per
700 steps). Trimming that is Backend's call and is not changed here.

## PathBrain behaviour (`game_brain/brain/path.py`)

Each step, one Observation → one small Action:

1. **No position** (intro, menus, warp fade):
   * Right after we triggered a warp: wait 8 frames, up to 20 times.
   * Otherwise: `BrainUnavailable`, so the next brain (`--brains path,rule` → RuleBrain) mashes A.
2. **New map** → wait 16 frames at a time until the position has been the same for 4
   observations. FireRed doors make you auto-walk one tile about 3 waits after arrival.
3. **Check the last action**:
   * A turn that didn't change `facing` means the player is frozen (fade-in, script, text box).
     Wait, and press A only every 4th time.
   * A step that didn't move us: press A (maybe a text box) and retry. If the same tile fails
     twice, treat it as an obstacle (NPC). It is blocked for 12 decisions and we replan with A\*.
     If nothing is reachable with blocks, we replan once without them (the NPC may have moved).
4. **Plan**: A\* (4-dir, Manhattan heuristic, fixed tie-break order, so paths are deterministic)
   to the current milestone's target (a tile, or the stand tile of any usable warp to a map).
5. **Act**:
   * Not facing the next direction: a 2-frame tap + 8 released (FireRed turns first).
   * Facing it: hold 8 + release 16 frames (exactly one tile; 17+ frames would walk two).
   * On a warp's stand tile: hold `enter` 8 + release 32. After 3 tries without a map change,
     that warp is dropped.

`Decision` gets three optional fields, omitted when unset, so old logs still load:
* `goal` (str)
* `path` (`[[x, y], …]`, first = current position)
* `milestones` (`[{id, label, done}]`)

### Goals (`game_brain/brain/goals.py`)

`GoalPlanner` keeps an ordered list of `Milestone(id, label, done(obs), target(obs), placeholder)`.
Once a milestone is done it stays done.

* **M1:** `intro` → `leave_bedroom` (warp to 4/0) → `leave_house` (warp to 3/0) → `pallet_town`.
* **M2** (placeholders, not implemented): `oak_lab`, `get_starter`, `first_battle`. While one is
  current, PathBrain idles rather than let a fallback brain wander back inside
  (`idle_on_placeholder=False` makes it report unavailable instead).

## How it was checked

* **Synthetic:** `tests/test_nav.py` covers A\* (walls, unreachable, blocked/replan, several goals,
  determinism), warp kinds, and `RamMapProvider` parsing/caching. `tests/test_path_brain.py`
  covers turn-before-move, bump → A → block → replan, frozen → wait, untriggerable warps, and
  placeholder idling. It also has an end-to-end `MockHouseAdapter` run (2F stairs → 1F with an
  NPC on a "free" tile + a text box → door mat → outside) with zero replay mismatches.
* **Real ROM:** `tests/test_path_brain.py::test_real_firered_pathbrain_reaches_pallet_town`
  (skipped without bindings + ROM). It runs `--brains path,rule` for 650 steps, asserts the map
  order 4/1 → 4/0 → 3/0 and ends on 3/0, then replays the log with zero mismatches.
  Timings (turn tap, 1–16 frame hold = 1 tile) were measured on this ROM by holding each
  direction for 1–32 frames from a save state and reading the position after.

## Not handled yet

* One-way ledges and direction-blocked tiles. During exploration, two tiles in the 2F room
  showed `.` but couldn't be crossed in one direction. The contract has no field for this yet,
  so PathBrain just bumps and replans. Details went to Backend.
* Water / surf tiles that show as `.`. In Pallet Town the south water has collision 0, but you
  can't walk on it.
* Map connections (walking off the edge of an outdoor map into the next one).
* Scripted events: Oak stops you at Pallet Town's north exit, freezing the player. PathBrain
  would wait / press A, which is untested.
* Moving NPCs are handled only by bump-and-replan.
