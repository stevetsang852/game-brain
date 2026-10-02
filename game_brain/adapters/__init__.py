"""Game adapters: turn a running game into Observations and execute Actions."""

from .base import Adapter, GameAdapter

__all__ = ["Adapter", "GameAdapter", "make_adapter"]


def make_adapter(name: str, **kwargs) -> Adapter:
    """Factory used by the demo CLI. "mock" needs nothing; "mgba" needs the
    mGBA Python bindings and a ROM path via $GAME_BRAIN_ROM (see notes/mgba-bridge.md)."""
    if name == "mock":
        from .mock import MockAdapter
        return MockAdapter(**kwargs)
    if name in ("mock-house", "mock_house"):
        from .mock import MockHouseAdapter
        return MockHouseAdapter(**kwargs)
    if name in ("mock-battle", "mock_battle"):
        from .mock import MockBattleAdapter
        return MockBattleAdapter(**kwargs)
    if name in ("mgba", "gba_mgba", "firered"):
        from .gba_mgba import MgbaFireRedAdapter
        return MgbaFireRedAdapter(**kwargs)
    raise ValueError(f"unknown adapter {name!r} (available: mock, mock-house, mock-battle, mgba)")
