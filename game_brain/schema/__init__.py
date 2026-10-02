"""Shared message schema between adapters, brains, arbiter and dashboard.

Four message types:

* :class:`Observation` -- adapter -> brain / dashboard
* :class:`Action`      -- brain / manual -> adapter (durations in *frames*)
* :class:`Decision`    -- brain -> dashboard
* :class:`ModeCommand` -- dashboard -> arbiter
"""

from .messages import (
    BUTTONS,
    ENVELOPE_TYPES,
    SCHEMA_VERSION,
    Action,
    ButtonPress,
    Decision,
    Mode,
    ModeCommand,
    Observation,
    SchemaError,
    from_envelope,
    from_json,
    to_envelope,
    to_json,
)

__all__ = [
    "BUTTONS",
    "SCHEMA_VERSION",
    "Action",
    "ButtonPress",
    "Decision",
    "Mode",
    "ModeCommand",
    "Observation",
    "SchemaError",
    "ENVELOPE_TYPES",
    "from_envelope",
    "from_json",
    "to_envelope",
    "to_json",
]
