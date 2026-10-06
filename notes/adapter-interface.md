# Adapter interface (contract for the mGBA bridge)

The mGBA/FireRed bridge is owned by **Backend** (developed at `/workspace/mgba` on the shared box).
This repo only ships `MockAdapter`. To plug the real emulator in, implement
`game_brain.adapters.base.Adapter` and register it in `game_brain.adapters.make_adapter`.

```python
class Adapter(ABC):
    name: str                                # e.g. "gba_mgba/firered" (written to run-log header)
    def reset(self) -> Observation: ...      # power-on / fixed start state, return first obs
    def observe(self) -> Observation: ...    # read state; MUST NOT advance the emulator
    def act(self, action: Action) -> int: ...  # run presses frame-by-frame, return frames advanced
    @property
    def frame(self) -> int: ...              # monotonic frame counter since reset
    def screenshot(self, path: str) -> str | None: ...  # PNG of current frame (optional)
    def close(self) -> None: ...
```

## `act(Action)` semantics

For each `ButtonPress(button, frames, release_frames)` in order:

1. hold `button` (or nothing if `"NONE"`) for exactly `frames` emulated frames;
2. then hold nothing for `release_frames` frames.

Return value must equal `action.total_frames`. Durations are **frames, not ms** — no
wall-clock sleeps, so a run is deterministic: same start state + same Actions ⇒ same
frames. `game_brain.runlog.replay()` relies on this (it compares `frame` and `ram` per step;
only RAM fields present in the logged observation are compared, so adding a RAM field doesn't break
replay of older logs).

Button names: `A B SELECT START RIGHT LEFT UP DOWN R L NONE`
(GBA KEYINPUT bits 0..9 in that order, excluding NONE).

## `Observation.ram` keys the brains/dashboard understand

| key | type | meaning |
|---|---|---|
| `player_x`, `player_y` | int | tile coords. **Omit both** while not in the overworld (intro, title, menus before map load) — RuleBrain uses "no position" as "mash A". |
| `map_bank`, `map_id` | int | map group / number |
| `facing` | str | `UP/DOWN/LEFT/RIGHT` |
| `in_battle` | bool | battle flag |
| `party_count` | int | optional |
| `party` | list of dict | optional; mGBA FireRed shape in `game_brain/adapters/gba_mgba/firered_party.py` and notes/party-and-icons.md |
| anything else | any | free-form, game-specific; logged as-is |

`Observation.screenshot_path` / `screenshot_b64` are optional.

## Dashboard interop (Frontend)

WebSocket envelope `{type, frame, ts, payload}`; `game_brain.schema.to_envelope()` /
`from_envelope()` produce/parse it for `observation`, `decision`, `mode_command`, `action`.
Dashboard actions go through `Arbiter.submit_manual()`, which **rejects them unless the mode
is MANUAL or ASSIST** (in Assist they preempt the brain; any action whose `source` starts with
`brain` is always rejected).

## Hints from a quick probe (before the scope change; for Backend to double-check)

Before the team moved emulator work to Backend, a ~10-minute probe on this box ran FireRed
through Debian's `libmgba-dev` 0.10.5 via a tiny ctypes shim (not included in this repo).
Observed, on the supplied ROM:

* ROM header `POKEMON FIRE` / `AGB-BPRE`, rev 0, but **SHA1 `e0194282c427689768f8e618a285552f264524a4`
  does NOT match the clean FireRed US 1.0 dump** (`41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc`)
  nor Rev 1 (`dd5945db…`). CRC32 `dd25f69b` vs. No-Intro `dd88761c`. Treat it as a modified
  image; addresses below matched anyway, but others may not.
* `gMain` @ `0x030030F0`: `callback2` (+0x04) changes on each scene; `vblankCounter2` (+0x24)
  increments 1/frame; `heldKeys` (+0x2C) mirrors pressed keys. Consistent with pokefirered.
* `gSaveBlock1Ptr` @ `0x03005008`: after mashing A through the intro (~4450 frames),
  pos=(6,6), map bank 4 / id 1 (Pallet Town player's house 2F) — matches expectations.
* `gSaveBlock2Ptr` @ `0x0300500C`: player name decodes correctly after the naming screen.
