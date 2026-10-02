# game-brain design note (v0.1)

## Goal
A game-agnostic "brain" that can auto-play games, with a human able to watch and take over.
First target: Pokémon FireRed on mGBA (adapter owned by Backend).

## Layers
1. **Adapter** (per game+emulator) — `reset / observe / act(Action) / screenshot`. Only layer that
   knows RAM addresses or emulator APIs. See `adapter-interface.md`.
2. **Schema** — 4 messages, plain JSON: `Observation`, `Action`, `Decision`, `ModeCommand`.
   Versioned (`v`), validated on construction, and wrapped in Frontend's
   `{type, frame, ts, payload}` envelope for the dashboard.
3. **Brains** — `decide(Observation) -> (Action, Decision)`. Pure: never touch the adapter.
   * `RuleBrain`: mash A without a position / in battle; walk a pattern in the overworld;
     A + rotate when stuck.
   * `RandomBrain`: seeded baseline.
   * `LLMBrain`: stub, raises `BrainUnavailable` (no API calls yet).
4. **Arbiter** — priority list of brains with fallback, plus the mode:

| Mode | brains consulted | brain action executed | dashboard/manual action |
|---|---|---|---|
| auto | yes | yes | rejected |
| assist | yes | yes (*semantics TBD*) | rejected |
| manual | no | no (rejected) | executed (queued) |
| shadow | yes | no — logged only; game idles for the same frames | rejected |

5. **Run log** — JSONL, one `step` line per decision with observation summary, decision,
   proposed and executed actions, frames advanced. `replay()` re-runs executed actions and
   checks frame + RAM per step.

## Why frames, not ms
mGBA steps frame by frame. Expressing every hold/release in frames makes runs
deterministic and replayable regardless of host speed (headless runs at >1000 fps).

## Open questions
* What exactly should ASSIST do (e.g. brain acts but asks for confirmation on risky actions,
  or human acts and brain only suggests)? Currently = AUTO execution.
* Savestates in `reset()` (start from a fixed state instead of power-on) — Backend.
* LLM prompt/response format and budget once a key exists.
