# 外來倉庫對照計劃（2026-10-07）

呢段只借方向。GUI 加了 harness 式每步紀錄，未搬 React，未接 LLM provider，未改大腦。

## GUI（feat/harness-trace-panel）

參考 [maxkskhor/pokemon-harness](https://github.com/maxkskhor/pokemon-harness) 的左畫面、右 trace：

- 新增「每步紀錄」卡。observation 顯示地圖／座標同截圖縮圖；decision 顯示 brain、plan、reason、executed。
- 可展開 raw JSON。最多留 40 步。
- 未搬 agent launcher、checkpoint thumbnail、replay scrubber。我哋已有存檔面板同 JSONL replay。

## 仍然唔做

- 唔照抄 pokefirered 位址。
- 唔把 PPO 做預設。
- 唔加綠寶石／紅綠 adapter。
