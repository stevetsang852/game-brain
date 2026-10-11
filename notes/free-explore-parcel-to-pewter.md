# FireRed：`deliver_parcel` 後自由探索（真新→常磐→Route2→森林南門→往尼比）可重跑筆記（2026-10-11）

**用途：** 對齊 README「下一步計劃（2026-10-11）」第 4 項（Research）。給 PathBrain／#107 自由探索與 #106 `MB_CAVE_DOOR` `0x60` 做 **parcel 後**回歸：Pallet → Route1 → 常磐 → Route2 → 森林南門（0x60）→ 往尼比方向。  
**真相來源：** `game-brain` README「下一步計劃（2026-10-11）」；main `1b83792`（進度含 #106／#107）。  
**非目標：** 不以 Brock／徽章 0→1 為本篇終點（metronome ROM 暫緩硬衝；徽章仍 **未證明**）。道館細節見前篇，勿重複整段。

---

## 1. 假設

| 項目 | 內容 |
|---|---|
| ROM SHA1 | `e0194282c427689768f8e618a285552f264524a4`（必須對齊） |
| 改版特性 | 御三家 **只會 METRONOME（揮指）** → 森林蟲系／野戰高變異；黑屏後常回常磐中心 |
| 建議 resume | 本機 `~/.game-brain/saves` 的 `milestone-deliver_parcel`（例：`0003945_milestone-deliver_parcel`）。**勿 commit** savestate／sidecar |
| 無 sidecar 時 | 先跑完下方「段 0」再開自由探索 |
| Adapter | main 已有 `WARP_ENTER[0x60]="UP"`（`firered.py`；#106／`22e28d5`；註解 verified e0194282） |
| PathBrain | #107：死 warp／NPC TTL／地圖邊緣出口；goal 17 對白完後改自由探索（可經邊緣由真新北上 Route1） |
| 徽章 | 到達過的存檔徽章 byte 仍 `0x00`；**0→1 未證明** → 不輸出 `ram["badges"]` |

---

## 2. 有序可重跑路徑

地圖 **bank／id** 以 adapter 讀 `SaveBlock1.location` 為準。「本 ROM」欄：Backend FINDINGS 或 PathBrain 已在 SHA1 `e0194282…` 讀回確認者標 **是**；僅 pret／walkthrough 候選標 **否（未查證）**。

### 段 0｜交包裹前（PathBrain 已驗證；resume 上下文）

| 序 | 地圖（bank／id） | 要做什麼 | 本 ROM 已驗證 | 注意 |
|---|---|---|---|---|
| 0.1 | 臥室 4／1 → 1F 4／0 → 真新鎮 3／0 | 開場出屋 | 是 | [`nav.md`](./nav.md) |
| 0.2 | 大木實驗室 4／3 | 選御三家、勁敵戰 | 是 | 輸贏劇情都繼續 |
| 0.3 | 3／0 → Route1 3／19 → 常磐 3／1 | 北上；草叢可打可不打 | 是 | 無固定 trainer；邊緣連線：Pallet (13,0) UP→3／19；Route1 頂 UP→3／1 (25,39) |
| 0.4 | 常磐商店 5／3 | 領 Oak's Parcel | 是 | warp (36,19)→5／3 |
| 0.5 | 回實驗室 4／3 | **`deliver_parcel`**（交包裹、領圖鑑） | 是 | 建議從此 sidecar resume |

### 段 1｜交包裹後自由探索（本篇主體）

