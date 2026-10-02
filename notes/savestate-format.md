# Save state format (save / resume)

> **For Backend review:** the gym wrapper's `reset()` should line up with this format. The
> sidecar fields below are the contract; `adapter_state` and `party_hp` especially need your eyes.

Code: `game_brain/savestate.py`; CLI in `game_brain/demo.py`. The dashboard (`live.py`) does not
use it yet; that is Frontend's.

> **Planned v2** (docs only, not implemented yet): `brain_state` in every save, plus a separate
> `ai_status` file kind. See "Format version 2" at the end, and the dashboard messages in
> [`dashboard-protocol.md`](dashboard-protocol.md) "存檔 / 續玩".

## Where saves go

* The default is `~/.game-brain/saves`, or `$GAME_BRAIN_SAVE_DIR`. Change it with `--save-dir DIR`.
* **Never inside the repo** (the repo is public, and states are ROM-derived game data).
  * `check_save_dir` refuses a dir inside the git work tree of the package or of the cwd.
  * The CLI then exits 2 with an error.
  * `.gitignore` also covers `*.ss*`, `*.sav`, `*.state`, `saves/`, `.game-brain/`, `*.tmp` and
    model files.
* Layout: `<save_dir>/<run_id>/<step:07d>_<reason>.{state,json[,sav]}` plus `<save_dir>/latest`.
  * `<run_id>` is the run's timestamp, the same as the `runs/<run_id>/` log dir.
  * `latest` is a text file holding the path of the newest sidecar **relative to `<save_dir>`**
    (`<run_id>/<step:07d>_<reason>.json`), so the save dir can be moved or mounted elsewhere
    (Docker writes `/saves`, the host sees `~/.game-brain/saves`).
  * Older pointers hold an absolute path. On `--resume latest` it is still accepted if it is an
    existing file inside `<save_dir>`; an absolute path from elsewhere (e.g. `/saves/<run_id>/<file>`
    from inside the container) is mapped to `<save_dir>/<run_id>/<file>` if that exists. A
    relative pointer that escapes `<save_dir>` (`..`) is ignored. If the pointer can't be used,
    the newest sidecar by mtime is used, as before.

## When a save is written

| reason | when |
|---|---|
| `milestone-<id>` | after the step on which milestone `<id>` became done (the last one, if several). Milestones already done on the first step of a run don't count |
| `periodic` | every `--save-every N` steps (default 500; `0` turns it off). Skipped when a milestone save happens on the same step |
| `final` | at the normal end of the run |

**Retention (`--keep-periodic N`, default 10):** after each periodic save, only the newest N
periodic saves of the current run directory are kept. Older ones are deleted with their `.state`
and `.sav` (sidecar first, so an interrupted prune never leaves a sidecar without its state). Only
sidecars whose `reason` is exactly `"periodic"` are candidates: milestone saves, the final save
and any other reason (`manual`, `pre-resume`, Go-Explore cells, …) are **never** deleted, nor is
the save `latest` points at, nor anything in other runs' directories. `0` keeps everything.
Deletions are logged as `{"kind": "save_pruned", step, path}` events.

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
| `starter` | `{"requested": "random"\|"bulbasaur"\|"charmander"\|"squirtle", "picked": <name> or null, "seed": int}`. `picked` is null until the ball is taken in Oak's lab (the `get_starter` milestone save already has it). `--resume` keeps it: the ball is `picked`, or (not picked yet) `requested` resolved with the recorded `seed`, so it is never re-rolled; an explicit different `--starter` is ignored with a warning. Missing (saves from before `--starter`) = `{"requested": "bulbasaur", "picked": "bulbasaur" if get_starter is done else null}` |
| `seed` | the run's seed (`--seed`, or the one drawn at random when `--starter random` is used without `--seed`). `--resume` without `--seed` reuses it (and the starter keeps its own `starter.seed`) |
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

## Format version 2（計劃中，未實作）

> 狀態：**只係規格**。由 Mannger／Frontend／Backend 定案（2026-10-02），**Backend 請 review**。
> 實作：Fullstack（`savestate.py`、每個 brain 嘅 `export_state()`／`import_state()`）；
> Dashboard 訊息同按鈕：Frontend（[`dashboard-protocol.md`](dashboard-protocol.md)「存檔 / 續玩」）。

