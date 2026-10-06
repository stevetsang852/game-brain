from game_brain.brain.goals import GoalPlanner


def test_load_without_starter_restarts_script():
    planner = GoalPlanner()
    planner.restore(["intro", "get_starter"])
    assert planner.apply_loaded_goals(0) == "restart_until_starter"
    assert planner.summary()[0]["done"] is False
    assert planner.free_after_starter is False


def test_load_with_only_starter_switches_to_explore():
    planner = GoalPlanner()
    planner.restore(["intro", "get_starter"])
    assert planner.apply_loaded_goals(1) == "explore_after_starter"
    assert planner.free_after_starter is True


def test_later_saved_goals_are_kept():
    planner = GoalPlanner()
    planner.restore(["intro", "get_starter", "viridian_mart"])
    assert planner.apply_loaded_goals(1) == "keep_saved_goals"
    assert planner.free_after_starter is False
    assert next(item for item in planner.summary() if item["id"] == "viridian_mart")["done"] is True