| 序 | 地圖（bank／id） | 要做什麼 | 本 ROM 已驗證 | 注意／陷阱 |
|---|---|---|---|---|
| 1 | 實驗室 4／3 → 真新 3／0 | 出實驗室 | 是（PathBrain） | resume 後第一步 |
| 2 | 真新 3／0 → Route1 3／19 | 北緣自由探索出鎮 | 是 | #107 地圖邊緣出口；goal 17 後不應卡死在鎮內 |
| 3 | Route1 3／19 → 常磐 3／1 | 北上進常磐 | 是 | 野生戰可避；METRONOME 慎打 |
| 4 | （建議）常磐寶可夢中心 5／4 | 回血；設定白屏重生點 | **是**（FINDINGS） | 森林黑屏後會重生在 **5／4**，非玩家家。`controls_locked=True` 時先連按 **A** 清鎖再 A\* |
| 5 | 常磐 3／1 北出 → Route2 **3／20** | 北上進 Route2 南段 | **是**（FINDINGS） | 西側往森林；東側樹叢需 Cut（後期，本段跳過） |
| 6 | Route2 → 森林南入口建築 **15／0** | 走入南門建築 | **是**（FINDINGS） | 門口地磚 behavior **`0x60`（`MB_CAVE_DOOR`）**；需 `WARP_ENTER` **UP**（#106）。對準 mats（約 (5,51)／(6,51)）**向上走進去** → 15／0 |
| 7 | 15／0 建築內 → 常青森林 **1／0** | 進森林 | **是**（FINDINGS） | **易卡：** 南側 mats **(7,10)** 會退回 Route2。要先走到北門附近 **(7,1)** 再 **UP** 進森林。driver 須顯式處理，勿只站在南 mats |
| 8 | 常青森林 1／0 往北 | 迷宮往北出口；可繞 Bug Catcher 視線 | **是**（地圖 id）；北出口 mats **是** | 北門 mats **(4–6,9)** behavior **`0x60` UP → 15／3**（Backend 2026-10-11 audit）。METRONOME 易黑屏 → A 清 `controls_locked`。鄰近 Sammy 等對白未清會凍導航。**不必**清全部訓練家 |
| 9 | 森林北 → 北入口建築 **15／3** → Route2 北 → 往尼比 **3／2** | 出森林後北上靠近 Pewter | **15／3 內／3／2：否（未進）** | Route2 側已見 **`0x65` DOWN→15／3** mats；**站進 15／3 內部、尼比 3／2** 因戰鬥未 adapter 驗證。缺口是 **driver／戰鬥**，**不是**欠新 `WARP_ENTER`（3／20、15／0、1／0 上非零 warp 已由 `0x60`／`0x65`／`0x69` 覆蓋） |

### Backend warp 核對補記（2026-10-11）

| 結論 | 內容 |
|---|---|
| **唔使新 PR** | 森林→尼比方向 **無需**再加 `WARP_ENTER` |
| 已覆蓋 | Route2 南／森林南建築／森林：`0x60`／`0x65`／`0x69`（見 FINDINGS） |
| driver | 15／0→1／0：先到 **(7,1)/(7,2)** 再 UP；卡在 (7,10) 是 driver，不是缺 metatile |
| 仍未驗證 | 站在 **15／3** 內部、**3／2** Pewter（戰鬥擋路） |
| 徽章 | 繼續唔出 |

出處：`/workspace/gb-test/badge/FINDINGS.md`（2026-10-11 warp audit）。

---

## 3. 明確停止條件（本篇）

| 達成即停 | 說明 |
|---|---|
| **足夠** | 進入／穩定停留在 Route2 北段，或明顯靠近尼比市（Pewter approach） |
| **足夠（較弱）** | 重複通過 3／20 → 15／0（0x60）→ 1／0，並能從中心黑屏恢復後再進森林（#106／#107 回歸） |
| **超出本篇** | 進尼比道館、打 Brock、驗證徽章 0→1 —— **不做**；見 2026-10-10 Brock 篇與 FINDINGS「NOT PROVEN」 |

徽章提醒（一句）：到達過的 lab／Route2／forest／center 存檔徽章 byte 皆 `0x00`；**0→1 未證明** → metronome ROM 下 Brock 硬衝 **暫緩**。

---

## 4. 金錢／遊戲時間——僅候選（未查證）

**本 ROM 未對過讀數。禁止**寫入 `ram`／dashboard `status`／人類預設 UI。

