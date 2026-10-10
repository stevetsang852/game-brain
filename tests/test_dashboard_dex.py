"""Pokédex counts live only in collapsed developer meta. No ROM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"


def test_page_has_dex_pill_in_dev_info():
    html = HTML.read_text(encoding="utf-8")
    assert 'id="dex"' in html
    assert 'id="dex"' in html.split('id="devInfo"', 1)[1].split("</details>", 1)[0]
    assert "renderDex" in html
    assert "pokedex_seen" in html and "pokedex_owned" in html
    # not on the human header
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert 'id="dex"' not in header
    assert 'id="badges"' not in html and 'id="money"' not in html and 'id="gameTime"' not in html


HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const start = src.indexOf("function fmtRam");
const end = src.indexOf("// Short Chinese names");
let LANG = "zh-Hant";
const els = {};
function $(id) {
  if (!els[id]) els[id] = { textContent: "", hidden: true };
  return els[id];
}
eval(src.slice(start, end));
const out = {};
out.fmtSeen = fmtRam("pokedex_seen", [1, 4, 16]);
out.fmtOwned = fmtRam("pokedex_owned", [1]);
renderDex({});
out.missing = { hidden: $("dex").hidden, text: $("dex").textContent };
renderDex({ pokedex_seen: [1, 4], pokedex_owned: [1] });
out.both = { hidden: $("dex").hidden, text: $("dex").textContent };
renderDex({ pokedex_seen: [], pokedex_owned: [] });
out.empty = { hidden: $("dex").hidden, text: $("dex").textContent };
console.log(JSON.stringify(out));
"""


def test_render_dex_counts_in_node():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    script = HTML.read_text(encoding="utf-8").split("<script>", 1)[1].split("</script>", 1)[0]
    r = subprocess.run(["node", "-e", HARNESS], input=script, capture_output=True, text=True,
                       encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["fmtSeen"] == "3 種" and out["fmtOwned"] == "1 種"
    assert out["missing"]["hidden"] is True
    assert out["both"]["hidden"] is False and out["both"]["text"] == "圖鑑 見過 2 · 擁有 1"
    assert out["empty"]["hidden"] is False and out["empty"]["text"] == "圖鑑 見過 0 · 擁有 0"
