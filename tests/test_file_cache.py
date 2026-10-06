from game_brain.cache import cache_json
from game_brain.data import species


def test_static_tables_and_json_cache_roundtrip():
    assert species(1)["name"] == "bulbasaur"
    again = cache_json("game_brain/data/species.json")
    assert again["1"]["name"] == "bulbasaur"
