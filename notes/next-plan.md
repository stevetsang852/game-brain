# Current code and next plan (2026-10-07)

Repo privacy does not allow committing a FireRed ROM, battery save, savestate, or gameplay screenshot. Those stay ignored. `experiments/` may hold policy JSON, reward JSONL, and notes.

## Current code

- Adapter / Brain / Arbiter. Default brains remain `battle,path,rule`.
- Auto, Assist, and Shadow situation selection is on branch `feat/auto-brain-select`, not necessarily merged: battle asks `llm` then `battle`; verified route asks `path`; unverified probe asks `llm` then `path`; `rl_ready` asks `rl`.
- `llm` is still a stub. PokéLLMon is a Showdown battle reference, not a local model and not the GBA engine.
- RL short-horizon runner and progress score exist. `rl/ppo.py` is a key-weight baseline, not executed PPO.
- Screen refresh flash: `drawRawFrame` must not reset `canvas.width` every changed-tile frame. Noted on `fix/screen-refresh-flash`.

## Next plan

1. Situation selection is merged (#91). Path-cache performance is merged (#95).
2. Badge and Pokédex RAM crosswalk on this ROM only. Needs the verified ROM; do not guess addresses.
3. Keep four low-level brains switchable. Master LLM may read logs and write brain content, not emit the current `ButtonPress`.
4. `python -m game_brain.experiments` writes a JSON and markdown summary under `experiments/`. It refuses ROM, savestate, and screenshot fields.
