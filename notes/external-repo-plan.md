# 外來倉庫對照計劃（2026-10-07，只係文件）

呢段只更新計劃，**未改程式**。外來倉庫只借方向，唔原樣搬入，亦唔會 commit ROM。

| 倉庫 | 借咩 | 而家唔做 |
|---|---|---|
| [maxkskhor/pokemon-harness](https://github.com/maxkskhor/pokemon-harness) | 火紅走 mGBA bindings；每步留 observation、推理、截圖。對齊我哋已有 JSONL／Decision，唔係換框架 | 唔換 UI，唔加新 provider，唔抄佢個 bedroom start state |
| [pret/pokefirered](https://github.com/pret/pokefirered) | 英文火紅符號同地圖名。乾淨 US 1.0 SHA1 係 `41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc` | 唔照抄位址。我哋驗證 ROM 係 `e0194282c427689768f8e618a285552f264524a4`，徽章／圖鑑 RAM 仍要逐個對 |
| [mgba-emu/mgba](https://github.com/mgba-emu/mgba) | 已經用緊 0.10.5 headless bindings | 唔升級模擬器 |
| [PokeAPI/pokeapi](https://github.com/PokeAPI/pokeapi) | 招式／屬性／種族靜態表，RuleBattleBrain 已用 | 唔改表，唔加全國 386 做目標 |
| [PWhiddy/PokemonRedExperiments](https://github.com/PWhiddy/PokemonRedExperiments) | 稀疏獎勵、地圖探索、存檔重開。同 `go_explore.py`、短局門檻同一類問題 | 唔換去 GB／PyBoy，唔把 PPO 做預設 |
| [wissammm/PkmnRLArena](https://github.com/wissammm/PkmnRLArena) | 綠寶石多智能體 RL、訓練後跑回 GBA。頁面曾寫暫停到 2026-06，用前先看最新 commit | 唔加 PettingZoo，唔開第二個 adapter |
| [pret/pokeemerald](https://github.com/pret/pokeemerald) | 日後綠寶石 adapter 的符號來源 | 今個里程碑唔做 |
| [Baekalfen/PyBoy](https://github.com/Baekalfen/PyBoy)、[pret/pokered](https://github.com/pret/pokered) | 若以後加紅綠，先用呢兩層 | 今個里程碑唔做 |

下一個文件上的次序（仍然唔改 code）：

1. 徽章同圖鑑 RAM 對照表：用 pokefirered 符號做候選，只接受我哋呢隻 ROM 實測過的位址。
2. Decision log 對照 pokemon-harness：每步要睇到 observation、原因、截圖引用。已有 JSONL 就沿用，唔開新格式。
3. Probe 仍然只提議。有 provider 之前，LLM 唔寫 `goals.json`，亦唔直接出按鍵。
4. RL 維持短局門檻之後。未證明由常磐市去到第一個徽章之前，唔換預設 `battle,path,rule`。
5. 綠寶石／紅綠 adapter 排喺火紅通關劇本之後。
