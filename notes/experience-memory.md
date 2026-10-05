<!-- Author: Backend Developer, 2026-10-06 (Asia/Taipei). Design only, no code / schema change. Base: origin/main a0d4ad1. -->

# 經驗記憶（Experience Memory）設計

> 目標：每次 run 的「動作 → 結果」記成經驗，跨 run 重用。**不另起新系統**：在現有 `game_brain/memory.py`（`ExperienceMemory` → `~/.game-brain/memory/experience.sqlite3`）上加表、加查詢 API，並把 `go_explore.py`、`rl/` 接進同一個庫。
> 本文件只是設計，沒有改任何 code 或 schema。所有數字都標明出處（mock 實測／舊真 ROM log 推算）。

## 0. 摘要（決定）

| # | 決定 |
|---|---|
| D1 | `experience.sqlite3` 保持唯一經驗庫；`run.jsonl` 仍是每步完整原始紀錄。SQLite 只放 **索引 + 聚合**（per state-action 統計、牆／warp、戰鬥結果、run 摘要），不再複製完整 RAM。 |
| D2 | Schema 用 `PRAGMA user_version` 做**增量 migration**（1 → 2 → …），只 `CREATE TABLE/INDEX`，**不改 `runs` 欄位**（`memory_database_path()` 用 `runs` 欄位完全相等判斷相容，改了會被當成另一個庫）。 |
| D3 | State key = `(namespace, map_bank, map_id, x, y, facing, in_battle, ctx)`，`ctx = party_count + milestones_done 的穩定 hash`。`namespace = adapter:ROM_SHA1`（現有）。 |
| D4 | 聚合表存「事實計數」（有冇郁、撞牆、換圖、里程碑、全滅…），reward 只按 `reward_version` 分開累計；reward 公式改了不會污染事實。 |
| D5 | 所有聚合都是**衍生資料**，可由 `transitions` 或 `run.jsonl` 重建（`--rebuild`）；聚合邏輯改版只需 bump `aggregator_version` 再重建。 |
| D6 | Brain 讀記憶預設**關閉**；開啟時只讀 **run 開始時凍結的 snapshot**，header 記錄 snapshot digest。`replay()` 只重播 `executed_action`，本來就不受影響（0 mismatch 保持）；snapshot 保證「同 seed + 同起點 + 同 snapshot → 同決策」。 |
| D7 | Go-Explore 與 RL runner 也寫進同一庫（`source` 欄分開），Go-Explore cell 抽樣權重乘一個由經驗算出的因子；RL 可用跨 run 計數做 exploration bonus 及離線資料。 |
| D8 | 多 worker：一個 DB 只一個 writer（批次 transaction），worker 只寫自己的 `run.jsonl`／queue，再 ingest（冪等）。 |

## 1. 現況（origin/main a0d4ad1）

### 1.1 `ExperienceMemory`（`game_brain/memory.py`，`SCHEMA_VERSION = 1`，`REWARD_VERSION = "novelty-v1"`）

| 表 | 主鍵 | 內容 | 寫入時機 |
|---|---|---|---|
| `runs` | `run_id` | namespace、`rom_hash`、`policy_version`（= `savestate.git_commit()`，含 `-dirty`）、`log_path`、`started_at`/`ended_at`、`resumed_from`（sidecar 路徑） | `__init__` 插入；`finish()` 寫 `ended_at` |
| `episodes` | `episode_id`（uuid） | `run_id`、`start_step`、`initial_state`（完整 `obs.summary()` JSON）、parent run/episode/step（sidecar `memory` 欄）、`end_reason` | `start()`；`record()` 在上一步 `terminated` 後開新 episode；`finish()` 填 `end_reason` |
| `transitions` | `(run_id, step_id)` | `state_before`/`state_after`（**完整** `obs.summary()` JSON，含 `collision`/`warps`/`npcs`/`party`/`battle`）、`executed_action`、`reward_parts`、`reward`、`terminated`/`truncated`、`actor`、`mode`、`brain`、`frames_advanced`、`cell_key`、版本欄 | `record()`：每步一個 transaction（`synchronous=FULL`） |
| `discoveries` | `(namespace, kind, key)` | kind ∈ `tile`/`map`/`milestone`/`party`/`battle_win`；跨 run 去重的新奇度 | `_seed()`（起點只作基線）、`record()` |
| `cells` | `(namespace, cell_key)` | `visits`、首次 run/episode/step、代表存檔相對路徑（`exploration/…`）、progress | `_visit()`；新 cell 用 `cell_saver` 存一個 `.state` |