### 點解要 bump 版本

v1 冇 brain 內部狀態，所以續玩之後頭幾步 AI 會同冇中斷過嘅 run 唔同（上面 "Brain-internal state is
not saved"）。v2 喺每個存檔加 `brain_state`。#28 嘅 reader 會拒絕 `format_version > 1`，所以舊程式唔會
誤讀新檔；新程式 v1、v2 都讀得。

### 遊戲存檔（`kind: "game"`）

目錄結構、檔名、寫入次序（`.state` → `.sav` → sidecar → `latest`）、原子寫入**全部唔變**。
`--save-every`、里程碑、`final`、dashboard「存遊戲」按鈕（`reason: "manual"`）、resume 前自動存
（`reason: "pre-resume"`）全部用**同一個** `SaveManager.save()`（包括 `adapter_state`）。

Sidecar 喺 v1 欄位之外加：

| 欄位 | 內容 |
|---|---|
| `format_version` | `2` |
| `kind` | `"game"`（v1 冇呢個欄位 = 當 `"game"`） |
| `label` | 按鈕存檔嘅標籤（可選，≤ 64 字元），否則 `null` |
| `settings` | 見下面「設定」 |
| `brain_state` | 見下面「`brain_state` schema」 |

### AI 狀態檔（`kind: "ai_status"`）

* 位置：`<save_dir>/ai_status/<ai_status_id>.ai.json`，`ai_status_id` = `ai_<YYYYmmdd-HHMMSS>_<step:07d>`
  （同一秒再存就加 `-2`…）。冇 `.state`：**唔包括遊戲**。
* 內容：

```json
{
  "format": "game-brain-ai-status", "format_version": 2, "kind": "ai_status",
  "ai_status_id": "ai_20261002-153000_0003268", "label": "包裹後",
  "timestamp": "2026-10-02T15:30:00+08:00",
  "source": {"run_id": "...", "save_id": null, "step": 3268, "frame": 44046,
             "rom_sha1": "e019...", "git_commit": "..."},
  "brains": ["battle", "path", "rule"],
  "settings": {"battle_confidence": 0.6, "handoff_steps": 20, "allow_run": false},
  "milestone": "back_to_pallet",
  "milestones_done": ["intro", "...", "oaks_parcel"],
  "brain_state": {"...": "同遊戲存檔一樣嘅 schema"}
}
```

* 套用：`resume_request {save_id, ai_status_id}` = 載入 `save_id` 嘅遊戲 state，再用 `ai_status` 嘅
  `brain_state`、`milestones_done`、`settings`（蓋過遊戲存檔自己嘅）。`brains`（名同次序）一定要同
  目前個 run 一樣，否則 `ai_status_incompatible`。ROM 唔同都套得（AI 狀態唔含遊戲資料），但回覆會講明。
* 里程碑預設跟 `ai_status` 走（Mannger 決定，刻意設計）；`resume_request` 可以帶 `milestones: "save"` 改用
  遊戲存檔 sidecar 嘅 `milestones_done`（喺 `import_state` 之後蓋過 PathBrain `planner`）。`milestones` 只可以同
  `ai_status_id` 一齊用，否則 `bad_request`。`resumed` 回覆會講明實際用咗邊邊。頁面要喺套用前並排顯示兩邊
  `milestones_done`、唔同就警告、俾用戶揀（見 [`dashboard-protocol.md`](dashboard-protocol.md)「里程碑：跟 AI 狀態定跟存檔」）。
* `list_saves` 嘅 `game[]` 同 `ai_status[]` 每項都帶完整 `milestones_done`（照抄檔案入面嘅 list）。

### 設定（`settings`）

由 CLI／dashboard 啟動參數嚟、會影響決策嘅值：`battle_confidence`（RuleBattleBrain
`confidence_threshold`）、`handoff_steps`、`allow_run`、`seed`、`starter`。`starter` 例外：佢係遊戲狀態嘅一部分（揀咗就改唔到），所以續玩一定**套用**遊戲存檔嘅 `starter`（v1 已經喺 sidecar 頂層有 `starter`）。續玩時：遊戲存檔嘅 `settings` 只係**紀錄**
（用目前啟動參數）；`ai_status` 嘅 `settings` 會**套用**（比較「同一個 AI」就要同一套門檻）。

