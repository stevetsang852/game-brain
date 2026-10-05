"""Loop penalties. A flat repeat penalty is not enough to stop tile farming."""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple


class AntiLoop:
    def __init__(self, window: int = 24, spam_limit: int = 6):
        self.window = window
        self.spam_limit = spam_limit
        self.positions: Deque[Tuple] = deque(maxlen=window)
        self.actions: Deque[str] = deque(maxlen=spam_limit)

    def reset(self) -> None:
        self.positions.clear()
        self.actions.clear()

    def penalty(self, position: Optional[Tuple], button: str) -> float:
        penalty = 0.0
        if position is not None:
            self.positions.append(position)
            if len(self.positions) >= 8:
                unique = len(set(self.positions))
                repeat_rate = 1.0 - unique / len(self.positions)
                if repeat_rate > 0.5:
                    penalty -= 0.2 * (2 ** min(repeat_rate * 4, 4))
        self.actions.append(button)
        if len(self.actions) == self.spam_limit and len(set(self.actions)) == 1:
            penalty -= 1.0
        return penalty