- Reward（`record()`）：新 tile +1、新地圖 +5、新里程碑 +10、隊伍新數量 +10、同（地圖, 對手 species）首勝 +5、明確 `outcome == "lose"` −10。只在 outcome **邊緣**計（`before.in_battle` 且 outcome 改變）。
- `_cell()`：`[map_bank, map_id, x, y, party_count, sorted(progress)]`，精確 tile、無 facing；戰鬥／非 overworld → `None`。
- `_success()`：寫死 FireRed「有御三家並在 3/1」→ `terminated`。
- 讀取端：`dashboard_status()`（最近 8 步、最少訪問 6 cells）、`inspect_memory()`／`route()`（CLI `python -m game_brain.memory [--route RUN:STEP]`）、`learning.train_imitation()`（只取 `actor='human'`）、`rl/ppo.train_short_ppo()`（全部 transitions）。**沒有任何 brain 在決策時讀這個庫。**
- 誰建立：`setup.Session.start()`（CLI `demo.py` 與 `dashboard/live.py` 共用）；`record_step()` 每步呼叫並把結果放進 `run.jsonl` 的 `experience` 欄；`_load_game_state()`／`new_game()` 會 `finish()` 再 `start()`；`live.py` 存檔掣會呼叫 `memory.save()`（WAL checkpoint）。
- 不相容舊庫：`memory_database_path()` 會改用 `experience-v1.sqlite3`，而不是 migrate。

### 1.2 其他相關 code

| 模組 | 現況 | 與記憶的關係 |
|---|---|---|
| `go_explore.py` | `GoExploreArchive`：`<memory-dir>/archive.json`（預設 `~/.game-brain/go-explore`，**單一 archive per dir，不是 spec 的 `<run_id>/`**），cell key = `[bank, map, x//G, y//G, party_count, sorted(progress)]`（G=2）、`visits/times_chosen/chosen_since_new`、tabular Q（`q_values` 存在 JSON）；`select()` 用 count-based 權重 ×2 前線 | **完全不寫 `ExperienceMemory`**；cell 定義與 `memory._cell` 不同（G=2 vs 精確 tile） |
| `rl/env.py` `FireRedEnv`（#59） | 6 鍵、reward = `progress_delta` + `AntiLoop.penalty` | 不寫記憶 |
| `rl/progress.py` | 通關 1000、徽章 50、圖鑑 20、隊伍 2、換圖 1、重複 −0.2、每步 −0.01 | mGBA adapter **沒有** `badges`/`champion`/`pokedex_*`，實際只得 party／換圖／步罰 |
| `rl/short.py`（#60） | 自己的 `seen` dict（只在本 run） | 跨 run 計數正是記憶可提供的 |
| `rl/ppo.py` | 讀 `transitions`，用 `progress_delta` 重算 reward，學一組**與 state 無關**的 6 鍵 logits | 是離線消費者 |
| `brain/path.py` | `_blocked`（撞兩次的 tile，TTL `block_ttl`）只在本 run | 跨 run 牆／NPC 阻擋可預先餵入 |
| `runlog.py` | 每步 `observation`、`observation_after`、`decision`、proposed/executed、`notes`、`experience`；map/npcs/party/battle 已 dedupe；`replay()` 只重播 `executed_action` | 完整原始資料來源 |
| `savestate.py` | `check_save_dir()` 拒絕 repo 內路徑；`git_commit()` | 隱私守門 |

### 1.3 已發現的缺口／bug

