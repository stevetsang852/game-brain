<!-- Author: Research Manager, 2026-10-02 (Asia/Taipei). Spec only, no code. Intended path in repo: notes/go-explore.md -->

# Go-Explore 規格（game-brain，FireRed）

> 作者：Research Manager，2026-10-02。這份只是規格，沒有程式碼。實作由 Fullstack 負責，使用 Backend 的 `GameBrainEnv`（`game_brain/gym_env.py`）。
> 參考：Ecoffet et al., *Go-Explore*（2019 / Nature 2021）；PWhiddy *PokemonRedExperiments* 與 pokemonred_puffer 的「新格子」探索獎勵（見 `notes/references.md`、`notes/ml-decision.md`）。

> **2026-10-03 實作狀態：** `game_brain/memory.py` 已有跨 run SQLite 經驗／探索索引、精確格子（G=1）、進度維度、首次代表存檔及實際動作軌跡匯出，CLI／Dashboard 共用。這是記憶基礎，**不是以下完整 Go-Explore 演算法**：尚無 `GameBrainEnv`、cell 自動選擇、sticky action 探索段、分數替換、cell 淘汰或訓練。現有 planner 只在記憶模組評分，不由記憶模組選動作；原本 PathBrain 行為不變。實際格式／指令見 README「跨次運行記憶」；以下 archive.json／G=2／Go-Explore 旗標仍是未實作規格。

## 0. 目標與界線

| 項目 | 內容 |
|---|---|
| 第一個目標 | 從開機（或 #28 的開機存檔）開始，**不使用 PathBrain 或任何寫死的 milestone 路線來選動作**，自己探索到常青市（map 3/1） |
| 可以用的 | `GameBrainEnv` 的觀察值、RAM 特徵、獎勵分項（`info["reward_parts"]`）、save state 讀寫 |
| 不能用的 | `GoalPlanner` 的目標座標或路徑來**選動作**。planner 只能用來**計分**和定義 cell 的進度維度（env 已經是這樣做） |
| 戰鬥 | 預設 `--battle-policy rule`：`in_battle` 為 True 時，把動作交給 RuleBattleBrain。這不算路線提示，只是避免隨機亂按讓戰鬥拖太長。另設 `--battle-policy random` 做對照 |
| 執行環境 | 只用本機 CPU，不需要 GPU，也沒有付費 API |
| 存檔位置 | 一律放在 repo 以外（`check_save_dir` 規則照舊），因為 repo 是公開的 |

## 1. Cell 定義

一個 cell 是一個「離散化的遊戲狀態」。archive 中每個 cell 只保留一個代表存檔。

```
cell = (map_bank, map_id, x // G, y // G, progress)
progress = (party_count, milestones_done_count)
```

| 欄位 | 來源 | 說明 |
|---|---|---|
| `map_bank`, `map_id` | `ram` | 不同地圖一定是不同的 cell |
| `x // G`, `y // G` | `ram`，預設 `G = 2` | 2×2 格合成一個 cell。真新鎮的房屋大約只有 10×8 格，`G = 4` 會太粗；1 號道路比較長，`G = 2` 的 cell 數量仍可接受。`--cell-grid` 可以調 |
| `party_count` | `ram["party_count"]` | 拿到御三家前是 0，拿到後是 1。同一個位置拿到前後要分開，否則「研究所裡有寶可夢的狀態」會被「沒有寶可夢的舊存檔」佔住 |
| `milestones_done_count` | env 的 planner（只用來計分） | 粗略的劇情進度。例如送完包裹後回到同一格，要算作新的 cell |

**不建立 cell 的時刻**（這些步驟仍會執行，只是不會寫入 archive）：
- `in_battle` 為 True，或座標是 `null`（戰鬥中、過場或轉圖中）。戰鬥結束、回到 overworld 後的第一步，才判斷是否為新 cell。
- 不用 facing（面向）當維度，否則 cell 數量會變成 4 倍，探索價值卻不大。

## 2. Archive 每個 cell 存什麼

