from game_brain.brain.goals import GoalPlanner


def test_load_without_starter_restarts_script():
    planner = GoalPlanner()
    planner.restore(["intro", "get_starter"])
    assert planner.apply_loaded_goals(0) == "restart_until_starter"
    assert planner.summary()[0]["done"] is False
    assert planner.free_after_starter is False


def test_load_with_starter_switches_to_explore():
    planner = GoalPlanner()
    mode = planner.apply_loaded_goals(1)
    assert mode == "explore_after_starter"
    assert planner.free_after_starter is True
    assert next(item for item in planner.summary() if item["id"] == "get_starter")["done"] is True