| # | 位置 | 問題 | 影響 |
|---|---|---|---|
| G1 | `memory.record()` | 每步存兩份完整 `obs.summary()`（含 collision 字串、warps、npcs）；`run.jsonl` 已 dedupe，SQLite 反而沒有 | 尺寸（見 §2.5）；與 run.jsonl 重複 |
| G2 | `transitions` | 沒有 decision 資訊（goal、confidence、reason、proposed action、`notes`），沒有「動作有冇效果」 | 無法回答「此處按 X 會不會撞牆」 |
| G3 | schema | 沒有 per (state, action) 聚合／索引，只有 `transitions_cell` index | 跨 run 查詢要掃全表 |
| G4 | `memory_database_path()` | 遇到不相容庫就開新檔（`experience-v1.sqlite3`），沒有 migration 機制 | 升級會「遺失」舊經驗 |
| G5 | `discoveries` | 新奇度依賴庫歷史 → 同一動作在不同時間 reward 不同；多 process 同寫時誰先 insert 誰得分 | reward 非平穩；並行下不可重現 |
| G6 | `go_explore.py` | 不寫 `ExperienceMemory`；cell 定義不一致；Q 表只在 `archive.json` | Go-Explore 經驗無法被其他 brain／RL 用 |
| G7 | `notes/go-explore.md` | 仍寫「使用 Backend 的 `GameBrainEnv`（`game_brain/gym_env.py`）」及 `<run_id>/` 目錄 | 文件與 code 不符 |
| G8 | `rl/env.py` `reset(savestate)` | 把路徑字串直接傳給 `adapter.load_state(data: bytes, …)`；mock-house 實測 `AttributeError: 'str' object has no attribute 'decode'` | 不能從存檔開始短局 |
| G9 | `rl/progress.py` | 依賴 adapter 未提供的 RAM 欄；`new_map` 只看「map 有沒有變」，門口來回可刷分 | 獎勵與 README 描述不符 |
| G10 | `rl/ppo.py` | 只看第一個 press、logits 不依 state、reward 用重算而非已存 `reward_parts` | 名為 PPO，實為全域按鍵偏好 |
| G11 | `demo.main()` | 沒有把 `a.imitation_model` 傳給 `run()` | CLI `--imitation-model` 在 demo 無效 |
| G12 | `memory._success()` | FireRed 3/1 寫死在「通用」記憶模組 | 換遊戲／換任務要改 memory.py |
| G13 | `.gitignore` | 沒有 `*.sqlite3*`、`archive.json` | 只靠 `check_save_dir()`；多一層防線較安全 |
| G14 | cell 存檔 | 每個精確 tile cell 存一個 `.state`（真 ROM 397,312 B），無上限 | 磁碟（README 已提示） |

## 2. 每步記錄什麼

### 2.1 欄位與去向

| 類別 | 欄位 | 來源 | `run.jsonl` | SQLite（建議） |
|---|---|---|---|---|
| 觀察摘要 | `map_bank, map_id, x, y, facing, in_battle, scene` | `obs.ram` | ✅ 完整 | `state_keys`（整數 `sid`） |
| | party 摘要 `[(species_id, level, hp/max_hp)]`、`party_count` | `ram["party"]` | ✅ | 只在 `battles`／`run_meta` 用；`ctx` 只用 `party_count` |
| | `milestones_done` | planner（memory 自己的 `GoalPlanner`） | ✅（`decision.milestones`） | `ctx` hash；`run_meta.milestones` |
| Action | canonical presses（`imitation._action_key()` 格式）+ 主鍵 button | `result.executed` | ✅ proposed + executed | `action_keys`（整數 `aid`），只記 **executed** |
| Decision | brain、actor、mode、goal、confidence、reason、intent/chosen_option | `result.decision` | ✅ | 聚合計數 by brain；**不存** reason 文字（太大，看 log） |
| 結果 | reward parts（`novelty-v1`；RL 用 `progress-v1`） | `record()`／`FireRedEnv.step()` | ✅ `experience` | `sa_stats.reward_sum/sq` 按 `reward_version` |
| | 位置差 `dx, dy`、`moved`、`map_changed`、`turned` | before/after | 新增到 `experience.effect` | `sa_stats` 計數 |
| | `bump`：方向鍵、before 已面向該方向、同圖同位置、雙方 overworld 且非戰鬥 | before/after | 同上 | `sa_stats.n_bump`、`tile_facts(kind='wall')` |
| | `no_effect`：RAM（扣除 `vblank_counter`/`held_keys`/`callback2` 計數類）完全不變 | before/after | 同上 | `sa_stats.n_effect` 的補數 |
| | warp：`map_changed` 且 before 位置／方向 | before/after | 同上 | `warp_edges` |
| | 新 tile／新 map（跨 run，現有 `discoveries`） | `record()` | ✅ | 照舊 |
| | 里程碑新增 | planner diff | ✅ | `sa_stats.n_milestone`、`run_meta` |
| | 戰鬥開始／outcome 邊緣（win/lose）、全滅 | `ram["battle"]` | ✅ | `battles`、`battle_moves`、`sa_stats.n_battle`/`n_whiteout` |
| | stuck／loop：同一 `(map,x,y)` 在 24 步窗 repeat rate > 0.5（同 `AntiLoop`）、同鍵連按 6 次 | 滑動窗 | `experience.loop` | `stuck_events` |

