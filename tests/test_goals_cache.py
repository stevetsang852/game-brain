from game_brain.brain.goals import clear_goal_cache, firered_milestones, load_goal_config
from game_brain.cache import cache_get


def test_goals_json_overrides_labels_and_caches():
    clear_goal_cache()
    milestones = firered_milestones()
    config = load_goal_config()
    assert cache_get("goals:firered")["game"] == "firered"
    assert [item["id"] for item in config["goals"]] == [m.id for m in milestones]
    assert milestones[-1].placeholder is True
