import json

import pytest

from game_brain.adapters.mock import MockHouseAdapter
from game_brain.go_explore import GoExploreArchive, GoExploreRunner, cell_key, explorer_action
from game_brain.schema import Observation


def _overworld(x=3, y=2):
    return Observation(frame=0, game="test", ram={
        "scene": "overworld", "in_battle": False, "map_bank": 3, "map_id": 1,
        "player_x": x, "player_y": y, "party_count": 1,
    })


def test_cell_key_uses_progress_for_scoring_but_ignores_battle_and_unknown_position():
    assert cell_key(_overworld(3, 2), ["parcel", "starter"], grid=2) == \
        cell_key(_overworld(2, 3), ["starter", "parcel"], grid=2)
    assert cell_key(_overworld(), ["starter"]) != cell_key(_overworld(), [])
    assert cell_key(Observation(0, ram={"scene": "battle", "in_battle": True}), []) is None
    assert cell_key(Observation(0, ram={"scene": "overworld"}), []) is None


def test_explorer_actions_are_short_and_deterministic():
    move = explorer_action("UP").presses[0]
    talk = explorer_action("A").presses[0]
    wait = explorer_action("NONE").presses[0]
    assert (move.button, move.frames, move.release_frames) == ("UP", 8, 16)
    assert (talk.button, talk.frames, talk.release_frames) == ("A", 2, 14)
    assert (wait.button, wait.frames, wait.release_frames) == ("NONE", 16, 0)


def test_go_explore_persists_cells_and_resumes_lifetime_budget(tmp_path):
    root = tmp_path / "go-explore"
    adapter = MockHouseAdapter(intro_presses=0, start_map=(3, 1), start_pos=(2, 2))
    archive = GoExploreArchive(root, adapter, grid=1, seed=7)
    first = GoExploreRunner(adapter, archive, seed=11, segment_steps=5, stuck_steps=5).run(
        max_steps=20, max_hours=None)

    saved = json.loads((root / "archive.json").read_text())
    assert first["steps"] == 20
    assert saved["namespace"] == "mock-house:synthetic"
    assert saved["cells"]
    assert saved["boot_save"]
    assert all((root / cell["save"]).is_file() for cell in saved["cells"].values())

    resumed = GoExploreRunner(
        adapter, GoExploreArchive(root, adapter, grid=1, seed=7),
        seed=11, segment_steps=5, stuck_steps=5,
    ).run(max_steps=30, max_hours=None)
    assert resumed["steps"] == 30
    assert resumed["cells"] >= first["cells"]


def test_go_explore_rejects_rom_mismatch_and_bad_budget(tmp_path):
    adapter = MockHouseAdapter(intro_presses=0)
    archive = GoExploreArchive(tmp_path / "go-explore", adapter)
    archive.data["namespace"] = "mock-house:different-rom"
    archive.flush()
    with pytest.raises(ValueError, match="different adapter/ROM"):
        GoExploreArchive(tmp_path / "go-explore", adapter)

    fresh = GoExploreArchive(tmp_path / "other", adapter)
    with pytest.raises(ValueError, match="step budget"):
        GoExploreRunner(adapter, fresh).run(max_steps=-1, max_hours=None)