原則：**`run.jsonl` = 一步一行完整事實；SQLite = 可查詢的「這個 state 做這個 action 通常會怎樣」。** 不在 SQLite 存 reason 文字、截圖、collision 字串。

### 2.2 State key

```
state = (namespace, map_bank, map_id, x, y, facing, in_battle, ctx)
ctx   = f"p{party_count}:m{sha1(sorted(milestones_done))[:8]}"
```

- 有 facing：撞牆／對話／按 A 是否有效取決於面向（與 `brain/imitation.state_features` 一致）；Go-Explore cell 不用 facing，兩者用不同粒度，靠 `(map, x, y)` 互相 join。
- 戰鬥中：`x, y` 用進戰前位置，`in_battle=1`；戰鬥細節另用 `battles` 表，不放進 state key（避免爆炸）。
- `scene != "overworld"` 且無座標：`sid` = `(map=-1, …, ctx)` 的「非 overworld」桶，只計數不給建議。

### 2.3 建議新增表（schema v2，只新增）

```sql
CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, aggregator_version, created_code_sha, last_rebuild_at

CREATE TABLE run_meta (               -- 不改 runs：memory_database_path() 比對 runs 欄位
  run_id TEXT PRIMARY KEY REFERENCES runs, source TEXT NOT NULL,  -- live|demo|go-explore|rl-short|ppo-eval
  adapter TEXT, brains TEXT, mode TEXT, starter_requested TEXT, starter_picked TEXT, seed INTEGER,
  rom_sha1 TEXT, code_sha TEXT, steps INTEGER, frames INTEGER, milestones TEXT, best_milestone TEXT,
  result TEXT, whiteouts INTEGER, battles_won INTEGER, battles_lost INTEGER,
  new_tiles INTEGER, new_maps INTEGER, memory_read TEXT, snapshot_digest TEXT
);
CREATE TABLE state_keys (sid INTEGER PRIMARY KEY, namespace TEXT NOT NULL, map_bank INT, map_id INT,
  x INT, y INT, facing TEXT, in_battle INT, ctx TEXT,
  UNIQUE (namespace, map_bank, map_id, x, y, facing, in_battle, ctx));
CREATE TABLE action_keys (aid INTEGER PRIMARY KEY, action_key TEXT UNIQUE NOT NULL, button TEXT);

CREATE TABLE sa_stats (               -- 每 (state, action) 聚合
  sid INT NOT NULL REFERENCES state_keys, aid INT NOT NULL REFERENCES action_keys,
  n INT, n_effect INT, n_moved INT, n_bump INT, n_turn INT, n_map_change INT,
  n_milestone INT, n_battle INT, n_whiteout INT, n_stuck INT,
  reward_version TEXT, reward_sum REAL, reward_sq REAL,
  first_run_id TEXT, last_run_id TEXT, last_seen REAL, last_run_seq INT,
  PRIMARY KEY (sid, aid));
CREATE TABLE sa_next (sid INT, aid INT, next_sid INT, n INT, PRIMARY KEY (sid, aid, next_sid));

CREATE TABLE tile_facts (namespace TEXT, map_bank INT, map_id INT, x INT, y INT, dir TEXT,
  kind TEXT,                          -- wall|npc_block|ledge|frozen
  n_obs INT, n_contra INT, last_run_id TEXT, last_seen REAL,
  PRIMARY KEY (namespace, map_bank, map_id, x, y, dir, kind));
CREATE TABLE warp_edges (namespace TEXT, src_bank INT, src_map INT, src_x INT, src_y INT, dir TEXT,
  dst_bank INT, dst_map INT, dst_x INT, dst_y INT, n INT, last_seen REAL,
  PRIMARY KEY (namespace, src_bank, src_map, src_x, src_y, dir, dst_bank, dst_map));

CREATE TABLE battles (namespace TEXT, run_id TEXT, start_step INT, end_step INT,
  map_bank INT, map_id INT, kind TEXT, opp_species_id INT, opp_level INT,
  lead_species_id INT, lead_level INT, outcome TEXT, turns INT, hp_left_pct REAL,
  PRIMARY KEY (run_id, start_step));
CREATE TABLE battle_moves (namespace TEXT, lead_species_id INT, opp_species_id INT, move_id INT,
  n INT, n_opp_hp_drop INT, opp_hp_drop_sum REAL, n_win_battle INT,
  PRIMARY KEY (namespace, lead_species_id, opp_species_id, move_id));

CREATE TABLE stuck_events (namespace TEXT, run_id TEXT, step INT, sid INT, kind TEXT, length INT,
  resolved_aid INT, PRIMARY KEY (run_id, step));
CREATE TABLE ingested (run_id TEXT PRIMARY KEY, last_step INT, aggregator_version INT);  -- 冪等
CREATE INDEX state_keys_map ON state_keys(namespace, map_bank, map_id);
```

