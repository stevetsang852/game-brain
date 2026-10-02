# Save state format (save / resume)

> **For Backend review:** the gym wrapper's `reset()` should line up with this format. The
> sidecar fields below are the contract; `adapter_state` and `party_hp` especially need your eyes.

Code: `game_brain/savestate.py`; CLI in `game_brain/demo.py`. The dashboard (`live.py`) does not
use it yet; that is Frontend's.

## Where saves go

* The default is `~/.game-brain/saves`, or `$GAME_BRAIN_SAVE_DIR`. Change it with `--save-dir DIR`.
* **Never inside the repo** (the repo is public, and states are ROM-derived game data).
  * `check_save_dir` refuses a dir inside the git work tree of the package or of the cwd.
  * The CLI then exits 2 with an error.
  * `.gitignore` also covers `*.ss*`, `*.sav`, `*.state`, `saves/`, `.game-brain/`, `*.tmp` and
    model files.
* Layout: `<save_dir>/<run_id>/<step:07d>_<reason>.{state,json[,sav]}` plus `<save_dir>/latest`.
  * `<run_id>` is the run's timestamp, the same as the `runs/<run_id>/` log dir.
  * `latest` is a text file holding the absolute path of the newest sidecar.

## When a save is written

| reason | when |
|---|---|
| `milestone-<id>` | after the step on which milestone `<id>` became done (the last one, if several). Milestones already done on the first step of a run don't count |
| `periodic` | every `--save-every N` steps (default 500; `0` turns it off). Skipped when a milestone save happens on the same step |
| `final` | at the normal end of the run |

`--no-save` turns saving off. The `demo.run()` API saves only when `save_dir` is given, so tests
never write to `~`. Adapters without save states (`mock`, `mock-battle`) skip saving with a warning.

Every file is written to `*.tmp`, fsynced, then renamed. The `.state` (and `.sav`) are written
first, the sidecar after, then `latest`. So if the process is killed during a save, there is no
sidecar for the half-written state, and `latest` still points at the previous complete save.

## Files

* **`.state`** (primary): the adapter's emulator snapshot.
  * mGBA: `core.save_raw_state()`, the same bytes as Backend's `bh.save()` and
    `GAME_BRAIN_START_STATE`.
  * mock-house: a Python literal of the mock world (tests only, loaded with `ast.literal_eval`).
* **`.sav`** (backup only, optional): the in-game battery save, written only if the game has saved.
  * Only the in-game SAVE menu writes it; we never do. The `.state` is what gets restored.
  * The mGBA adapter gives the core an **in-memory** battery file, empty by default, so nothing
    is written next to the ROM.
  * Boot is identical with that empty file and with no save loaded. Checked on this ROM: the EWRAM
    hash after 3000 frames of the same input is the same.
  * Blank flash (all 0xFF/0x00) means "no .sav".
  * On `--resume`, a `.sav` next to the sidecar is loaded into that in-memory file, so a later
    in-game save keeps working.
  * Not exercised on the real runs: the AI never uses the SAVE menu, so no `.sav` was produced.
    The load/read path was checked with a 128 KiB test pattern (round trip equal).
* **`.json` sidecar** (`format: "game-brain-savestate"`, `format_version: 1`):

| field | meaning |
|---|---|
| `state_file`, `state_sha1` | the `.state` next to it, and its SHA1 (checked on resume and replay) |
| `sav_file`, `sav_sha1` | the `.sav`, or `null` |
| `reason`, `new_milestones` | why it was saved |
| `step` | number of steps executed so far, which is also the step number the resumed run starts at |
| `frame` | adapter frame counter at the save. The resumed run continues counting from here |
| `scene`, `in_battle`, `map_bank`, `map_id`, `x`, `y`, `facing` | from `observe()` at the save moment. Position is `null` in battle or during a transition |
| `milestone` | current (first not-done) milestone id |
| `milestones_done` | ids of done milestones |
| `party_count` | `ram["party_count"]` |
| `party_hp` | `[{hp, max_hp}]` from `ram["party"]` if the adapter has it (it doesn't yet: Backend's party work). Otherwise, in battle, the active battler from `ram["battle"]["player"]` with `"source": "battle"`. Otherwise `null` |
| `adapter`, `adapter_state` | adapter name, plus adapter-side state that is not in the emulator snapshot. mGBA: `{"battle_ready": bool}` (whether this battle's mon data is trusted yet, see notes/mgba-bridge.md). Without it, resuming mid-battle would hide `ram["battle"]` HP until the next menu |
| `rom_sha1` | SHA1 of the ROM. Resume refuses a different ROM |
| `brains`, `git_commit` (`-dirty` if uncommitted changes), `run_id`, `resumed_from` (sidecar this run resumed from), `timestamp` (local ISO 8601 with offset) | provenance |

## Resume

`--resume PATH|latest`: `PATH` is a sidecar `.json` or its `.state`, and `latest` means
`<save_dir>/latest`. Steps:

1. Check that the adapter name and ROM SHA1 match the sidecar, and the state SHA1 matches the file.
2. `adapter.reset()`, then `adapter.load_state(bytes, frame=sidecar.frame, adapter_state=…)`.
3. **Milestones:** every brain with a `planner` (PathBrain) gets `planner.restore(milestones_done)`.
   PathBrain also recomputes from observations, as before.
4. Step numbering continues at `sidecar.step`. `--steps N` runs N more steps.
5. The run log records it:
   * the header has `resumed_from` = `{sidecar, state, state_sha1, step, frame, adapter_state, sav, milestone}`;
   * there is a `{"kind": "resumed", …}` event;
   * every save of the resumed run has `resumed_from` in its sidecar.
   * Saves are also logged as `{"kind": "save", step, frame, reason, path}` events.
6. `runlog.replay()` sees `resumed_from` in the header and loads that state (SHA1 checked) before
   replaying, so resumed logs replay too.

Brain-internal state is **not** saved: PathBrain's settle and bump counters, RuleBattleBrain's
"trusted this battle" flag. So the first resumed decisions can differ from an uninterrupted run:
* PathBrain waits for the position to settle once.
* RuleBattleBrain, resumed mid-battle, presses B ("not trusted yet") until the next input menu.
  That is the same button it uses for text anyway.

The game state at the resume moment is identical (tested). The run after it is deterministic for
its own log (replay 0 mismatches), but not step-for-step the same as the run that was killed.

## Real-ROM evidence (2026-10-02, Asia/Taipei)

| | |
|---|---|
| run A | boot, `--brains battle,path,rule --save-every 250 --save-dir /tmp/…`. Saves at every milestone up to `route_1` (step 2315) and every 250 steps. Killed with `kill -9` right after `0002500_periodic.json` appeared (log had reached step 2501) |
| save 2500 | in a wild battle on Route 1 (Pidgey L3): `party_hp` 25/25, frame 31970, `battle_ready` true, milestone `viridian_city` |
| run B | `--resume latest --steps 900`. First step 2500, frame 31970, `observation.ram` **identical** to run A's step 2500 (HP 25/25). Won that battle (ended step 2843, frame 37458, same as the uninterrupted boot run), back on 3/19 (12,27), won another wild battle, then **Viridian City 3/1 (25,39) at step 3188** (frame 42962). Replay: 0 mismatches |
| run C | `--resume …/0002315_milestone-route_1.json --steps 30`. First observation identical to run A's step 2315: 3/19 (13,39). Replay: 0 mismatches |

`tests/test_savestate_real.py` repeats this in tmp_path (boot to 2510 steps, resume from the
2500 save, 800 steps to Viridian, replay []).
