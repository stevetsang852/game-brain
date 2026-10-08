# Current code and next plan (2026-10-08)

Daily plan source of truth: README 「目前進度」／「下一步計劃（2026-10-08）」. Repo stays PUBLIC. Never commit ROM, battery save, savestate, screenshot, model, or secrets. `experiments/` may hold policy JSON, reward JSONL, and notes.

## Current code (summary)

- Default brains: `battle,path,rule`. Situation selection (#91) is on main.
- Path route reuse + experience-db fsync NORMAL (#95). Progress signal on rule/random (#94).
- Experiments CLI (#96): `python -m game_brain.experiments` — no ROM data.
- Party list + `/icons/<id>.png` on main. `set_auto_learn` protocol on main; dashboard HTML still lacks the toggle.
- Badge / Pokédex RAM still unverified on this FireRed ROM only.

## Next plan (2026-10-08)

See README 「下一步計劃（2026-10-08）」: Fullstack auto-learn e2e; Backend badge/Pokédex RAM crosswalk (verified ROM SHA1 only); Frontend Auto Learn toggle + mobile; Designer placement/sidebar spec; Research RAM candidate offsets only.
