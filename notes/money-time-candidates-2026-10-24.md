# Research note: money / game-time candidates remain unverified (2026-10-24)

Source of truth for the daily plan is README 「下一步計劃」. This note records why the money and game-time candidates stay candidates, and why badge RAM stays deferred.

ROM SHA1 remains `e0194282c427689768f8e618a285552f264524a4` (modified US FireRed). No new real-ROM observation was performed for this note.

## Candidates (unchanged from 2026-10-12 addendum)

| Item | Candidate formula | Status |
|---|---|---|
| Money | `money = u32(p1+0x0290) ^ u32(p2+0xF20)` where `p1=u32(0x03005008)`, `p2=u32(0x0300500C)` | **Unverified** — do not expose on `ram`, status, or human UI |
| Game time | SaveBlock2: `u16 +0x00E` (hours), `u8 +0x010` / `+0x011` / `+0x012` | **Unverified** — same |

## Why they stay candidates

Existing verified milestone sidecars (see `notes/badge-pokedex-ram.md`) stop at `deliver_parcel` / early Pewter approach. None of those saves has been shown, in a committed note, to contain a clear before/after transition for money (e.g. 0 → non-zero after a shop or battle reward) or for the clock (ticks while the game is running). Without that observation on this ROM, the pret offsets remain hypotheses.

No code change is made. `firered.py` continues to omit money, game time, and badges from `Observation.ram`.

## Badges

Still **unverified**. No gym has been beaten; the candidate badge byte stays 0 on all checked overworld states. Do not emit `ram["badges"]`.

## What this note does not do

- No new `WARP_ENTER` or adapter behavior
- No real-ROM run
- No screenshots, saves, or logs committed
- No change to progress scoring

Next safe work after this note: any code change that can be unit/mock tested without a real ROM, a new LLM provider, or unverified badge bits. Designer visual confirmation of #117 party expand (desktop + 390×844) still requires human OK and must not commit screenshots. Backend real-ROM observation of one Pewter interior remains deferred until a real ROM is available.
