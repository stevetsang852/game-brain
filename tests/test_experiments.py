import json

import pytest

from game_brain.experiments import ExperimentError, write_summary


def test_summary_writes_json_and_note(tmp_path):
    paths = write_summary(tmp_path, "short-baseline", {
        "algorithm": "global-action-reward-weighting-v1",
        "namespace": "mock",
        "updates": 3,
        "logits": {"A": 0.1},
    })
    saved = json.loads(open(paths["json"], encoding="utf-8").read())
    assert saved["name"] == "short-baseline"
    assert saved["updates"] == 3
    assert "not stored" in open(paths["markdown"], encoding="utf-8").read()


def test_summary_rejects_rom_payload(tmp_path):
    with pytest.raises(ExperimentError):
        write_summary(tmp_path, "bad", {"algorithm": "x", "rom": "firered.gba"})
