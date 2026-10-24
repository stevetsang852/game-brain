"""Research note and adapter must not expose unverified money / game-time / badges.

No ROM, no saves, no screenshots.
"""

from pathlib import Path

NOTE = Path(__file__).parents[1] / "notes" / "money-time-candidates-2026-10-24.md"
ADAPTER = Path(__file__).parents[1] / "game_brain" / "adapters" / "gba_mgba" / "firered.py"


def test_research_note_records_candidates_as_unverified():
    text = NOTE.read_text(encoding="utf-8")
    assert "Unverified" in text or "未查證" in text or "unverified" in text.lower()
    assert "money" in text.lower()
    assert "game time" in text.lower() or "遊戲時間" in text
    assert "badges" in text.lower() or "徽章" in text
    assert "do not expose" in text.lower() or "唔" in text or "omit" in text.lower()


def test_adapter_source_does_not_emit_money_time_or_badges():
    src = ADAPTER.read_text(encoding="utf-8")
    # Keys that would appear if we exposed them.
    for forbidden in ('"money"', "'money'", '"game_time"', "'game_time'", '"badges"', "'badges'"):
        assert forbidden not in src, f"adapter must not emit {forbidden}"
