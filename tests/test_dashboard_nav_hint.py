"""Free-explore / nav hint lives only in developer meta. No ROM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"


def test_page_has_nav_hint_in_dev_info():
    html = HTML.read_text(encoding="utf-8")
    assert 'id="navHint"' in html
    assert 'id="navHint"' in html.split('id="devInfo"', 1)[1].split("</details>", 1)[0]
    assert "renderNavHint" in html and "navIntent" in html
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert 'id="navHint"' not in header


HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const start = src.indexOf("function navIntent");
const end = src.indexOf("// Short Chinese names");
let LANG = "zh-Hant";
const state = { lastObs: null, lastDecision: null };
const els = {};
function $(id) {
  if (!els[id]) els[id] = { textContent: "", hidden: true };
  return els[id];
}
eval(src.slice(start, end));
const out = {};
out.edge = navIntent({ plan: "free explore",
  reason: "at (12,0): step UP off the map edge (map connection) (3 cells)" });
out.warp = navIntent({ plan: "free explore",
  reason: "at (7,1): press UP to take warp (7,0) -> 1/0 (5 cells)" });
out.walk = navIntent({ plan: "free explore", reason: "walk DOWN (2 cells)" });
out.none = navIntent({ plan: "script", reason: "talk to Oak" });
state.lastObs = { ram: { map_bank: 3, map_id: 0 } };
state.lastDecision = { plan: "free explore",
  reason: "at (12,0): step UP off the map edge (map connection) (3 cells)" };
renderNavHint();
out.shown = { hidden: $("navHint").hidden, text: $("navHint").textContent };
state.lastObs = { ram: { map_bank: 3, map_id: 2 } };
state.lastDecision = { plan: "free explore",
  reason: "at (8,5): press UP to take warp (8,4) -> 6/1 (4 cells)" };
renderNavHint();
out.pewter = { hidden: $("navHint").hidden, text: $("navHint").textContent };
state.lastObs = { ram: { map_bank: 6, map_id: 1 } };
state.lastDecision = { plan: "free explore",
  reason: "walk DOWN (2 cells)" };
renderNavHint();
out.indoor = { hidden: $("navHint").hidden, text: $("navHint").textContent };
state.lastObs = { ram: {} };
state.lastDecision = null;
renderNavHint();
out.hidden = { hidden: $("navHint").hidden, text: $("navHint").textContent };
console.log(JSON.stringify(out));
"""


def test_nav_hint_parses_edge_and_warp():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    script = HTML.read_text(encoding="utf-8").split("<script>", 1)[1].split("</script>", 1)[0]
    r = subprocess.run(["node", "-e", HARNESS], input=script, capture_output=True, text=True,
                       encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["edge"] == "邊緣 UP"
    assert out["warp"] == "warp UP → 1/0"
    assert out["walk"] == "自由探索 walk DOWN"
    assert out["none"] == ""
    assert out["shown"]["hidden"] is False and out["shown"]["text"] == "導航 3/0 · 邊緣 UP"
    assert out["pewter"]["hidden"] is False and out["pewter"]["text"] == "導航 3/2 · 目標：尼比室內門（唔係 Route2 北緣）"
    assert out["indoor"]["hidden"] is False and out["indoor"]["text"] == "導航 6/1 · 目標：探索室內（唔係即刻反出）"
    assert out["hidden"]["hidden"] is True
