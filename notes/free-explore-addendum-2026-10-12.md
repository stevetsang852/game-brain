# Addendum：自由探索筆記補記（2026-10-12）

對齊 README「下一步計劃（2026-10-12）」Research 第 5 項。母篇：[`free-explore-parcel-to-pewter.md`](./free-explore-parcel-to-pewter.md)。

ROM SHA1 仍為 `e0194282c427689768f8e618a285552f264524a4`。徽章繼續暫緩。

---

## 1. 15／3 內部同尼比 3／2——**已真 ROM 進入**（唔係新 metatile）

| 地圖 | 狀態（2026-10-12 Backend） | 說明 |
|---|---|---|
| 森林北建築 **15／3** 內部 | **已進** | 由森林北 mats `(4–6,9)` `0x60` UP → 15／3 `(7,10)`。內部非零 warp：`(7,10)` `0x65` DOWN→1／0；`(7,1)` `0x60` UP→Route2 3／20 `(5,13)` |
| 尼比市 **3／2** | **已進** | Route2 北緣 UP → Pewter `(20,39)`。門：`0x60`／`0x69`（例 (17,6)、(15,16)→6／2 等） |

**唔使新 `WARP_ENTER` PR**：15／3 同 3／2 上非零 enter-relevant behavior 仍由現有 **`0x60`／`0x65`／`0x69`** 覆蓋。

出處：Backend FINDINGS（Enter 15/3 interior + Pewter 3/2, 2026-10-12；本機筆記，唔喺本 repo）。

---

## 2. Driver 要點（唔係欠 metatile）

| 段 | 正確做法 | 不要做 |
|---|---|---|
| 15／0 → 森林 1／0 | 行去 **(7,1)/(7,2)** 再 **UP** | 企喺南 mats **(7,10)** 打轉 |
| 森林 → 15／3 | A\* 到 ~(5,10)，企 `(4–6,9)` 再 UP | — |
| 15／3 → Route2 北 | 行去 **(7,1)/(7,2)** 再 UP（同 15／0 模式） | — |
| Route2 北 → 尼比 | 北緣可出格係 **x=8..11、y=0/1**；再 UP | 喺 **(5,2)** UP（樹擋，唔出圖） |

Fullstack 下一步應對齊：由 `1/0` 北 mats 入 `15/3`，再出 Route2 北緣入尼比 `3/2`。

---

## 3. 金錢／遊戲時間——仍只係候選

| 項目 | 候選 | 狀態 |
|---|---|---|
| 金錢 | `money = u32(p1+0x0290) ^ u32(p2+0xF20)`；`p1=u32(0x03005008)`，`p2=u32(0x0300500C)` | **未查證** → 唔入 `ram`／status／人類 UI |
| 遊戲時間 | SaveBlock2：`u16 +0x00E`（時）、`u8 +0x010`／`+0x011`／`+0x012` | **未查證** → 同上 |

---

## 4. 本 addendum 不做

- 不開代碼 PR、不改 `firered.py`、不輸出 `ram["badges"]`／金錢／時間
- 不以 Brock 為終點

來源：README 2026-10-12；Backend FINDINGS 2026-10-11／2026-10-12；母篇 [`free-explore-parcel-to-pewter.md`](./free-explore-parcel-to-pewter.md)。

本檔：[`notes/free-explore-addendum-2026-10-12.md`](./free-explore-addendum-2026-10-12.md)（docs PR 落地）。
