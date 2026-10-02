# Dashboard protocol

The dashboard is a local web page served by `python -m game_brain.dashboard`.
It talks to the live loop over one WebSocket: `ws://127.0.0.1:8765/ws`.

## Safety

* The server only binds loopback addresses (`127.0.0.1`, `::1`); `--host 0.0.0.0` is refused.
* WebSocket upgrades with a non-local `Origin` header get `403`, so other sites open in the
  same browser cannot drive the game.
* Inbound frames are capped at 64 KiB, must be masked, and may not be fragmented.
* The page can only send `mode_command` and `action`. Any `action` it sends is re-tagged
  `source: "manual"`, so it can never pose as a brain.
* Manual actions are passed to `Arbiter.submit_manual`, which rejects them unless the mode
  is `manual` or `assist` (in `assist` they preempt the brain for that step). The rejection is shown in the page's event list and recorded in
  `arbiter.rejected`.

## Envelope

Every message, both directions: `{"type", "frame", "ts", "payload"}`.
`payload` is the schema message dict without its `type` (see `to_envelope` / `from_envelope`
in `game_brain/schema/messages.py`). Durations are always frames.

| type | direction | payload |
|---|---|---|
| `observation` | server → page | `Observation`: `frame`, `game`, `ram`, `screenshot_b64` (PNG, when the adapter supports screenshots) |
| `decision` | server → page | `Decision`: `brain`, `plan`, `reason`, `mode`, `executed`, `actor` (brain / human / none; missing = brain) |
| `status` | server → page | dashboard-only, not a schema message: `step`, `mode`, `adapter`, `proposed_action`, `executed_action`, `frames_advanced`, `pending_manual`, `notes` |
| `error` | server → page | dashboard-only: `reason` for a refused inbound message |
| `mode_command` | page → server | `ModeCommand`: `mode` ∈ auto / assist / manual / shadow |
| `action` | page → server | `Action`: `presses: [{button, frames, release_frames}]` — manual and assist modes (server side; see `decision.actor`) |

Order per step: `observation`, then `decision`, then `status`. A newly opened tab is sent the
latest envelope of each type first, so it shows the current state immediately.

## Screenshots

When the adapter implements `screenshot()`, a frame is attached every `--screenshot-every`
steps (default 1) as base64 PNG in `observation.payload.screenshot_b64`. Frames are written
to a temp dir outside the repo and are never logged (`Observation.summary()` drops them).
Without screenshots (e.g. `MockAdapter`) the page draws a grid from `ram.player_x/player_y`.
