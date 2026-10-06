from game_brain.rl.learn_demo import MapBattle, train


def test_map_reaches_door_and_battle_ends():
    env = MapBattle(seed=1)
    env.step("UP")
    env.step("UP")
    env.battle, env.hp = 1, 1
    _, reward, done = env.step("A")
    assert env.battle == 0
    assert reward > 0
    assert done is False


def test_train_improves_or_holds():
    result = train(episodes=30, seed=2)
    assert result["after"] >= result["before"]
    assert result["states"] > 0