| 項目 | 候選讀法 | 狀態 |
|---|---|---|
| 金錢 | `p1=u32(0x03005008)`，`p2=u32(0x0300500C)`；`money = u32(p1+0x0290) ^ u32(p2+0xF20)` | **未查證** |
| 時（小時） | SaveBlock2：`u16(p2+0x00E)` | **未查證** |
| 分／秒／VBlank | `u8 +0x010`／`+0x011`／`+0x012` | **未查證** |

細節與陷阱（非 BCD、須 XOR、IWRAM pointer≠EWRAM）見前篇 `2026-10-10-firered-brock-path-money-time.md`（news-research；唔喺本 repo）§B；本篇不重複推導。金錢／時間候選同陷阱亦可對照 [`badge-pokedex-ram.md`](./badge-pokedex-ram.md)。

---

## 5. 不要出貨

| 項目 | 原因 |
|---|---|
| `ram["badges"]` | 徽章 0→1 **未證明**（FINDINGS） |
| 金錢／遊戲時間進 `ram`／status／人類 UI | 僅 pret 候選，**未查證** |
| 把本篇終點設成 Brock | README：metronome ROM 暫緩硬衝；本篇只到「往尼比」自由探索 |
| commit savestate／截圖／ROM | README／`.gitignore` |

已驗證可繼續用：圖鑑 owned／seen（#98）——與本篇路徑無關，勿誤刪。

---

## 6. Backend／Fullstack 驗收清單（自由探索回歸）

1. ROM SHA1 = `e0194282c427689768f8e618a285552f264524a4`。
2. 從 `deliver_parcel` sidecar（或跑完段 0）出發；**勿**把存檔推進 git。
3. PathBrain（#107）能經真新北緣進 Route1，再進常磐，不因死 warp／NPC TTL 永久卡死。
4. Route2 **3／20** → 南門 mats **0x60** UP → **15／0**（#106）；建築內從南 mats 走到 **(7,1)** 再 UP → 森林 **1／0**。
5. 黑屏／護士後：若 `controls_locked`，連按 A 至解鎖再移動。
6. 本篇通過標準：到達 Route2 北或 Pewter approach，**或**穩定重複 3／20→15／0→1／0 並能恢復。**不要求** Brock／徽章。
7. 全程不新增 `ram["badges"]`／money／play_time 輸出。

---

## 7. 來源

| 路徑／連結 | 用途 |
|---|---|
| [`../README.md`](../README.md)「下一步計劃（2026-10-11）」第 4 項（寫作當日） | 本篇範圍真相來源；落地後見現時 README「目前進度」 |
| [`next-plan.md`](./next-plan.md) | 同日計劃摘要 |
| `/workspace/gb-test/badge/FINDINGS.md` | 本 ROM 已確認：3／20、15／0、1／0、5／4、0x60、徽章仍 0x00、METRONOME／controls_locked／(7,10) 卡點 |
| [`../game_brain/adapters/gba_mgba/firered.py`](../game_brain/adapters/gba_mgba/firered.py) | `WARP_ENTER[0x60]="UP"`（#106） |
| `2026-10-10-firered-brock-path-money-time.md`（news-research；唔喺本 repo） | 前篇完整 Brock 路線＋金錢／時間候選；本篇不重複道館 |
| `2026-10-08-firered-badge-dex-offsets.md`（news-research；唔喺本 repo） | 徽章／圖鑑候選（徽章仍未查證） |
| [`badge-pokedex-ram.md`](./badge-pokedex-ram.md) | deliver_parcel sidecar 與徽章 pre-gym 皆 0x00 |
| [`nav.md`](./nav.md) | 段 0 連線與 mart／parcel 導航 |
| pret `map_groups.json`／Bulbapedia Part 2–3 | 北入口 15／3、Pewter 3／2 等 **未在本 ROM 讀回** 的候選 |

---

本檔：[`notes/free-explore-parcel-to-pewter.md`](./free-explore-parcel-to-pewter.md)（docs PR 落地）。
