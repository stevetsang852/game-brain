"""mGBA bridge for Pokemon FireRed (US, BPRE). See notes/mgba-bridge.md."""

from .adapter import MgbaFireRedAdapter
from .firered import FireRedRam

__all__ = ["MgbaFireRedAdapter", "FireRedRam"]