- 只存 species／move **ID**（數字），名稱由 ROM 表即時解碼，不入庫。
- `transitions` 保留（`learning.py`、`ppo.py`、`route()` 依賴）。v3 再把 `state_before/after` 的 `MAP_KEYS`（`collision/warps/map_w/map_h`）與 `npcs` 去掉（兩個消費者都不用這些欄位），並加一個 `effect` JSON 欄。

### 2.4 Migration 規則

| 規則 | 說明 |
|---|---|
| 版本 | `user_version` 0/1 → 依序執行 `_MIGRATIONS[2..N]`，每段一個 transaction，結束寫 `user_version` 與 `memory_meta.schema_version` |
| 只新增 | 不 DROP、不 ALTER `runs`；需要的新 run 欄位放 `run_meta` |
| 舊 code 遇新庫 | 現有 `version not in (0, SCHEMA_VERSION)` → `ValueError`，明確拒絕（符合預期） |
| 聚合改版 | bump `aggregator_version` → `python -m game_brain.memory --rebuild`（由 `transitions` 或 `--from-logs runs/*/run.jsonl` 重算），不需 migration |
| 備份 | migration 前 `sqlite3 .backup` 到 `experience.sqlite3.bak-v<old>` |

### 2.5 尺寸估計

| 項目 | 數據 | 出處 |
|---|---|---|
| 現有 `transitions` | mock-house 2,000 步 → DB 4.97 MB（約 2.5 KB/步，`state_before` 平均 657 B） | 本 PR 實測（`--adapter mock-house --brains battle,path,rule --steps 2000`） |
| 真 ROM `obs.summary()` | 平均 705 B、最大 1,614 B（舊 log，無 `party`；加 party 約 +0.5–1 KB） | `pr34tmp` 舊真 ROM run.jsonl 2,077 步用 `iter_steps` 還原 |
| 真 ROM 現有每步 | 約 3–5 KB（兩份 summary + index） → 100 萬步 ≈ 3–5 GB | 推算 |
| 建議 `sa_stats` | 每行 ≈ 120 B；首個徽章前估計 ≤ 2–5 萬 tile × 4 facing × 已試動作（實際只有走過的）≈ 10–30 萬行 → 12–36 MB | 推算 |
| `state_keys`/`tile_facts`/`warp_edges`/`battles` | 合計 < 20 MB | 推算 |
| v3 精簡 `transitions`（去 MAP_KEYS/npcs） | 約 −40～60% | 待 P8 實測 |
| cell `.state` | 397,312 B／cell | `notes/go-explore.md` Backend 實測 |

註：本機目前**沒有** `~/.game-brain/memory`（只有 `saves/`，44 MB），所以沒有真庫可量。真 ROM 實測需要 mGBA Python binding（本次環境未有 build，見 PR body）。

## 3. 跨 run 查詢

### 3.1 API（新模組 `game_brain/memory_view.py`，唯讀）

