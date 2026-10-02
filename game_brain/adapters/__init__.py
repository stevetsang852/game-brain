"""Game adapters: turn a running game into Observations and execute Actions."""

from .base import Adapter, GameAdapter

__all__ = ["Adapter", "GameAdapter", "make_adapter"]


def make_adapter(name: str, **kwargs) -> Adapter:
    """Factory used by the demo CLI. Only "mock" ships in this package for now;
    the mGBA/FireRed bridge is developed separately (see notes/adapter-interface.md)."""
    if name == "mock":
        from .mock import MockAdapter
        return MockAdapter(**kwargs)
    raise ValueError(f"unknown adapter {name!r} (available: mock)")
