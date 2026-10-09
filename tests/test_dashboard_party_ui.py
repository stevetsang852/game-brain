"""Six-slot party list on the dashboard page. No ROM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"

HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const start = src.indexOf("function renderMapName");
const end = src.indexOf("function drawRawFrame");
let LANG = "zh-Hant";
const state = { partySeen: null };
function mapLabel(map) { return map[0] + "/" + map[1]; }
function el() {
  const node = {
    textContent: "", className: "", style: {}, children: [], dataset: {},
    set innerHTML(v){ this._html = v; this.children = []; if (!v) this.textContent = ""; },
    get innerHTML(){ return this._html || ""; },
    classList: {
      add(x){ node.className = (node.className + " " + x).trim(); },
      contains(x){ return node.className.split(" ").includes(x); },
      toggle(x){ if (this.contains(x)) node.className = node.className.split(" ").filter(c => c !== x).join(" "); else this.add(x); }
    },
    appendChild(c){ this.children.push(c); return c; },
    addEventListener(type, fn){ this["on" + type] = fn; },
    querySelector(){ return this.children.find(c => c.className.split(" ").includes("open")) || null; },
    replaceWith(other){ this.replaced = other; }
  };
  return node;
}
const box = el();
const mapName = el();
const $ = id => id === "partyList" ? box : mapName;
const document = { createElement: () => el() };
eval(src.slice(start, end));
const full = [
  { slot: 0, species_id: 1, species: "BULBASAUR", level: 5, hp: 22, max_hp: 22, active: true,
    moves: [{ name: "TACKLE", pp: 35, max_pp: 35 }] },
  { slot: 1, species_id: 4, species: "CHARMANDER", level: 6, hp: 8, max_hp: 20, status: "burn" },
  { slot: 2, species_id: 7, species: "SQUIRTLE", level: 5, hp: 0, max_hp: 21, status: "paralysis" },
  { slot: 3, species_id: 50, species: "DIGLETT", egg: true },
  { slot: 4, bad_egg: true }
];
renderParty([{ slot: 0, species_id: 1, species: "BULBASAUR", level: 5, hp: 22, max_hp: 22 }]);
renderParty(full);
const rows = box.children;
const line = row => row.children[1].children[0].children.map(c => c.textContent + c.children.map(x => x.textContent).join("")).join(" ");
const fill = row => row.children[1].children[1].children[0];
const out = {
  n: rows.length,
  empty: rows[4].textContent,
  last: rows[5].textContent,
  active: rows[0].className,
  lead: line(rows[0]),
  icon: rows[0].children[0].src,
  eggIcon: rows[3].children[0].src,
  egg: line(rows[3]),
  eggKids: rows[3].children[1].children.length,
  fainted: rows[2].className,
  faintText: line(rows[2]),
  burn: line(rows[1]),
  burnDetail: rows[1].children[1].children[2].textContent,
  mid: fill(rows[1]).className,
  hi: fill(rows[0]).className,
  zero: fill(rows[2]).style.width
};
rows[0].onclick();
out.opened = rows[0].className.includes("open");
out.moves = rows[0].children[1].children[2].textContent;
const img = rows[1].children[0];
img.onerror();
out.fallback = img.replaced.textContent;
renderMapName({ map_bank: 3, map_id: 0 });
out.map = mapName.textContent;
renderParty(full.map(m => m && m.slot === 0 ? Object.assign({}, m, { hp: 10 }) : m));
out.flash = box.children[0].className.includes("flash");
console.log(JSON.stringify(out));
"""


def _script():
    html = HTML.read_text(encoding="utf-8")
    return html.split("<script>", 1)[1].split("</script>", 1)[0]


def test_page_party_card_is_six_slots():
    html = HTML.read_text(encoding="utf-8")
    assert 'id="mapName"' in html and "party-card" in html
    assert "/icons/" in html and "412" in html
    assert "minmax(0, 1fr) 320px" in html


def test_party_rows_in_node():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", "-e", HARNESS], input=_script(), capture_output=True, text=True,
                       encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["n"] == 6
    assert out["empty"] == "空" and out["last"] == "空"
    assert "active" in out["active"] and "出戰" in out["lead"]
    assert out["icon"].endswith("/icons/1.png") and out["eggIcon"].endswith("/icons/412.png")
    assert out["egg"] == "蛋" and out["eggKids"] == 1
    assert "fainted" in out["fainted"] and "昏倒" in out["faintText"]
    assert "22/22" not in out["lead"] and "燒" not in out["burn"]
    assert "燒" in out["burnDetail"] and "8/20" in out["burnDetail"]
    assert out["mid"] == "mid" and out["hi"] == "hi" and out["zero"] == "0%"
    assert out["opened"] and "TACKLE 35/35" in out["moves"] and "22/22" in out["moves"]
    assert out["fallback"] == "C" and out["map"] == "3/0" and out["flash"] is True
