# Current code and next plan (2026-10-13)

Daily plan source of truth: README 「目前進度」／「下一步計劃（2026-10-13）」. Repo stays PUBLIC. Never commit ROM, battery save, savestate, screenshot, model, or secrets. `experiments/` may hold policy JSON, reward JSONL, and notes.

## Current code (summary)

- Default brains: `battle,path,rule`. Situation selection (#91) is on main.
- Path route reuse + experience-db fsync NORMAL (#95). Progress signal on rule/random (#94).
- Experiments CLI (#96): `python -m game_brain.experiments` — no ROM data.
- #98 Pokédex owned/seen on `ram` (verified). #110 shows counts in collapsed 開發者資訊 (Designer OK; page tests 27). Badge bits still unverified — no `ram["badges"]` (metronome ROM; Brock deferred). Money/time candidates only.
- #99 Auto Learn toggle + six-slot party on dashboard. #100/#101 screen buffer + `frame_ack`.
- #104 human-first dashboard default on main (`2816c49`). #105 last ~40 steps in collapsed 開發者資訊 (`1cf5d18`; 439 passed). #108 party icons no black square (`bf125bc`).
- #106 `MB_CAVE_DOOR` 0x60 as `WARP_ENTER` UP (`22e28d5`). #107 free-explore dead warps / NPC TTL / map-edge exits (`cfe72d0`; 442 passed). #111 regression: free-explore map-edge Pallet→Route1 (`39a6065`).
- Taipei 2026-10-12: #113 free-explore nav hint in 開發者資訊 (`e9b7ad0`; page tests 29; Designer OK). #114 PathBrain 15/0 walk north to forest (`cc81fd6`; path tests 64; full real-ROM pytest 451 passed). #115 narrow right panel ~220px (`7fec75f`; page tests 2; Designer OK). Prior docs #112 (`b7345b2`).
- Backend 2026-10-12: real-ROM **entered** forest north bldg **15/3** and Pewter **3/2**; still no new `WARP_ENTER` (`0x60`/`0x65`/`0x69`); Route2 north exit only x=8..11 y=0/1; (5,2) UP blocked by tree; badges still deferred.
- Research: `notes/free-explore-parcel-to-pewter.md` + addendum `notes/free-explore-addendum-2026-10-12.md`.
- Main before this docs PR: `7fec75f`.

## Next plan (2026-10-13)

See README 「下一步計劃（2026-10-13）」:

1. Fullstack: PathBrain / free-explore continue — forest north exit: from `1/0` north mats into `15/3`, walk to `(7,1)`/`(7,2)` then UP out to Route2 `3/20`, then on north edge **x=8..11, y=0/1** UP into Pewter `3/2` (do not UP at `(5,2)`). Add unit/mock tests; no save/log/screenshot commits.
2. Backend: real-ROM adapter observe one Pewter interior (e.g. gym or Pokémon Center; 3/2 doors already map to 6/x); open a `WARP_ENTER` PR only if new verified behavior; still no `ram["badges"]`.
3. Frontend: keep human default as map name + active mon; put the other five plus exact HP/moves behind expand (per Designer 10/12); desktop + 390×844 screenshots for Designer.
4. Designer: confirm Frontend party expand (other five + exact HP/moves) layout; desktop + 390×844 OK before merge.
5. Research: if addendum is already in notes/, short follow-up — Pewter interior next steps; money/time still candidates only; badges still deferred. If today's docs already landed the addendum, do that short note or align next-plan.