```python
view = ExperienceView.open(root, namespace, snapshot="run-start")   # 唯讀 + 凍結
view.snapshot_info() -> {"schema_version", "aggregator_version", "seq", "digest"}
view.action_stats(obs) -> list[ActionStat]       # n, p_effect, p_bump, mean_reward, confidence, last_seen
view.best_actions(obs, k=3, min_n=3)             # 依 Wilson 下界 / mean reward
view.blocked_actions(obs, min_n=2, p=0.9)        # 高信心無效／撞牆
view.known_walls(map_bank, map_id) -> set[(x, y, dir)]
view.known_warps(map_bank, map_id) -> list[WarpEdge]
view.loop_hotspots(map_bank, map_id) -> list[(x, y, n_stuck)]
view.battle_record(opp_species_id, lead_species_id=None) -> {n, win, lose, mean_turns}
view.move_stats(lead_species_id, opp_species_id) -> list[(move_id, n, mean_drop, win_rate)]
view.cell_stats(cell_key) -> {untried_actions, dead_end, stuck, visits}   # Go-Explore 用
view.run_history(limit=20) -> list[run_meta]
```

### 3.2 誰用、怎樣用

| 消費者 | 用法 | 保護 |
|---|---|---|
| PathBrain | 每次進新圖，用高信心 `known_walls`（`n_obs ≥ 2`、`n_contra = 0`）預填 `_blocked`；`known_warps` 補 collision 看不到的出入口 | 仍有 TTL；A* 失敗時照舊清掉 bump 記錄（`path.py` fallback） |
| RuleBrain | stuck 時優先試 `p_effect` 高、`blocked_actions` 以外的鍵 | 只排序，不刪選項 |
| RuleBattleBrain | 同分招式用 `move_stats` tie-break；`battle_record` 影響 confidence | 驗證 ROM 只有 METRONOME，先只記錄不用 |
| Dashboard | `memory` status 加：此 tile 已知動作／牆、地圖熱度（visits）、`run_history` 表 | bounded；不送完整路徑或 RAM（沿用 `dashboard_status()` 規則） |
| CLI | `python -m game_brain.memory --state MAP/X/Y`、`--walls MAP`、`--runs` | 唯讀 `mode=ro` |

### 3.3 信心與衰減

- 機率：Beta(1,1) 先驗 → `p = (k+1)/(n+2)`，排序用 Wilson 95% 下界。
- 信心：`confidence = n / (n + 5)`。
- 衰減：用 run 序號而非時間：`w = 0.5 ** ((cur_run_seq - last_run_seq) / H)`，`H = 20` runs；`tile_facts` 若有 `n_contra > 0`（後來走過去了，例如 NPC 移開）即降為 `npc_block`／失效。
- 版本：所有表都帶 `namespace`（ROM SHA1）→ 換 ROM 自然隔離。`code_sha` 只寫在 `run_meta`，不影響牆／warp 這類 ROM 事實；reward 類統計按 `reward_version` 分開；聚合語意改 → `aggregator_version` + rebuild。

### 3.4 Determinism

| 層面 | 現況 | 加入記憶讀取後 |
|---|---|---|
| `runlog.replay()` | 重播 `executed_action`，比對 frame 與 RAM | **不受影響**：決策不在 replay 中重算，0 mismatch 保持 |
| 「同 seed 同起點 → 同動作」（真 ROM 測試與 `--seed` 重現都依賴這點） | 成立（brain 只看 obs + seed） | 若 brain 讀「會增長的庫」就**不成立** |
| 現有 reward 記錄 | `discoveries` 已依歷史（G5） | 照舊：reward 是紀錄，不是決策 |

做法：
1. `--memory-read {off,snapshot}`，**預設 off**；所有現有測試維持 off。
2. `snapshot`：Session 開始時以 `sqlite3.Connection.backup()` 把讀取用表（`state_keys`、`action_keys`、`sa_stats`、`tile_facts`、`warp_edges`、`battle_moves`）複製到 `:memory:`；整個 run 只讀這份。本 run 新學到的東西仍只存在 brain 自身狀態（如 `PathBrain._blocked`），不會從庫回讀。
3. Header 與 `run_meta` 記：`memory_read = {mode, schema_version, aggregator_version, seq, digest}`；`digest` = 上述表按主鍵排序後的 canonical dump sha1。
4. 需要「決策重現」時：`--memory-snapshot-out` 把該 snapshot 存到 `~/.game-brain/memory/snapshots/<digest>.sqlite3`，重跑時 `--memory-snapshot <digest>`；digest 不符即拒絕。

