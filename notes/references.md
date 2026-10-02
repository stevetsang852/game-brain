<!-- Copied verbatim from /workspace/news-research/game-brain/references-2026-10-02.md. Author: Research Manager (2026-10-02). Only formatting may differ. -->

> Credit: compiled by **Research Manager**, 2026-10-02. Copied into this repo unchanged.

# game-brain 參考資料（2026-10-02，Research Manager）
| 項目 | 遊戲／模擬器 | 對我們有用的地方 | 授權 |
|---|---|---|---|
| maxkskhor/pokemon-harness https://github.com/maxkskhor/pokemon-harness | FireRed（mGBA Python bindings）、Red（PyBoy） | 最接近 game-brain：同一套 API、即時 UI、逐回合 trace、save state、replay/分支 | MIT |
| sethkarten/continual-harness（原 pokeagent-speedrun）https://github.com/sethkarten/continual-harness | Emerald（mGBA）、Red（PyBoy） | PokéAgent 官方 harness：sub-agents、A*、屬性表、傷害計算、長期記憶、web UI、MCP | MIT |
| ioannis-mouratidis/ai-plays-pokemon https://github.com/ioannis-mouratidis/ai-plays-pokemon | FireRed（mGBA + Lua socket + mGBA-http） | FireRed RAM 讀取、戰鬥偵測、高階戰鬥動作 | 未確認 |
| Dmrgn/GeminiPlaysPokemonLive https://github.com/Dmrgn/GeminiPlaysPokemonLive | FRLG／Emerald（mGBA-http） | RAM＋截圖→先用小 vision 模型轉文字，再交主 LLM（省 token） | 未確認 |
| lee-security/pss-mgba https://github.com/lee-security/pss-mgba | mGBA-http（Red） | 移動監督（一格一步、等畫面穩定）、卡住記憶、里程碑計分 | 未確認 |
| PWhiddy/PokemonRedExperiments https://github.com/PWhiddy/PokemonRedExperiments | Red（PyBoy，RL） | RAM 獎勵設計、座標探索獎勵；適合之後的小模型 | MIT |
| pret/pokefirered | FireRed 反組譯 | RAM 符號／位址查表（只查，不抄） | 未確認 |
