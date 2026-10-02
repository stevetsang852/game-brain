"""Local web dashboard: live game view, current plan, recent steps, mode switch, manual pad.

Standard library only. Serves one HTML page and a WebSocket on 127.0.0.1 (never 0.0.0.0).
See notes/dashboard-protocol.md for the wire contract.
"""

from .server import DashboardServer

__all__ = ["DashboardServer"]