## 4. 與 Go-Explore／RL 整合

### 4.1 Go-Explore（`go_explore.py`、`notes/go-explore.md`）

| 項目 | 建議 |
|---|---|
| 寫回 | `GoExploreRunner` 選填 `experience=ExperienceMemory(..., source="go-explore")`。每個 segment 從 cell 載入後 `finish("segment_end")` + `start(obs, step, parent=cell 代表的 run/episode/step)`，因為 `record()` 要求連續 step（G6），而 lineage 讓 `route()` 可接回 |
| 需要的小改 | `record()` 現在吃 `StepResult`；加 `record_raw(step, before, action, after, frames, brain, actor="brain", decision=None)` 給沒有 Arbiter 的 runner |
| Cell key | 不合併兩個定義：`go_explore.cell_key`（G=2）繼續做 archive key；記憶用精確 `state_keys`，以 `(map, x//G, y//G, ctx)` 聚合查詢 `cell_stats()` |
| 抽樣權重 | `w(c) *= clamp(1 + a·untried(c) − b·dead_end(c) − c·stuck(c), 0.25, 4)`；`untried` = cell 內已知 tile 的 4 方向中從未試過的比例；`dead_end` = cell 內 `p_effect < 0.1` 的比例。a=b=c=0.5 起步，用 snapshot（每 N iteration 刷新一次並寫入 metrics） |
| Q 表 | 短期仍在 `archive.json`；可選用 `sa_stats` 的 mean reward（同 `reward_version`）做 warm start。長期是否把 Q 搬入 SQLite 列為 open question |
| 存檔 | Go-Explore 繼續自己存 cell `.state`；`ExperienceMemory(save_cells=False)` 避免重複存兩份 |

### 4.2 短局 RL（`rl/env.py` #59、`rl/short.py` #60、`rl/ppo.py`）

| 項目 | 建議 |
|---|---|
| 寫回 | `FireRedEnv(adapter, experience=None)`：有傳入就在 `step()` 內 `record_raw(...)`，`source="rl-short"`、`reward_version="progress-v1"`，`reward_parts` 存 env 的 parts（含 `loop`） |
| 跨 run 探索 bonus | `r_explore = β / sqrt(1 + N(s))`，`N` 來自 snapshot 的 `sa_stats`（每次訓練 iteration 刷新一次 → 對該 iteration 平穩） |
| 撞牆懲罰 | 用 `blocked_actions` 給小負值，或直接做 action mask（mask 比 shaping 安全，不改最優策略） |
| AntiLoop | `loop_hotspots` 可作為窗大小／門檻的地圖別參數 |
| 離線資料 | `ppo.py`／`learning.py` 改讀 `export_offline(namespace, source=…, actor=…, reward_version=…)`；`ppo.py` 應用已存 `reward_parts` 或明確版本化的重算（G10） |
| `short.py` | `seen` 改為「本 run 計數 + snapshot 跨 run 計數」 |

### 4.3 並行寫入

| 情境 | 做法 |
|---|---|
| 單一 live／demo | 照舊：每 run 一個 connection、WAL、每步 commit |
| 多 worker（Go-Explore `--workers N`、RL） | worker **不直接寫** DB：寫自己的 `run.jsonl`（或 multiprocessing queue）；主程序單一 writer 每 200 步或 1 秒批次 transaction ingest |
| 冪等 | `ingested(run_id, last_step)` watermark；重跑 ingest 不會重複計數 |
| `synchronous` | `runs/transitions` 保持 FULL；聚合表是衍生資料，可 NORMAL |
| 讀者 | 一律 `mode=ro` 或 snapshot，不阻塞 writer |
| 新奇度競爭（G5） | 並行時 `discoveries` 只由單一 writer 決定，順序 = ingest 順序（記錄在 `ingested`），可重現 |

## 5. 隱私與 repo 規則

