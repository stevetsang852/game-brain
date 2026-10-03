<!-- Copied from /workspace/news-research/game-brain/ml-decision-2026-10-02.md. Author: Research Manager (2026-10-02). Content unchanged; only formatting may differ. -->

> Credit: written by **Research Manager**, 2026-10-02. Copied into this repo unchanged.

## Implementation status (2026-10-03)

The repository now includes a stdlib-only supervised behavior-cloning baseline:
`python -m game_brain.learning` trains from `actor='human'` transitions in one exact
adapter/ROM namespace. `ImitationBrain` reproduces only exact observed states and falls
through to the next brain for unknown or ambiguous states. It is suitable for replaying
human-demonstrated segments, including after the current route placeholder, but does not
generalize to unseen tiles or infer Route 2 / Viridian Forest map IDs. Those route goals
must wait for observations from the modified ROM. Go-Explore cell selection and PPO remain
future work; the current SQLite exploration archive is not a Go-Explore controller.

# game-brain 學習型決策參考（2026-10-02，Research Manager）
## TypeSafe Jev（https://typesafe.ai/blog/introducing-system-one-models-and-jev，2026-09-15）
- 快速結構化決策模型：輸入狀態，輸出預先定義選項＋校準機率；官方稱 70–500ms、input $0.042/MTok、output 免費、不會出 schema 錯誤。
- 限制：early access 要申請；目前只吃文字/結構化狀態，不吃圖片；單次選項上限 255；數據全是廠商自報。Doom demo 每秒 10 次約 US$7/小時（官方說法）。
- 定位：不是學習演算法，是付費 API。適合做 LLMBrain 旁的「快速選擇層」，用信心度決定 Auto 還是交給人（Assist）。
## 學習型方法
| 方法 | 參考 | 用途 | 需要 |
|---|---|---|---|
| Go-Explore 式 save state 探索 | Go-Explore 論文；PokeRL 列為後續方向 | 不用訓練，靠 deterministic 模擬器探索新格子、走出屋 | map/座標 RAM（已有）、save state |
| 行為複製（模仿學習） | actor=human log | 用人手 Manual/Assist 紀錄訓練小模型 | 足夠人手 log |
| PPO 強化學習＋課程學習 | PWhiddy/PokemonRedExperiments（MIT）、drubinstein/pokemonred_puffer、reddheeraj/PokemonRL（MIT）、arXiv 2502.19920 | 出屋→探索→戰鬥分段訓練 | Gym 包裝、RAM 獎勵、大量 step |
| 戰鬥：規則＋傷害計算，之後再學 | continual-harness 的 type chart/damage calc | 戰鬥選招 | in_battle/party RAM（未驗證） |