| 欄位 | 說明 |
|---|---|
| `cell` | 上面的 tuple，序列化成 key，例如 `3-19_6-14_p1-m9` |
| `save` | 指向一組 #28 **v1** 存檔（`.state` + `.json` sidecar，`format_version: 1`）。`reason` 用新值 `go-explore-cell`（v1 讀取端會忽略未知的 reason，需要 Fullstack 確認） |
| `score` | 用來決定「同一個 cell 由哪個存檔代表」，見 §4 |
| `steps_from_boot` | 從開機到這個狀態總共走了幾步（env step） |
| `parent`, `actions` | 父 cell 的 key，加上從父 cell 存檔走到這裡的動作序列（uint8）。這樣可以重組從開機開始的完整軌跡，之後也能拿來做模仿學習 |
| `times_chosen`, `times_seen`, `chosen_since_new` | 選擇權重用的計數，見 §3 |
| `first_iter`, `first_env_step` | 第一次發現這個 cell 的時間點，用來畫探索曲線 |

檔案配置：

```
~/.game-brain/go-explore/<run_id>/   # 獨立目錄（或 $GAME_BRAIN_GOEXPLORE_DIR），不要和 ~/.game-brain/saves 共用：--resume latest 找不到指標時會挑該目錄最新的 .json
  archive.json          # 全部 cell 的索引和計數（每 N 個 iteration 原子寫入：先寫 .tmp，再 fsync、rename）
  cells/<key>.state     # #28 v1
  cells/<key>.json      # #28 v1 sidecar，另加 "go_explore": {cell, parent, steps_from_boot, score}
  cells/<key>.actions   # 從父 cell 走到這裡的動作序列
  metrics.jsonl         # 每個 iteration 一行，見 §6
```

一個 cell 被更好的存檔取代時，舊的三個檔一起刪掉，避免佔用磁碟。

## 3. 選擇從哪個 cell 繼續（selection）

照原論文的 count-based 權重，再加上「前線」加分：

```
w(c) = 1/sqrt(1 + times_chosen) + 1/sqrt(1 + times_seen) + 1/sqrt(1 + chosen_since_new)
w(c) *= (1 + B_front) ,  若 c 是 progress 最高的 cell 之一，或位於 archive 中最新發現的地圖
P(c) = w(c) / Σ w
```

| 參數 | 預設 | 說明 |
|---|---|---|
| `B_front` | 1.0 | 優先探索最新的地圖或劇情前線，避免一直在房間裡打轉 |
| `times_seen` | — | 任何探索段經過這個 cell 就 +1 |
| `chosen_since_new` | — | 從這個 cell 出發後找到新 cell 就歸 0，否則 +1。如果一直找不到新東西，權重會慢慢下降 |

## 4. 從 cell 出發探索（explore）

1. `env.reset()` 載入該 cell 的 `.state`（記憶體讀取，不重新開機）。
2. 最多走 `K = 100` 步，動作規則：
   - **sticky action**：每一步有 `p_repeat = 0.9` 的機率沿用上一個動作，否則從動作表重新抽。原論文和 Pokémon 專案都顯示，隨機的連續移動比每步都換方向有效得多。
   - 抽動作的機率：方向鍵合計 0.7、A 0.2、B 0.08、NONE 0.02，`START` 為 0（會開選單，浪費步數）。
   - 戰鬥中依 `--battle-policy` 處理（§0）。
3. 每一步結束後計算 cell：
   - **新 cell**：建立存檔，寫入 archive。
   - **已有的 cell**：如果新的 `score` 比較好就取代。
4. 以下情況提早結束這一段：全滅（`reward_parts.whiteout < 0`），或連續 `K_stuck = 40` 步 cell 都沒變。

**score（同一個 cell 怎麼比較好壞）**：依序比較
1. `milestones_done_count` 較高者勝；
2. `steps_from_boot` 較少者勝（軌跡越短越好，之後模仿學習也會比較乾淨）；
3. 隊伍 HP 總和較高者勝（拿得到 `party_hp` 時）。

## 5. 主迴圈與停止條件

