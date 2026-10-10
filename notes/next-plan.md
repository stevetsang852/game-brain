# Current code and next plan (2026-10-11)

Daily plan source of truth: README 「目前進度」／「下一步計劃（2026-10-11）」. Repo stays PUBLIC. Never commit ROM, battery save, savestate, screenshot, model, or secrets. `experiments/` may hold policy JSON, reward JSONL, and notes.

## Current code (summary)

- Default brains: `battle,path,rule`. Situation selection (#91) is on main.
- Path route reuse + experience-db fsync NORMAL (#95). Progress signal on rule/random (#94).
- Experiments CLI (#96): `python -m game_brain.experiments` — no ROM data.
- #98 Pokédex owned/seen on `ram` (verified). Badge bits still unverified — no `ram["badges"]` (metronome ROM; Brock deferred). Money/time candidates only.
- #99 Auto Learn toggle + six-slot party on dashboard. #100/#101 screen buffer + `frame_ack`.
- #104 human-first dashboard default on main (`2816c49`). #105 last ~40 steps in collapsed 開發者資訊 (`1cf5d18`; 439 passed). #108 party icons no black square (`bf125bc`).
- #106 `MB_CAVE_DOOR` 0x60 as `WARP_ENTER` UP (`22e28d5`). #107 free-explore dead warps / NPC TTL / map-edge exits (`cfe72d0`; 442 passed) — can leave Pallet north to Route 1.
- Main before this docs PR: `cfe72d0`.

## Next plan (2026-10-11)

See README 「下一步計劃（2026-10-11）」:

1. Frontend: show verified `pokedex_seen`/`pokedex_owned` counts in collapsed 開發者資訊 only (not human default); no badges/money/time UI; one developer-open screenshot for Designer.
2. Designer: confirm Pokédex count layout in developer info (must not crowd human default); OK before merge.
3. Backend: after #106, keep verifying missing warps/metatiles on Viridian Forest → Pewter path (only proven `WARP_ENTER`); still no `ram["badges"]` until gym 0→1 (defer hard Brock push on this metronome ROM).
4. Research: re-runnable notes for free-explore after parcel: Pallet→Route1→Viridian→Route2→forest south gate (0x60)→toward Pewter; money/time stay candidate offsets only.
5. Fullstack: regression for #107 (mock-house or short real ROM): after goal 17, free explore can leave Pallet to Route 1 via map edge; no save/log/screenshot commits.
