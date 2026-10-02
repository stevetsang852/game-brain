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
| `npcs` (optional, M2) | list[dict] | `{x, y, prev_x, prev_y, elevation, local_id, gfx}`: other objects on this map |
| `party_count` (optional, M2) | int | Pokémon in the party (0 before the starter) |

`npcs` and `party_count` come from `adapters/gba_mgba/firered_extra.py`, a temporary reader
written for M2. It is **for Backend to take over** (see "Verified RAM (M2)" below). The adapter
calls it through one additive line in `MgbaFireRedAdapter.observe`.

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
row strings, not a rebuild. Since PR #9 (Backend), the run log stores the map keys only when
they change. `npcs` is logged every step: in the real M2 run the 1500-step log is ~3 MB,
mostly the lab's ten objects. Deduplicating that too is a possible follow-up for Backend
(logging is not changed here).

## PathBrain behaviour (`game_brain/brain/path.py`)

Each step, one Observation → one small Action:

1. **No position** (intro, menus, warp fade):
   * Right after we triggered a warp, or during a script (Oak's walk into the lab): wait 8
     frames, up to 20 times.
   * Otherwise: `BrainUnavailable`, so the next brain (`--brains path,rule` → RuleBrain) mashes A.
2. **New map** → wait 16 frames at a time until the position has been the same for 4
   observations. FireRed doors make you auto-walk one tile about 3 waits after arrival.
3. **Check the last action**:
   * A turn that didn't change `facing` means the player is frozen (fade-in, script, text box).
     Wait, and press the milestone's `script_button` (A, or B after the starter) every 2nd time.
   * A step that didn't move us: press `script_button` (maybe a text box) and retry. If the same tile fails
     twice, treat it as an obstacle (NPC). It is blocked for 12 decisions and we replan with A\*.
     If nothing is reachable with blocks, we replan once without them (the NPC may have moved).
4. **Plan**: A\* (4-dir, Manhattan heuristic, fixed tie-break order, so paths are deterministic)
   to the current milestone's target (a tile, or the stand tile of any usable warp to a map).
5. **Act**:
   * Not facing the next direction: a 2-frame tap + 8 released (FireRed turns first).
   * Facing it: hold 8 + release 16 frames (exactly one tile; 17+ frames would walk two).
   * On a warp's stand tile: hold `enter` 8 + release 32. After 3 tries without a map change,
     that warp is dropped.
6. **NPCs (M2)**: the current and previous tile of each entry in `ram["npcs"]` are blocked in A\*
   *before* any bump. If that leaves no path, the remembered bump-blocks are dropped first,
   then the NPC blocks; failing that, the brain walks up and bumps (bump → A → block → replan
   stays the fallback for obstacles the observation doesn't list).
7. **Scripts (M2)**: `Target.script()` (Oak walking you to the lab) presses the milestone's
   `script_button` every decision, at most `max_script_decisions` (600) times. A frozen player
   (a turn that doesn't take) waits, pressing `script_button` every 2nd decision, and gives up
   after `max_frozen` (400). Giving up means `BrainUnavailable`, so the fallback brain acts:
   no endless loops.
8. **Interact (M2)**: `Target.interact(x, y, face, button)` walks to (x, y), turns to `face` and
   presses `button` until the milestone is done (at most `max_interact_presses`, 200).
9. **Placeholder milestone**: first finish any open dialogue. Probe with a horizontal tap (which
   never moves a YES/NO cursor); if the turn doesn't take, press the milestone's `script_button`.
   Once a probe turns the player, idle.

`Decision` gets three optional fields, omitted when unset, so old logs still load:
* `goal` (str)
* `path` (`[[x, y], …]`, first = current position)
* `milestones` (`[{id, label, done}]`)

`milestones` is on **every** step's Decision. When PathBrain can't act (intro, menus), its
`BrainUnavailable` carries `context={"milestones", "goal"}`, and the arbiter copies that onto the
fallback brain's Decision. The dashboard (PR #10) relies on this.

### Goals (`game_brain/brain/goals.py`)

`GoalPlanner` keeps an ordered list of `Milestone(id, label, done(obs), target(obs), placeholder)`.
Once a milestone is done it stays done.

* **M1:** `intro` → `leave_bedroom` (warp to 4/0) → `leave_house` (warp to 3/0) → `pallet_town`.
* **M2:** `oak_stops_you` → `oak_lab` → `get_starter` (details below).
* **Rival:** `rival_battle` (target: the lab exit warp to 3/0, `script_button="B"`; done when
  `in_battle` is True) → `rival_battle_over` (done back in the overworld with `in_battle` False).
  PathBrain finishes the starter dialogue with B (B answers NO to "give a nickname?", so the
  naming screen never opens), then walks to the exit; the rival stops you at (7,8). The battle
  brain (or RuleBrain without one) plays the battle; PathBrain still sees every observation via
  `observe()` from the arbiter. Win or lose you are back at (7,8).
* **Next** (placeholder): `route_1`. PathBrain idles (`idle_on_placeholder=False` makes it report
  unavailable instead).

Done-detection uses only verified signals:

| milestone | target | done when |
|---|---|---|
| `oak_stops_you` | tile (12,1) on 3/0 | on 3/0 at (12,1) or (13,1), or in the lab, or `party_count` > 0 |
| `oak_lab` | `script` while on 3/0 | map 4/3 (Oak's lab), or `party_count` > 0 |
| `get_starter` | `interact((8,5), UP, A)` in the lab; lab door warp if back in Pallet | `party_count` ≥ 1 |

M1 milestones are also marked done on later evidence (in the lab, `party_count` > 0), so a run
started from a later save state doesn't get stuck on an earlier goal.

### M2 route (measured on this ROM)

* **Oak's trigger:** stepping from (12,2) onto (12,1) at Pallet's north exit freezes the player.
  (13,1) is the other exit tile. Oak walks up, then the game walks you
  (12,1) → (12,3) → (11,3) → … → (11,14) → (16,14) → (16,13) and through the lab door. Mashing
  A gets through it: about 80 presses / ~1300 frames from the trigger to the lab.
* **Lab (4/3):** you arrive at (6,12) and are walked to (6,4). Oak is at (6,3) and the rival at
  (5,4), both on `.` tiles. Then a long speech; mashing A ends it, and the player can move.
* **Starter balls:** object events (gfx 92) on table tiles: (8,4) = **Bulbasaur**, (9,4) =
  Squirtle, (10,4) = Charmander. Facing (8,4) from (8,5) and pressing A showed
  "I see! BULBASAUR is your choice". The rival then took Charmander from (10,4).
* **Why Bulbasaur:** it's strong against the first two gyms (Brock, Misty) and simple to
  use. `firered_milestones("CHARMANDER")` / `("SQUIRTLE")` picks another.
* **Prompt order after A on the ball:**
  1. text;
  2. "you want to go with the GRASS POKéMON BULBASAUR?" YES/NO, with the cursor on YES, so A = YES;
  3. text;
  4. "AAAAAAA received the BULBASAUR from PROF. OAK!". `party_count` becomes 1 when this
     text box opens;
  5. "Do you want to give a nickname…?" YES/NO. A here opens the naming screen (seen: it
     typed a letter), B = NO;
  6. rival text.

  Hence `script_button="B"` for everything after `party_count` = 1.
* **Not done here:** walking south of row 8 in the lab before the starter is stopped by a
  script, and after the starter the exit starts the rival battle. That's the next milestone,
  `rival_battle`, which needs a battle brain.

## Verified RAM (M2)

These are read by `adapters/gba_mgba/firered_extra.py`, which is **for Backend to take over**.
They were checked on this ROM (SHA1 `e0194282…`, not the clean 1.0 dump). Probe scripts and
save states stayed in `/tmp`.

**`gObjectEvents` 0x02036E38** (16 × 0x24 bytes; Backend already reads `facing` from it):

* Field layout:
  * active = bit 0 of byte 0;
  * graphicsId @+5, localId @+8, mapNum @+9, mapGroup @+0xA;
  * elevation = low nibble @+0xB;
  * currentCoords s16 @+0x10/+0x12 and previousCoords @+0x14/+0x16, both minus 7 (MAP_OFFSET).
* The player's slot comes from `gPlayerAvatar.objectEventId` (already verified by Backend); it
  has localId 255.
* Method:
  1. Dumped all active slots in Pallet Town and the lab. The positions match the screen: Oak
     behind the player, the rival to his left, three balls on the table.
  2. For every NPC on a `.` tile with a reachable neighbour, walked to the neighbour and stepped
     into it. The step was **blocked** each time:
     * Pallet girl (5,15) from (5,16);
     * lab rival (5,4) from (5,5);
     * Oak (6,3) from (6,4);
     * after the starter, the rival at (10,5) from (10,6).
  3. Wandering NPCs: `currentCoords` ≠ `previousCoords` mid-step, so both tiles are blocked
     (FireRed's collision check tests both).
* **A\* effect:** from the lab state at (6,4), going to (4,4) with the rival at (5,4) in the
  straight line:
  * with `npcs`: 0 bumps, route (6,5) → (5,5) → (4,5) → (4,4) in 11 decisions;
  * with `avoid_npcs=False`: 6 bumps, and still at (6,4) after 59 decisions. Pressing A at the
    rival talks to him, which re-freezes the player.

**`gPlayerPartyCount` 0x02024029 (u8):**

* It is 0 in every state before the starter (house, Pallet, lab).
* It becomes 1 exactly when "received the BULBASAUR" appears.
* Cross-check: `gPlayerParty` 0x02024284 slot 0 has a non-zero personality and level 5. With the
  gen-3 substructure decryption (key = personality ^ otId, order = personality % 24), Growth
  gives species 1 (Bulbasaur), and the nickname bytes spell "BULBASAUR" (not renamed). The
  real-ROM pytest asserts all of this after replaying its log.

Not verified / not used: the script-lock flag, the text-box state, event flags. Scripts are
detected by behaviour instead ("a turn didn't take"), so no RAM is needed for them.

## How it was checked

* **Synthetic:**
  * `tests/test_nav.py` covers A\* (walls, unreachable, blocked/replan, several goals,
    determinism), warp kinds, and `RamMapProvider` parsing/caching. It also covers the M2
    planner order and done-detection, and `firered_extra.read_extra` on fake memory.
  * `tests/test_path_brain.py` covers turn-before-move, bump → A → block → replan, frozen → wait
    → give up, untriggerable warps, placeholder "finish dialogue then idle", NPC tiles blocked
    before bumping (incl. walking NPCs), script and interact targets with budgets, and the
    arbiter copying `milestones` onto the fallback brain's Decision.
  * End-to-end `MockHouseAdapter` run: 2F → 1F (unlisted NPC: bump/replan; text box) →
    outside → Oak trigger → script → lab (listed NPCs never bumped) → ball → YES → B for the
    nickname → idle. Replay has zero mismatches.
* **Real ROM:** `tests/test_path_brain.py::test_real_firered_pathbrain_gets_starter` (skipped
  without bindings + ROM).
  * It runs `--brains path,rule` for 1100 steps from power-on and asserts:
    * the map order 4/1 → 4/0 → 3/0 → 4/3, ending in the lab with `party_count` 1 in the
      overworld (no naming screen);
    * `milestones` on every step, and the idle reason on the last step;
    * zero replay mismatches;
    * after the replay: Bulbasaur, not renamed (decrypted party slot 0).
  * Timings (turn tap, 1–16 frame hold = 1 tile) were measured on this ROM by holding each
    direction for 1–32 frames from a save state and reading the position after.

## Not handled yet

* One-way ledges and direction-blocked tiles. During exploration, two tiles in the 2F room
  showed `.` but couldn't be crossed in one direction. The contract has no field for this yet,
  so PathBrain just bumps and replans. Details went to Backend.
* Water / surf tiles that show as `.`. In Pallet Town the south water has collision 0, but you
  can't walk on it.
* Map connections (walking off the edge of an outdoor map into the next one).
* Battles: PathBrain does not wait through the ~20-step pre-battle transition (no position, `in_battle`
  still False); RuleBrain presses A there.
* Scripts are handled by "press A/B while frozen". A script that needs a menu answer other than
  the default (YES) would need its own milestone.
* Moving NPCs are handled only by bump-and-replan.
