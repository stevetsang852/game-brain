# Current code and next plan (2026-10-12)

Daily plan source of truth: README 「目前進度」／「下一步計劃（2026-10-12）」. Repo stays PUBLIC. Never commit ROM, battery save, savestate, screenshot, model, or secrets. `experiments/` may hold policy JSON, reward JSONL, and notes.

## Current code (summary)

- Default brains: `battle,path,rule`. Situation selection (#91) is on main.
- Path route reuse + experience-db fsync NORMAL (#95). Progress signal on rule/random (#94).
- Experiments CLI (#96): `python -m game_brain.experiments` — no ROM data.
- #98 Pokédex owned/seen on `ram` (verified). #110 shows counts in collapsed 開發者資訊 (Designer OK; page tests 27). Badge bits still unverified — no `ram["badges"]` (metronome ROM; Brock deferred). Money/time candidates only.
- #99 Auto Learn toggle + six-slot party on dashboard. #100/#101 screen buffer + `frame_ack`.
- #104 human-first dashboard default on main (`2816c49`). #105 last ~40 steps in collapsed 開發者資訊 (`1cf5d18`; 439 passed). #108 party icons no black square (`bf125bc`).
- #106 `MB_CAVE_DOOR` 0x60 as `WARP_ENTER` UP (`22e28d5`). #107 free-explore dead warps / NPC TTL / map-edge exits (`cfe72d0`; 442 passed). #111 regression: free-explore map-edge Pallet→Route1 (`39a6065`).
- Backend 2026-10-11 forest→Pewter audit: no new `WARP_ENTER` needed on checked maps (`0x60`/`0x65`/`0x69`); 15/0→1/0 needs walk to (7,1)/(7,2) then UP (driver); 15/3 interior and Pewter 3/2 still unverified.
- Research note landed: `notes/free-explore-parcel-to-pewter.md`.
- Main before this docs PR: `39a6065`.

## Next plan (2026-10-12)

See README 「下一步計劃（2026-10-12）」:

1. Fullstack: PathBrain / free-explore driver — in forest south building `15/0`, do not spin on south mats `(7,10)`; walk to `(7,1)`/`(7,2)` then UP into forest `1/0`. Add mock or unit test; no save/log/screenshot commits.
2. Backend: real-ROM enter and adapter-observe forest north building `15/3` interior and Pewter `3/2`; open a `WARP_ENTER` PR only if new verified behavior; still no `ram["badges"]`.
3. Frontend: one free-explore / nav hint line in 開發者資訊 (current map bank/id + PathBrain decision edge/warp intent); not on human default; one developer-open screenshot for Designer.
4. Designer: confirm that nav / free-explore hint layout in developer info (must not crowd human default); OK before merge.
5. Research: once `notes/free-explore-parcel-to-pewter.md` is on main, short addendum — `15/3` interior and `3/2` still unverified, 15/0 driver `(7,1)` UP tip; money/time remain candidates only.