### `brain_state` schema（v2）

```json
{
  "schema": 1,
  "arbiter": {"mode": "auto"},
  "brains": [
    {"name": "battle", "class": "RuleBattleBrain", "state": {...}},
    {"name": "path",   "class": "PathBrain",       "state": {...}},
    {"name": "rule",   "class": "RuleBrain",       "state": {...}}
  ]
}
```

* 每個 brain 加兩個方法：`export_state() -> dict`（JSON-safe）同 `import_state(state: dict) -> None`。
  * JSON 化規則：tuple → list；set → 排好序嘅 list；key 唔係字串嘅 dict（例如 `(map, tile)`）→
    `[[key, value], …]`。
  * `import_state` 先 `reset()` 再填；**未知欄位忽略、缺少欄位用 reset 值**，唔會 raise。
  * 冇實作 `export_state` 嘅 brain（例如 LLM stub）→ `state: null`，續玩時只 `reset()`。
  * `name`／`class` 唔夾（例如 brains 次序改咗）→ 嗰個 brain `reset()`，寫 log warning。
* Arbiter：`mode` 只係紀錄；續玩用目前模式（dashboard）或 `--mode`（CLI）。
* 每個 brain 存咩（大綱；實作時以 code 為準，加欄位唔使 bump `schema`）：

| brain | `state` |
|---|---|
| PathBrain | `planner.milestones_done`；`t`（決策計數，`blocked` 嘅到期值係相對佢）、`map`、`last_pos`、`last`（上一個動作 kind/detail）、`blocked`（`[[[map],[tile]], expiry]`）、`warp_tries`/`dead_warps`、`edge_tries`/`dead_edges`、`fails`、`turn_fails`、`transition_waits`、`settle_waits`、`settling`、`stable`、`script_n`、`free`、`stats` |
| RuleBattleBrain | `trusted`（今場戰鬥資料信得過未）、`handoff_waits`、`unready`、`compiler`（揀好嘅 intent 同未撳完嘅按鍵序列，或 `null`）、`stats` |
| RuleBrain | `i`（固定圖案位置）、`last_pos`、`last_was_walk`、`stuck` |
| RandomBrain | `seed`、`rng_state`（`random.getstate()` 轉 list） |
| LLMBrain（stub） | `null` |

* 設定值（`settle_checks`、`max_frozen` 等 constructor 參數）**唔存**：由程式／啟動參數決定。
* 目標：續玩後同一個 game state ＋ 同一個 `brain_state` → 同冇中斷過嘅 run **逐步一樣**
  （實作 PR 要用真 ROM 證明：kill → resume → 之後 N 步 action 同原本 log 一樣）。

### v1 相容

* 讀：`format_version` 1 或 2 都收；冇 `kind` 當 `"game"`；冇 `brain_state` → brains `reset()`、
  只還原 `milestones_done`（即係 #28 行為）；冇 `label`／`settings` → `null`。
* `list_saves` 回 `has_brain_state: false` 俾 v1 存檔，頁面可以標示「AI 狀態唔完整」。
* 寫：實作之後一律寫 v2；唔會改寫舊檔。
* `runlog.replay()` 唔受影響（replay 只用遊戲 state 同 executed actions）。

### Run log header（`resumed_from`，v2）

v2 續玩（CLI `--resume` 同 dashboard `resume_request` 一樣）嘅 run log header `resumed_from` 喺 v1 欄位
（`sidecar, state, state_sha1, step, frame, adapter_state, sav, milestone`）之外再加：

| 欄位 | 內容 |
|---|---|
| `ai_status_id` | 套用咗嘅 AI 狀態 id；冇套用（包括 CLI `--resume`）就係 `null` |
| `has_brain_state` | `true` = 續玩時真係 `import_state` 咗一份 `brain_state`（嚟自 `ai_status` 或者 v2 存檔）；`false` = 冇（v1 存檔又冇 `ai_status_id`），brains 只係 `reset()` ＋還原里程碑 |

* 舊 log（冇呢兩個欄位）當 `ai_status_id: null`；`has_brain_state` 冇就當唔知道（`null`）。
* `runlog.replay()` 只用 `state`／`state_sha1`，唔睇呢兩個欄位，所以 replay 唔受影響。
