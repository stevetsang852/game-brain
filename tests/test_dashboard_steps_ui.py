"""Recent ~40 steps list inside 開發者資訊: 地圖／決定／原因, expandable, no auto-scroll."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"

HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const start = src.indexOf("function fmtAction");
const end = src.indexOf("function renderRomSelection");
let LANG = "zh-Hant";
const MAX_STEPS = 40;
const ACTOR_LABEL = { brain: "大腦", human: "人手", none: "等待" };
const MAP_ZH = { "3/0": "真新鎮", "4/0": "自家一樓", "4/1": "自家二樓", "4/3": "研究所", "3/19": "1 號道路", "3/1": "常青市", "5/3": "友好商店" };
function mapLabel(map) {
  if (!Array.isArray(map) || map.length < 2) return "未知地圖";
  const key = map[0] + "/" + map[1];
  return MAP_ZH[key] || ("地圖 " + key);
}
function el(tag) {
  const node = {
    tagName: (tag || "div").toUpperCase(),
    className: "", textContent: "", title: "", children: [], attributes: {},
    style: {}, scrollTop: 0,
    setAttribute(k, v) { this.attributes[k] = v; },
    append(...nodes) { for (const n of nodes) this.appendChild(n); },
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    insertBefore(c, ref) {
      if (!ref) return this.appendChild(c);
      const i = this.children.indexOf(ref);
      if (i < 0) return this.appendChild(c);
      this.children.splice(i, 0, c); c.parent = this; return c;
    },
    get firstChild() { return this.children[0] || null; },
    get lastChild() { return this.children[this.children.length - 1] || null; },
    remove() {
      if (!this.parent) return;
      const i = this.parent.children.indexOf(this);
      if (i >= 0) this.parent.children.splice(i, 1);
    },
  };
  // Element.remove on child via parent.lastChild.remove — use bound remove
  const self = node;
  node.remove = function () {
    if (!self.parent) return;
    const i = self.parent.children.indexOf(self);
    if (i >= 0) self.parent.children.splice(i, 1);
  };
  return node;
}
const box = el("div");
box.scrollTop = 99; // would change if code auto-scrolled
const state = { lastObs: { ram: { map_bank: 3, map_id: 0 } } };
const $ = id => id === "steps" ? box : null;
const document = { createElement: (tag) => el(tag) };
eval(src.slice(start, end));

const act = { presses: [{ button: "UP", frames: 8 }] };
const d = { plan: "往北", reason: "未探索", brain: "path", actor: "brain", executed: true };
for (let i = 1; i <= 45; i++) {
  state.lastObs = { ram: { map_bank: 3, map_id: i % 2 } };
  prependStepRow({ step: i, mode: "auto" }, { frame: i * 10 }, d, act);
}
const first = box.children[0];
const sum = first.children[0];
const detail = first.children[1];
const out = {
  n: box.children.length,
  max: MAX_STEPS,
  newestStep: sum.children[0].textContent,
  map: sum.children[1].textContent,
  dec: sum.children[2].textContent,
  reason: sum.children[3].textContent,
  cls: first.className,
  tag: first.tagName,
  detailHasFrame: detail.children[0].children.some(c => c.textContent === "frame"),
  scrollTop: box.scrollTop,
  oldestStep: box.children[box.children.length - 1].children[0].children[0].textContent,
  fmt: fmtDecision(d, act),
  propose: fmtDecision({ ...d, executed: false }, act),
};
console.log(JSON.stringify(out));
"""


def _script():
    html = HTML.read_text(encoding="utf-8")
    return html.split("<script>", 1)[1].split("</script>", 1)[0]


def test_page_steps_inside_devinfo_and_columns():
    html = HTML.read_text(encoding="utf-8")
    assert 'id="devInfo"' in html
    # steps list lives inside the collapsed 開發者資訊 block
    i = html.index('id="devInfo"')
    j = html.index("</details>", i)
    chunk = html[i:j]
    assert 'id="steps"' in chunk
    assert "地圖" in chunk and "決定" in chunk and "原因" in chunk
    assert "steps-head" in chunk
    assert "MAX_STEPS = 40" in html
    assert "prependStepRow" in html
    assert "fmtDecision" in html
    # no auto-scroll on the steps helper
    body = html.split("function prependStepRow", 1)[1].split("\n}", 1)[0]
    assert "scrollIntoView" not in body
    assert "scrollTop" not in body
    assert ".scroll" not in body


def test_prepend_step_row_caps_at_40_and_keeps_scroll():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(
        ["node", "-e", HARNESS],
        input=_script(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["n"] == 40 and out["max"] == 40
    assert out["newestStep"] == "#45"
    assert out["oldestStep"] == "#6"  # 45..6 kept after dropping 1..5
    assert out["map"] == "常青市"  # step 45 → map_id 1 → 3/1
    assert "往北" in out["dec"] and "UP 8f" in out["dec"] and "path" in out["dec"]
    assert out["reason"] == "未探索"
    assert out["tag"] == "DETAILS" and "actor-row-brain" in out["cls"]
    assert out["detailHasFrame"] is True
    assert out["scrollTop"] == 99  # unchanged — no auto-scroll
    assert out["fmt"].startswith("往北")
    assert out["propose"].startswith("往北") and "(提議)" in out["propose"]