```
archive = {cell(start): start_save}
for it in range(max_iters):
    c = select(archive)            # §3
    explore(c, K)                  # §4
    if it % 20 == 0: flush archive.json + metrics
    stop if: 已到達 map 3/1，且再跑 R=200 個 iteration（用來縮短軌跡）
          or env_steps >= budget（預設 2,000,000）
          or wall_time >= 預設 8 小時
```

建議加 `--resume-archive <dir>`：從 `archive.json` 和 cells 接著探索。這就是 YIN 要的「每次的學習結果都保存，下次接著玩」。

## 6. 怎樣衡量進步

`metrics.jsonl` 每個 iteration 一行，PR 裡附上曲線圖或表格。

| 指標 | 意義 | 驗收時的用法 |
|---|---|---|
| `cells` | archive 裡一共有幾多個 cell | 主要的探索曲線。應該持續上升，平了代表卡住 |
| `maps` | 去過幾多張地圖 (bank, id) | 粗略的進度 |
| `max_progress` | 最高的 `(party_count, milestones_done_count)` | 劇情有沒有推進 |
| `first_viridian_env_step` | 第一次到達 3/1 時，總共用了幾多 env step（所有探索段加起來） | **探索效率**，越少越好 |
| `best_viridian_steps_from_boot` | archive 裡 3/1 那個 cell 的最短軌跡長度 | **軌跡品質**。作為參考：規則大腦 PathBrain 是第 3115 步到達。Go-Explore 的軌跡通常比較長，用 R 個 iteration 收斂 |
| `steps_per_sec`, `wall_time` | 吞吐量 | 估算 PPO 的成本用 |
| `disk_mb` | archive 的大小 | 控制磁碟用量 |

**對照組**（同一個步數預算）：純隨機 policy（沒有 archive）跑出的 `cells`，作為下限。Go-Explore 的 `cells` 應該明顯較高。

**驗收**：
1. 從開機開始，沒有用 PathBrain，archive 裡出現 3/1 的 cell。
2. 把 `best_viridian` 的完整軌跡（父 cell 鏈上的 `.actions` 串起來）從開機重播一次，結果同樣到達 3/1，0 mismatch。這同時驗證 determinism 和存檔鏈沒有斷。
3. PR 附 `metrics.jsonl` 的摘要表，以及隨機對照組的數字。

## 7. 已知風險和處理

| 風險 | 處理 |
|---|---|
| 開場的對白和劇情（大木攔路、媽媽）位置不動，cell 不變 | 動作表裡 A 佔 0.2；`K_stuck` 結束該段，但不懲罰該 cell，只靠 `chosen_since_new` 慢慢降權 |
| 真新鎮北面出口在拿到御三家前會被大木劇情攔住 | `progress` 把拿到寶可夢前後分開，研究所裡 `party_count=1` 的 cell 會自然成為新前線 |
| 磁碟：一個 `.state` 是 397,312 bytes（Backend 實測），sidecar 約 0.8 KB | 1,000 個 cell 約 390 MB，1 萬個約 3.9 GB。`--max-cells` 預設 3000（約 1.2 GB），超過時淘汰權重最低、且不是任何前線 cell 祖先的 cell；被取代時刪除舊檔 |
| `ram["npcs"]` 只是部分驗證 | Go-Explore 不使用 NPC 資料，不受影響 |
| 戰鬥殘留舊值 | 已由 #21 和 brain 端的保護處理。cell 只在 overworld 建立，不會用到戰鬥值 |
| 吞吐量：單一 process 約 69 步/秒（Backend 實測，24 frames/步） | 預設 `budget` 2,000,000 步，單 process 約 8 小時，與 `wall_time` 一致。可用 `--workers N` 平行跑探索段（每個 worker 一個 env，archive 只由主程序寫），4 個 worker 約 2 小時 |

## 8. 之後（不在這次範圍）

- **Robustify**：用 archive 的最佳軌跡做模仿學習的示範（backward algorithm，即從軌跡尾端開始倒著練），然後才上 PPO。PPO 的成本由 Mannger 整理後交給 YIN 批准。
- Dashboard（Frontend）：存檔列表可直接讀 `cells/*.json`；探索進度圖用 `metrics.jsonl` 的 `cells` 曲線，加上每張地圖已探索的格子。
