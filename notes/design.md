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
| assist | yes, unless a human action is queued | yes, unless preempted | **accepted; jumps the queue** (executed before the brain is asked again) |
| manual | no | no (rejected) | executed (queued) |
| shadow | yes | no — logged only; game idles for the same frames | rejected |

   **Assist** (decided by GitHub Mannger): the brain plays as in Auto. A dashboard action
   submitted in Assist is queued and, on the next step(s), executed *instead of* asking the
   brain — the human takes over momentarily. Several queued actions run one per step in
   FIFO order; when the queue is empty the brain resumes. Brain-sourced actions are still
   rejected by `submit_manual`. Switching to Auto/Shadow drops any queued human actions;
   Manual ↔ Assist keeps them.

   Every `Decision` carries **`actor`**: `"brain"` (a brain produced the action — executed in
   Auto/Assist, only proposed in Shadow), `"human"` (a queued dashboard/manual action was
   executed; in Assist this means the brain was preempted), or `"none"` (idle wait).
   Older logs without `actor` parse as `"brain"`.

5. **Run log** — JSONL, one `step` line per decision with observation summary, decision,
   proposed and executed actions, frames advanced. `replay()` re-runs executed actions and
   checks frame + RAM per step. Human actions are logged as the step's `executed_action`
   (`source: "manual"`, `decision.actor: "human"`), so replay needs no live input and stays
   deterministic.

## Why frames, not ms
mGBA steps frame by frame. Expressing every hold/release in frames makes runs
deterministic and replayable regardless of host speed (headless runs at >1000 fps).

## Open questions
* Savestates in `reset()` (start from a fixed state instead of power-on) — Backend.
* LLM prompt/response format and budget once a key exists.
