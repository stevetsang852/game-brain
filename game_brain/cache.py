"""Process-local cache. Uses lodis when installed, otherwise a dict."""

from __future__ import annotations

from typing import Any, Optional

try:
    from lodis import Redis
    _store = Redis(db=0)
except ImportError:  # pragma: no cover - CI installs lodis
    _store = None
    _fallback: dict[str, Any] = {}


def cache_get(key: str) -> Optional[Any]:
    if _store is None:
        return _fallback.get(key)
    return _store.get(key)


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    if _store is None:
        _fallback[key] = value
        return
    _store.set(key, value, ex=ttl)


def cache_delete(key: str) -> None:
    if _store is None:
        _fallback.pop(key, None)
        return
    _store.delete(key)
