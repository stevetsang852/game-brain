"""Raw GBF1 frames stay composited and are acknowledged. No ROM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).parents[1] / "game_brain/dashboard/static/index.html"

HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const start = src.indexOf("let screenBuf");
const end = src.indexOf("function onStatus");
const canvas = { width: 240, height: 160 };
const ctx = {
  createImageData(w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; },
  putImageData(img) { this.last = img.data; },
  getImageData() { throw new Error("readback"); }
};
const state = { displayMode: "auto" };
const sent = [];
function send(env) { sent.push(env); }
const notes = { screenNote: { textContent: "等待畫面…" } };
function $(id) { return notes[id]; }
function ensureScreenSize() {}
eval(src.slice(start, end));
function tile(frame, x, y, rgb) {
  const bytes = new Uint8Array(10 + 4 + 16 * 16 * 3);
  bytes.set([0x47, 0x42, 0x46, 0x31], 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(4, frame, true);
  view.setUint16(8, 1, true);
  bytes[10] = x; bytes[11] = y; bytes[12] = 16; bytes[13] = 16;
  for (let i = 0; i < 16 * 16; i++) {
    bytes[14 + i * 3] = rgb[0];
    bytes[15 + i * 3] = rgb[1];
    bytes[16 + i * 3] = rgb[2];
  }
  return bytes.buffer;
}
drawRawFrame(tile(7, 0, 0, [255, 0, 0]));
drawRawFrame(tile(8, 16, 0, [0, 255, 0]));
const px = ctx.last;
process.stdout.write(JSON.stringify({
  red: px[0], green: px[16 * 4 + 1],
  acks: sent.map(s => s.frame), note: notes.screenNote.textContent
}));
"""


def test_raw_frame_keeps_unchanged_pixels_and_acks():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    proc = subprocess.run(["node", "-e", HARNESS], input=HTML.read_text(encoding="utf-8"),
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["red"] == 255
    assert out["green"] == 255
    assert out["acks"] == [7, 8]
    assert out["note"] == "即時畫面"
