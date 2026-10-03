"""Keep integration runs' persistent memory out of the user's real archive."""

import pytest


@pytest.fixture(autouse=True)
def isolate_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("GAME_BRAIN_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("GAME_BRAIN_CONFIG_DIR", str(tmp_path / "config"))