| 規則 | 說明 |
|---|---|
| 位置 | DB、snapshot、cell `.state` 只放 `~/.game-brain/memory`（或 `$GAME_BRAIN_MEMORY_DIR`），`check_save_dir()` 照舊拒絕 repo 內路徑 |
| 不入庫的資料 | 不存 species／move 名稱、collision 字串、截圖（名稱與地圖來自 ROM）；只存 ID 與座標 |
| 測試 | 只用 mock adapter 合成資料；不提交任何 `.sqlite3`、`archive.json`、run.jsonl |
| `.gitignore` | 建議另一個小 PR 加 `*.sqlite3`、`*.sqlite3-wal`、`*.sqlite3-shm`、`archive.json`（G13） |
| PR 證據 | 只貼統計（行數、大小、命中率、mismatch 數），不貼 DB dump 或 RAM |
| Dashboard | 沿用 `dashboard_status()`：不送完整本機路徑或遊戲 snapshot |

## 6. 分階段實作（每個 PR 可單獨 revert）

| Phase | 內容 | 測試 | 真 ROM 證據 |
|---|---|---|---|
| P0 | 本文件 | — | — |
| P0.5 | 小修：G8 `rl/env.reset` 讀 sidecar／bytes、G11 demo 傳 `imitation_model`、G7 文件、G13 `.gitignore` | 各自單元測試 | `rl.short` 從 `pallet.state` 起跑一次 |
| P1 | Migration 框架 + `memory_meta` + `run_meta`（寫入 run 摘要），無行為改變 | v1 庫 migrate 到 v2 且 `runs` 欄位不變；v2 庫被舊版本拒絕；`--no-memory` 不受影響 | 500 步 run：`replay()` 0 mismatch，DB 大小 |
| P2 | `effect` 判定 + `state_keys/action_keys/sa_stats/sa_next/tile_facts/warp_edges/stuck_events`；`run.jsonl` `experience.effect` | mock-house：撞已知牆 → `tile_facts`；換圖 → `warp_edges`；重跑兩次計數翻倍 | 開機→研究所：學到的牆與 `ram["collision"]` 一致率 ≥ 95%；replay 0 mismatch |
| P3 | `battles` + `battle_moves`（只在 outcome 邊緣） | mock-battle 勝／負各一 | 勁敵戰 + 1 號道路野戰紀錄 |
| P4 | `ExperienceView` + CLI 查詢（唯讀） | 查詢結果與手算一致；`mode=ro` 下無寫入 | CLI 輸出摘要 |
| P5 | `--memory-read snapshot`、header digest、`--memory-snapshot(-out)` | 同 seed + 同 snapshot 跑兩次 executed actions 完全相同；不同 snapshot digest 不同 | 兩次 3,000 步 run 動作一致；replay 0 mismatch |
| P6 | Brain 消費（opt-in）：PathBrain 預填牆／warp、RuleBrain stuck 排序 | 記憶 off 時行為與 main 完全相同 | A/B：到常磐市步數（PathBrain 基準 3,115 步）不退步 |
| P7 | Go-Explore 寫回 + 抽樣因子；`FireRedEnv` 寫回 + exploration bonus | mock-house Go-Explore 寫入 transitions 且 `route()` 可接 | 30 分鐘 Go-Explore：`cells` 曲線 vs 無因子對照 |
| P8 | `--rebuild`、v3 精簡 `transitions`、保留期／壓縮 | rebuild 後聚合與增量結果一致 | 10 萬步前後 DB 大小對比 |

## 7. Open questions

1. `ctx` 用 milestone 集合 hash 是否太細？（每個新里程碑都會讓同一 tile 變新 state）替代：只用 `party_count` + 已完成里程碑**數量**。
2. v3 是否把 `transitions` 的完整 summary 移除，只留 `run.jsonl` 作原始資料？`route()`／imitation 需要的欄位要先列清楚。
3. Go-Explore Q 表要不要搬入 SQLite（變成 `sa_stats` 一個 `q_value` 欄），還是保持 archive 自足？
4. `_success()`（3/1 + 御三家）是否改由 `rl/curriculum.STAGES` 提供 task 定義？
5. Brain 讀記憶的 A/B 門檻：要贏多少才可以預設開啟？
6. 並行 ingest 是否值得現在做，還是等 `--workers` 真的實作？
7. `rl/progress.py` 依賴未驗證 RAM 欄（徽章／圖鑑）：記憶裡的 `progress-v1` reward 是否先不累計，直到 RAM 驗證？
