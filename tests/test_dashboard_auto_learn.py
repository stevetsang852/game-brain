"""Page logic for the Auto Learn switch. No ROM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"


def _script():
    html = HTML.read_text(encoding="utf-8")
    return html.split("<script>", 1)[1].split("</script>", 1)[0]


def test_page_has_auto_learn_switch_and_mobile_targets():
    html = HTML.read_text(encoding="utf-8")
    assert 'id="autoLearn"' in html and 'role="switch"' in html
    assert 'cmd: "set_auto_learn"' in html
    assert "後端未支援" in html and "只喺 Auto 模式生效" in html
    assert "grid-template-columns: repeat(3, 48px)" in html
    assert "width: 56px; height: 56px" in html
    assert "repeat(3, 34px)" not in html
    assert "等待畫面…" in html and "等待第一個 observation" not in html
    assert ".screen-card { order: 1; }" in html and ".party-card { order: 2; }" in html and ".mode-card { order: 3; }" in html
    assert 'id="nowLine"' in html and 'id="devInfo"' in html and "<summary>開發者資訊</summary>" in html
    assert "free-stripe" in html and ".bar div.free { animation: none; }" in html
    goal = html.split('id="goal"', 1)[1].split('id="milestones"', 1)[0]
    assert goal.index('id="msBar"') < goal.index('id="phaseSub"')


HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const start = src.indexOf("const MAP_ZH");
const end = src.indexOf("function connect()");
const body = src.slice(start, end);
const els = {};
function el() {
  const node = {
    textContent: "", hidden: true, disabled: false, className: "",
    style: {}, children: [],
    classList: { _c: new Set(), add(x){ this._c.add(x); }, remove(x){ this._c.delete(x); },
      contains(x){ return this._c.has(x); }, toggle(x, on){ if (on) this._c.add(x); else this._c.delete(x); } },
    setAttribute(k, v){ this[k] = v; }, getAttribute(k){ return this[k]; },
    querySelector(){ return this.children[0] || null; },
    appendChild(c){ this.children.push(c); }
  };
  return node;
}
const $ = id => {
  if (!els[id]) {
    els[id] = el();
    if (id === "autoLearnBar") els[id].children.push(el());
  }
  return els[id];
};
let LANG = "zh-Hant";
const state = { ws: { readyState: 1 }, autoLearn: { pending: null, toastStuck: null } };
const sent = [];
function send(env){ sent.push(env); }
eval(body);
function snap() {
  const btn = $("autoLearn"), bar = $("autoLearnBar");
  return {
    disabled: btn.disabled, on: btn.classList.contains("on"), unsupported: btn.classList.contains("unsupported"),
    checked: btn.getAttribute("aria-checked"), note: $("autoLearnNote").textContent,
    bar: bar.hidden ? null : bar.children[0].style.width, spin: !$("autoLearnSpin").hidden,
    phase: $("phase").textContent, sub: $("phaseSub").textContent, toast: $("toast").hidden ? null : $("toast").textContent,
    conn: $("conn").className, free: $("msBar").classList.contains("free")
  };
}
const out = {};
renderAutoLearn({ mode: "auto" });
renderPhase({ mode: "auto" });
out.missing = snap();
renderAutoLearn({ mode: "auto", auto_learn: { enabled: false, stuck_steps: 0, threshold: 300 } });
renderPhase({ mode: "auto", phase: "running", auto_learn: { enabled: false, stuck_steps: 0, threshold: 300 } });
out.off = snap();
renderAutoLearn({ mode: "auto", auto_learn: { enabled: true, stuck_steps: 120, threshold: 300 } });
out.counting = snap();
renderAutoLearn({ mode: "auto", auto_learn: { enabled: true, stuck_steps: 10, threshold: 300 } });
out.early = snap();
renderAutoLearn({ mode: "manual", auto_learn: { enabled: true, stuck_steps: 0, threshold: 300 } });
out.manual = snap();
renderAutoLearn({ mode: "auto", phase: "free_explore", stuck: { step: 4029, map: [3, 0], x: 12, y: 0, reason: "x" },
  auto_learn: { enabled: false, stuck_steps: 0, threshold: 300 } });
renderPhase({ mode: "auto", phase: "free_explore", stuck: { step: 4029, map: [3, 0], x: 12, y: 0 },
  auto_learn: { enabled: false, stuck_steps: 0, threshold: 300 } });
out.switched = snap();
renderPhase({ mode: "auto", phase: "free_explore", stuck: { step: 4029, map: [3, 0], x: 12, y: 0 },
  auto_learn: { enabled: false, stuck_steps: 0, threshold: 300 } });
out.toastOnce = $("toast").textContent;
renderAutoLearn({ mode: "auto", auto_learn: { enabled: false, stuck_steps: 300, threshold: 300 } });
out.hint = snap().note;
state.autoLearn.pending = true;
renderAutoLearn({ mode: "auto", auto_learn: { enabled: false, stuck_steps: 0, threshold: 300 } });
out.waiting = snap();
renderAutoLearn({ mode: "auto", auto_learn: { enabled: true, stuck_steps: 0, threshold: 300 } });
out.confirmed = snap();
$("autoLearn").setAttribute("aria-checked", "false");
$("autoLearn").disabled = false;
$("autoLearn").classList.remove("wait");
toggleAutoLearn();
out.sent = sent[0];
out.pending = state.autoLearn.pending;
state.lastStatus = { mode: "auto", auto_learn: { enabled: true, stuck_steps: 0, threshold: 300 } };
onAutoLearnError({ cmd: "set_auto_learn", reason: "no" });
out.rolled = { pending: state.autoLearn.pending, toast: $("toast").textContent, checked: $("autoLearn").getAttribute("aria-checked") };
onAutoLearnError({ reason: "other" });
out.ignored = state.autoLearn.pending;
console.log(JSON.stringify(out));
"""


def test_auto_learn_switch_states_in_node():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", "-e", HARNESS], input=_script(), capture_output=True, text=True,
                       encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["missing"]["disabled"] and out["missing"]["unsupported"] and out["missing"]["note"] == "後端未支援"
    assert out["off"]["checked"] == "false" and out["off"]["note"] == "" and out["off"]["phase"] == "進行中"
    assert out["counting"]["note"] == "已停 120／300 步" and out["counting"]["bar"] == "40%" and out["counting"]["on"]
    assert out["early"]["note"] == "" and out["early"]["bar"] is None
    assert out["manual"]["disabled"] is False and out["manual"]["on"] and out["manual"]["note"] == "只喺 Auto 模式生效"
    assert "真新鎮" in out["switched"]["sub"] and "4029" in out["switched"]["sub"] and out["switched"]["note"] == ""
    assert out["switched"]["free"] is True and out["off"]["free"] is False
    assert out["toastOnce"] == out["switched"]["toast"]
    assert out["hint"] == "AI 停咗 300 步，開 Auto Learn 可以自動繼續"
    assert out["waiting"]["spin"] and out["waiting"]["checked"] == "false"
    assert out["confirmed"]["checked"] == "true" and out["confirmed"]["spin"] is False
    assert out["sent"] == {"type": "command", "cmd": "set_auto_learn", "enabled": True}
    assert out["rolled"]["pending"] is None and out["rolled"]["toast"] == "自動學習切換失敗"
    assert out["rolled"]["checked"] == "true"  # last confirmed status stays on
    assert out["ignored"] is None

