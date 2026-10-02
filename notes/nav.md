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
* **M3:** `leave_lab` (exit warp to 3/0) → `route_1` (`Target.edge("UP")` on Pallet Town) →
  `viridian_city` (`Target.edge("UP")` on Route 1). Wild battles in Route 1's grass are fought by
  the battle brain. If the starter faints (whiteout) you wake up at home, and the targets lead back
  out: 2F → 1F → Pallet → north. This was not seen in the runs so far; every battle was won.
* **Oak's Parcel:** `viridian_mart` (warp to 5/3 in Viridian) → `oaks_parcel` (leave the mart) →
  `back_to_pallet` (`Target.edge("DOWN")` on Viridian and Route 1) → `deliver_parcel` (lab door,
  then `interact((6,4), UP, A)` on Oak). See "Oak's Parcel (measured on this ROM)" below.
* **Next** (placeholder): `pewter_city`. PathBrain finishes any open dialogue, then idles
  (`idle_on_placeholder=False` makes it report unavailable instead).

### Whiteout (verified on the ROM)

How it was checked: `tests/test_whiteout_real.py`.
* The harness boots with `battle,path,rule` up to the first wild battle's action menu on Route 1.
* Then a **test-only RAM write** sets `gBattleMons[0].hp` to 1, and the harness saves a state to a tmp dir (never committed).
* The run under test starts from that state, so replay from the same state needs no write.

Results, starting from that state:

* **Loss:** PIDGEY Lv3 knocks out BULBASAUR; `outcome` is `"lose"`.
  * You wake up in the **player's house 1F (4/0) at (8,5)**. Mom talks ("MOM: AAA …"), and PathBrain's frozen handling presses through it (about 240 steps).
  * `party_count` stays 1. **HP is restored:** the next battle starts at 25/25.
* **Draw:** in the same run, a second, *natural* whiteout happened. METRONOME called SELFDESTRUCT and both mons fainted (screenshots).
  * Raw `gBattleOutcome` is **3** there, so the adapter reports `"unknown"`. In pokefirered, 3 is `B_OUTCOME_DREW`; Backend should add a `"draw"` label.
  * A draw also whites you out to 4/0 (8,5).
* **Recovery:** the `viridian_city` targets lead out of the house (1F warp → Pallet → north edge → Route 1). The run reached **Viridian City 3/1 (25,39) at step 2103** after both whiteouts. Replay had 0 mismatches.
* **Planner fix:** the starter outside the lab, *including at home*, now implies the rival battle is over. Before, a run resumed mid-battle woke up at home stuck on `rival_battle` (no target), and RuleBrain wandered.

### Map connections (M3)

* There is no warp between Pallet Town and Route 1, or between Route 1 and Viridian City; they are
  map connections. The adapter does **not** read the connection list (`gMapHeader.connections`),
  so PathBrain does not know where an edge leads.
* What *is* verified and is enough: walking off the map edge changes `map_bank`/`map_id`
  straight away. There is no blackout, and position is never None. The new position is in the new
  map's coordinates.
  * Pallet Town (13,0) UP → 3/19 (13,39).
  * Route 1, top row, UP → 3/1 (25,39). Viridian City is 48×40; the screenshot shows the
    town.
* `Target.edge(direction)`:
  * A\* goes to the nearest walkable tile on that edge, then PathBrain faces out and steps off.
  * If the map does not change after 3 presses (`max_warp_tries`), that edge tile is dropped.
    With none left, PathBrain reports unavailable.
* Which edge to take is game knowledge in the milestone ("Route 1 is north of Pallet"), checked by
  the map id on arrival. No RAM address is guessed.
* **For Backend (later):** reading `gMapHeader.connections` (direction, offset, dest map) would
  let PathBrain plan across maps without hard-coding directions. Not needed for M3.
* Route 1 ledges show as `#` in `collision`, which is right for walking north (you can't climb a
  ledge). Walking south would need one-way handling.
