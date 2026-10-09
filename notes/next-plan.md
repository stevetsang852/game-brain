# Current code and next plan (2026-10-09)

Daily plan source of truth: README 「目前進度」／「下一步計劃（2026-10-09）」. Repo stays PUBLIC. Never commit ROM, battery save, savestate, screenshot, model, or secrets. `experiments/` may hold policy JSON, reward JSONL, and notes.

## Current code (summary)

- Default brains: `battle,path,rule`. Situation selection (#91) is on main.
- Path route reuse + experience-db fsync NORMAL (#95). Progress signal on rule/random (#94).
- Experiments CLI (#96): `python -m game_brain.experiments` — no ROM data.
- #98 Pokédex owned/seen on `ram` (verified). Badge bits still unverified — no `ram["badges"]`.
- #99 Auto Learn toggle + six-slot party on dashboard. #100/#101 screen buffer + `frame_ack` (main `384407d`; real-ROM **437 passed**).
- Human-first UI (default: screen, map, active mon only, one status line, Auto Learn on/off; rest collapsed) is **specced, not implemented**.

## Next plan (2026-10-09)

See README 「下一步計劃（2026-10-09）」: Frontend human-first layout + screenshots; Designer screenshot OK; Backend badge 0→1 on gym save; Research gym path / money-time candidates only; Fullstack collapsed 40-step JSONL log after human UI merges.
