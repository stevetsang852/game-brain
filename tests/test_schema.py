import json

import pytest

from game_brain.schema import (Action, ButtonPress, Decision, Mode, ModeCommand, Observation,
                               SchemaError, from_envelope, from_json, to_envelope, to_json)


def test_button_press_validates_frames_and_buttons():
    assert ButtonPress("a", 3).button == "A"
    with pytest.raises(SchemaError):
        ButtonPress("X", 1)
    with pytest.raises(SchemaError):
        ButtonPress("A", 0)
    with pytest.raises(SchemaError):
        ButtonPress("A", 1000)  # looks like milliseconds -> rejected
    with pytest.raises(SchemaError):
        ButtonPress("A", 2.5)


def test_action_total_frames():
    a = Action([ButtonPress("A", 2, 4), ButtonPress("DOWN", 16)])
    assert a.total_frames == 22
    assert Action.wait(8).total_frames == 8


@pytest.mark.parametrize("msg", [
    Action([ButtonPress("START", 2, 6)], source="manual"),
    Observation(frame=42, game="MOCK", ram={"player_x": 1, "player_y": 2, "in_battle": False}),
    Decision(brain="rule", plan="p", reason="r", mode="shadow", executed=False),
    ModeCommand("assist", issued_by="dashboard", reason="test"),
])
def test_json_roundtrip(msg):
    back = from_json(to_json(msg))
    assert back == msg


def test_envelope_matches_frontend_contract():
    env = to_envelope(Decision(brain="rule", plan="p"), frame=7, ts=123.0)
    assert set(env) == {"type", "frame", "ts", "payload"}
    assert env["type"] == "decision" and env["frame"] == 7 and env["ts"] == 123.0
    assert "type" not in env["payload"]
    json.dumps(env)  # must be JSON-serialisable
    for t, msg in [("observation", Observation(frame=1)), ("action", Action.tap("A")),
                   ("mode_command", ModeCommand(Mode.MANUAL))]:
        env = to_envelope(msg, frame=1)
        assert env["type"] == t
        assert from_envelope(json.loads(json.dumps(env))) == msg


def test_envelope_rejects_unknown_type():
    with pytest.raises(SchemaError):
        from_envelope({"type": "hack", "frame": 0, "ts": 0, "payload": {}})


def test_mode_parse():
    assert Mode.parse("SHADOW") is Mode.SHADOW
    with pytest.raises(SchemaError):
        Mode.parse("turbo")


def test_observation_summary_drops_image():
    o = Observation(frame=1, screenshot_b64="AAAA")
    assert "screenshot_b64" not in o.summary()
