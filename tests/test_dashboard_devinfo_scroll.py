"""開發者資訊: independent scroll; game screen stays in viewport. Page-only."""

from pathlib import Path

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"


def test_devinfo_scroll_keeps_screen():
    html = HTML.read_text()
    assert ".dev-info[open]" in html
    assert "max-height: min(42dvh" in html
    assert ".dev-info[open] .dev-grid" in html
    assert "overflow-y: auto" in html
    assert "grid-template-rows: minmax(0, 1fr) auto" in html
    assert "keepScreenVisibleOnDevInfo" in html
    assert 'id="devInfo"' in html


def test_offline_status_and_party_placeholders():
    html = HTML.read_text()
    assert 'function isConnected()' in html
    assert 'el.textContent = "—"' in html
    assert 'Offline" : "未連線"' in html or "Offline' : '未連線'" in html
    assert 'pill.className = "pill off"' in html
    assert 'if (!isConnected()) { el.textContent' in html or 'if (!isConnected()) { el.textContent = LANG' in html