* Pallet Town after the rival: a girl NPC at (12,2) next to the exit talks to you ("Look, look!
  ... TRAINER TIPS"). PathBrain's frozen handling (A every other decision) gets through it.

Done-detection uses only verified signals:

| milestone | target | done when |
|---|---|---|
| `oak_stops_you` | tile (12,1) on 3/0 | on 3/0 at (12,1) or (13,1), or in the lab, or `party_count` > 0 |
| `oak_lab` | `script` while on 3/0 | map 4/3 (Oak's lab), or `party_count` > 0 |
| `get_starter` | `interact((8,5), UP, A)` in the lab; lab door warp if back in Pallet | `party_count` ≥ 1 |

M1 milestones are also marked done on later evidence (in the lab, `party_count` > 0), so a run
started from a later save state doesn't get stuck on an earlier goal.

### Oak's Parcel (measured on this ROM)

Evidence: save states and screenshots made at runtime in a temp dir (not committed), plus the
from-boot run below.

* **Mart:** the Viridian Mart door is the warp at (36,19) on 3/1 → 5/3. You arrive on the exit
  warp tile (4,7), and the clerk's script starts at once ("Hey! You came from PALLET TOWN?"). The
  game walks you to the counter (4,3), facing LEFT. About 20 A presses later the text reads
  "… received OAK'S PARCEL", and you are free. The exit is the warp (4,7), `enter` DOWN.
  * PathBrain change: on arrival it stands on the exit warp and presses DOWN, but you can't even
    turn (still facing UP). Before, that counted as a failed warp try, and after 3 the exit was
    "dead". Now "pressed the warp direction, same tile, facing ≠ `enter`" is treated as a freeze:
    wait / press the script button, like other freezes (`max_frozen` cap).
  * **Parcel detection:** the bag / key-item RAM is **not verified**, so no address is read.
    `oaks_parcel` is done when you leave the mart (the script always runs on the first visit, and
    you can't leave before it ends). This is scene/position evidence, not an item check.
* **Going south (Route 1):** ledges are `#` in `collision`, so A\* just takes the non-ledge path
  south. In the run there were only 2 "no movement" bumps (NPC / text), and no ledge jumps.
  **Ledge behaviour values (0x32/0x33 etc.) were not needed, so they were not measured.** They
  would be needed to *use* a ledge as a one-way shortcut. Flagged for Backend; nothing assumed.
* **Lab:** Oak is `npcs` local_id 4 at (6,3). Stand at (6,4), face UP, press A. Script: the rival
  (local_id 8) walks in to (5,4), and Oak walks to the table (5,2). About 108 A presses in, the
  two Pokedex objects on the table (local_id 9 and 10, gfx 94, at (4,1)/(5,1)) disappear from
  `npcs` (the screenshot shows the empty table). About 7 presses later the text reads "… received
  the POKéDEX", followed by five POKé BALLS and a long speech. After that you are free again
  (~5 presses after the brain's old 100-press placeholder cap, so the cap is now 200).
  * **Pokedex detection:** the Pokedex flag is **not verified**, so no address is read.
    `deliver_parcel` is done when, in the lab, Oak (local_id 4) is in `npcs`, the player is at
    y ≤ 5, and neither local_id 9 nor 10 is listed.
  * `npcs` only lists objects near the camera. Measured: Oak at y=3 appears once you are at
    y ≤ 10, so the table at y=1 needs y ≤ ~8. Without the y check, the brain wrongly marked the
    milestone done at the lab entrance (6,10), before talking to Oak.
* **Save / resume (notes/savestate-format.md):** each new milestone gets its own milestone save
  (`milestone-viridian_mart`, `-oaks_parcel`, `-back_to_pallet`, `-deliver_parcel`). After the mart
  the map no longer says whether you have the parcel; only the sidecar's `milestones_done` says so,
  and `--resume` restores it. Measured on the ROM (2026-10-02):
  * resume from `milestone-oaks_parcel` (3/1 (36,19), step 3268): first observation identical
    to the boot run; delivered at step 3944, the same step as the uninterrupted run; replay [].
  * resume from `milestone-viridian_mart` (5/3 (4,7), step 3167, clerk script pending): parcel,
    back to Pallet at step 4064, delivered at step 4279 (different wild battles); replay [].
  * The mart also counts as "left the lab" (`left_lab`), so a save state in the mart without a
    sidecar does not send PathBrain back to the rival battle.
* **Whiteout during the errand:** you wake up at home. `to_viridian` / `to_pallet` lead back
  (house → Pallet → north before the mart; → Pallet → lab after it). This is a unit test only;
  no whiteout happened on the parcel errand in the real runs.

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
  use. `firered_milestones("CHARMANDER")` / `("SQUIRTLE")` picks another; from the CLI / dashboard:
  `--starter charmander|squirtle|random` (game_brain/setup.py). The rival always takes the counter
  starter (Bulbasaur → Charmander, Charmander → Squirtle, Squirtle → Bulbasaur), read from the
  rival battle's species on the ROM; see the `--starter` PR for full runs with each starter.
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

* One-way ledges (jumping down as a shortcut; south of Viridian A\* just avoids them) and
  direction-blocked tiles. During exploration, two tiles in the 2F room
  showed `.` but couldn't be crossed in one direction. The contract has no field for this yet,
  so PathBrain just bumps and replans. Details went to Backend.
* Water / surf tiles that show as `.`. In Pallet Town the south water has collision 0, but you
  can't walk on it.
* Map connections are only handled as "walk off this edge" (see "Map connections (M3)"); there
  is no cross-map route planning.
* Battles: PathBrain does not wait through the ~20-step pre-battle transition (no position, `in_battle`
  still False); RuleBrain presses A there.
* Scripts are handled by "press A/B while frozen". A script that needs a menu answer other than
  the default (YES) would need its own milestone.
* Moving NPCs are handled only by bump-and-replan.
