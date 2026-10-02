# Battle brain 設計提案（第一場勁敵戰同之後）

> 狀態：**只係設計**，未有 code。目標係第一場勁敵戰（研究所，妙蛙種子 Lv5 對小火龍 Lv5），
> 介面要撐得住之後嘅野生戰、訓練員戰，同埋 LLM／Jev 選擇層。
> 戰鬥 RAM 由 **Backend** 負責。我冇自己寫任何位址；引用到嘅位址都係 Backend 提供（`in_battle`）。§3 嘅欄位全部係 **request，未驗證**。

## 0. 而家嘅情況（以 main `733f0bb` 為準）

- 入咗戰鬥之後，`gMain.callback2` 唔再係 `CB2_Overworld`。
  - 所以 adapter 唔會出 `player_x`/`map_*`，`scene` 係 `"other"`。
  - PathBrain 冇位置，就 `BrainUnavailable`。
  - 跟住 RuleBrain 會狂按 A。
- 狂按 A 喺戰鬥入面，大概等於 FIGHT → 游標所喺嗰招。
  - 第一場戰，可能單靠呢招都打得贏（未驗證）。
  - 但係「打贏」係碰彩，唔係設計。
- `Observation.in_battle` 已經喺 schema（`ram["in_battle"]`）。
  - **Backend 嘅 PR**（`feat/in-battle`，commit `7400b7b`，未 merge）會輸出：`in_battle` = `gMain+0x439` 嘅 bit 1。
  - 真 ROM 驗證：勁敵戰期間連續 **874 步**都係 True，打完變返 False，返到研究所 (7,8)。
  - 即係**勁敵戰就喺研究所入面、啱啱攞完御三家之後**發生。
- **`gBattleTypeFlags` 打完戰唔會清零。** 所以唔可以用嚟判斷「係咪喺戰鬥」；只可以喺 `in_battle` True 嗰陣用嚟睇戰鬥類型（Backend）。
- Research Manager（`notes/ml-decision.md`）嘅建議：戰鬥先用 type chart + 傷害計算嘅規則，之後先學。
  Jev／LLM 屬第二批，角色係「喺預先列好嘅選項入面揀，附信心度」。

## 1. 偵測戰鬥同 arbiter 點分流

**建議：唔改 arbiter，用現有嘅 priority list + `BrainUnavailable`。**

```
--brains battle,path,rule
```

| 狀況 | BattleBrain | PathBrain | 結果 |
|---|---|---|---|
| `ram["in_battle"] is True` | 出手 | （唔會問到） | 戰鬥 |
| `in_battle` False | `BrainUnavailable("not in battle")` | 照行路 | 行路 |
| 冇 `in_battle` key（未驗證 / 舊 adapter） | `BrainUnavailable("in_battle not available")` | 冇位置就 unavailable | RuleBrain 狂按 A（即係而家嘅行為） |

- **只信 Backend 驗證過嘅 `in_battle`。** 唔用「`scene == "other"` 而且啱啱觸發咗勁敵」去估。
  - 原因：開場、改名畫面、選單都係 `"other"`。
  - 估錯嘅話，BattleBrain 會喺改名畫面亂撳。
- PathBrain 加一個防呆：`in_battle is True` 就 `BrainUnavailable`。
  - 而家冇位置已經會咁，所以只係雙保險。
- BattleBrain 都會帶 `BrainUnavailable.context`，令 dashboard 每步照有 `milestones` / `battle` 摘要。
  - 呢個機制 PR #12 已經有。
- **戰前過場窗口（Backend 實測）**：`in_battle` 變 True 之前，大約有 **20 步** `scene` 已經係 `"other"`，但 `in_battle` 仍然係 False。
  - 呢段時間 PathBrain 冇位置，RuleBrain 會狂按 A。
  - 建議 PathBrain 加一個 `battle_transition` 狀態，觸發條件：上一步喺 overworld、今步 `scene == "other"`、而且唔係我哋自己觸發嘅 warp。
    - 只出 `wait`（NONE），**唔撳 A**，最多 N 步（例如 40，要喺 ROM 量度）。
    - 期間 `in_battle` 變 True，就交俾 BattleBrain。
    - 超過 N 步都唔係戰鬥（例如其實係選單），就 `BrainUnavailable` → RuleBrain。
  - 點解唔撳 A：A 喺戰鬥第一個選單會揀 FIGHT，令 log 入面「邊個 brain 揀嘅招」唔清楚。
- 再早啲嘅過場（勁敵行過嚟、對白）仍然係 overworld + frozen，由 PathBrain 現有嘅「凍結就撳 `script_button`」處理。

## 2. BattleAction：高層意圖 + 編譯成按鍵

```python
@dataclass(frozen=True)
class BattleAction:            # game_brain/brain/battle/actions.py（提案）
    kind: str                  # "FIGHT" | "SWITCH" | "ITEM" | "RUN"
    slot: int | None = None    # FIGHT: move_slot 0-3；SWITCH: party_slot 0-5
    item_id: int | None = None # ITEM
    target: int | None = None  # ITEM 用喺邊隻（party_slot）；雙打之後再算
    def id(self) -> str: ...   # "FIGHT:0"、"SWITCH:2"、"ITEM:13@0"、"RUN"（dashboard／log 用）
```

### 方案比較

| 方案 | 點做 | 好處 | 壞處 |
|---|---|---|---|
| A. 新 Action 類型（schema 加 `BattleAction` 取代 presses） | adapter 自己識執行 | 簡單直接 | 破壞 schema、replay、dashboard 手動鍵盤；adapter 要識 FireRed UI |
| B. **Brain 層 compiler + `Decision.intent`（可選）** | BattleBrain 揀 BattleAction，`compile()` 逐步出普通 `Action`；Decision 帶 `intent="FIGHT:0"` | `Action` 完全唔變，replay／log／dashboard 照用；同一個 compiler 可以俾 LLM／Jev／Assist 共用 | 一個意圖要幾個 step 先執行完，要喺 brain 入面記住進度 |
| C. `Action.macro` 欄位，由 adapter 展開 | 例如 `{"macro": "FIGHT", "slot": 0}` | 一步搞掂 | adapter 同 UI 綁死；log 入面嘅 `executed_action` 唔再係真按鍵，replay 要靠 adapter 版本 |

**建議 B。**

- `Action` 仍然係 frame-based 按鍵。
- `BattleAction` 只係 brain 入面嘅意圖，記錄喺 `Decision.intent`（可選欄位，冇就唔寫）。

### Compiler（closed-loop，每個 step 睇一次 Observation）

一個 BattleAction 變成一串細 step，每個 step 一個細 Action。下一個 step 做唔做，睇 Observation。

1. **等選單**：`battle_menu == "action"` 先郁；否則撳 A 推對白。
   - 戰鬥對白要用 A；FireRed 戰鬥對白 B 推唔推到，未驗證。
2. **絕對定位，唔靠記憶**：Gen 3 會記住上一次個游標位，所以先撳 `LEFT`、`UP` 去左上角，再按目標行。
   - 假設：撳到邊界唔會 wrap，喺 FireRed 未驗證。
   - Action 選單 2×2：FIGHT（左上）、BAG（右上）、POKéMON（左下）、RUN（右下）。
   - 招式選單 2×2：slot 0 左上、1 右上、2 左下、3 右下。
   - 例：`FIGHT:1` = [LEFT, UP, A] → 等 `battle_menu == "move"` → [LEFT, UP, RIGHT, A]。
   - 例：`RUN` = [LEFT, UP, RIGHT, DOWN, A]。
3. **核對**：如果 Backend 俾到游標位（`battle_cursor`），每按一下都核對；位置唔啱就由第 2 步重嚟。
   - 重試有上限（例如 3 次），超過就 `BrainUnavailable` → RuleBrain。
4. **冇選單狀態時嘅後備（open-loop）**：
   - 每次撳完等固定 frame（例如 tap 2 + release 14，同 PathBrain 一樣），要喺 ROM 量度。
   - 呢個模式只准用喺第一場勁敵戰；log 會標 `reason="open-loop"`。
- 時間全部用 frame 計，冇 wall-clock，所以 replay 一樣係 deterministic。

## 3. BattleObservation：向 Backend 嘅 request（全部未驗證）

建議 adapter 喺 `in_battle` 嗰陣輸出一個 `ram["battle"]` dict，等 overworld key 唔會亂。Brain 用 `BattleObservation.from_ram()` 包一層。

| key | 型別 | 用途 | 優先 |
|---|---|---|---|
| `in_battle` | bool | 分流（§1） | **P0**（Backend `7400b7b` 已驗證，等 merge） |
| `battle.type` | `"wild"`/`"trainer"`（之後有 `"double"`） | RUN 合唔合法；可唔可以用球 | **P0**（可以由 `gBattleTypeFlags` 嚟，但只准喺 `in_battle` True 嗰陣讀，因為佢唔會清零） |
| `battle.menu` | `"action"`/`"move"`/`"party"`/`"bag"`/`"text"`/`"other"` | compiler 等選單（§2） | **P0** |
| `battle.cursor` | int | 核對游標（冇都得，會用 open-loop） | P1 |
| `battle.player` | `{species, level, hp, max_hp, status, moves:[{id, pp, max_pp}]}` | 揀招、PP 用晒 | **P0** |
| `battle.opponent` | `{species, level, hp_pct, status}`（hp_pct 0–100 夠用） | 傷害估算 | **P0** |
| `battle.party` | `[{species, level, hp, max_hp, status, fainted}]` | SWITCH | P1 |
| `battle.turn` | int | log／dashboard；偵測「冇進展」 | P1 |
| `battle.outcome` | `None`/`"win"`/`"lose"`/`"run"`/`"caught"` | milestone done | **P0**（或者用「`in_battle` 由 True 變 False」頂住先） |
| `battle.bag` | `[{item_id, count}]`（只要戰鬥用得嘅） | ITEM | P2 |

- 種族值、招式威力、屬性表**唔使 RAM**。放喺 repo 嘅靜態資料（`game_brain/data/`），資料來源要註明 license。
- 唔好由 ROM 抽。

## 4. Brain 選項（同一個介面）

```python
class BattleBrain(Brain):              # decide(obs) -> (Action, Decision)，同其他 brain 一樣
    def options(self, bo) -> list[BattleAction]   # 合法選項（PP > 0、trainer 戰唔准 RUN …）
    def choose(self, bo, opts) -> tuple[BattleAction, float, str]   # (選擇, confidence 0–1, 原因)
```

`decide()` 由基底類別做：

1. 選單未到就推對白；
2. 到咗 action 選單就 `options()` → `choose()`；
3. 用 compiler 執行；
4. 填 Decision 欄位（§5）。

子類別只寫 `choose()`。

### RuleBattleBrain（第一個實作）

- 估傷害：用簡化 Gen 3 公式 `((2L/5+2)·Power·A/D)/50+2` × STAB × 屬性相剋 × 0.925（平均亂數）。
  - A/D 未有就當 1。
  - 揀期望傷害最高嗰招；冇攻擊招（或者 PP 用晒）就揀狀態招。
- 換隻、用道具、逃走嘅規則，等有需要先加：
  - HP < 20% 而且有傷藥 → ITEM；
  - 野生戰而且唔想打 → RUN。
- **第一場勁敵戰，老實講：**
  - 妙蛙種子 Lv5 只有 Tackle（撞擊）同 Growl（叫聲）。對面小火龍 Lv5 只有 Scratch（抓）同 Growl。
  - 規則基本上即係「每回合 Tackle」，同狂按 A 嘅結果可能一樣。
  - 呢場戰嘅價值係**打通管線**：偵測、選單、compiler、log、milestone。唔係展示策略。
  - 輸贏有亂數；用固定 save state 起步就 deterministic。

### LLMBrain／Jev（第二批，research 建議）

- 輸入：`BattleObservation` 嘅文字／JSON 摘要，加 `options()` 列出嘅選項（每個有 `id` 同 label）。
- 輸出：一個 option id 加 confidence。
- **只可以揀列表入面嘅 option**；揀咗列表以外嘅就當 invalid → fallback 去 RuleBattleBrain。
- Confidence 低過門檻 τ（例如 0.6）：
  - **Assist 模式**：出 `Decision(handoff=True)`，等人手撳（暫時唔郁，wait），dashboard 高亮。
    超過 N 步冇人理，就用 RuleBattleBrain 嘅選擇。
  - **Auto 模式**：直接用 RuleBattleBrain 嘅選擇，log 記低 `confidence`。
- 費用、延遲、API key 未決定。LLMBrain 而家係 stub。
- ML（行為複製、PPO）放最後，要先有足夠 `actor=human` 嘅戰鬥 log。

## 5. Dashboard 欄位（Decision 可選欄位，冇就唔寫，向後兼容）

| 欄位 | 型別 | 例子 |
|---|---|---|
| `intent` | str | `"FIGHT:0"`（正在執行嘅 BattleAction） |
| `battle` | dict | `{"type": "trainer", "turn": 3, "player": {...}, "opponent": {...}}`（§3 嘅摘要） |
| `battle_options` | list | `[{"id": "FIGHT:0", "label": "Tackle", "score": 7.1}, {"id": "FIGHT:1", "label": "Growl", "score": 0.5}]` |
| `chosen_option` | str | `"FIGHT:0"` |
| `confidence` | float 0–1 | `0.92` |
| `handoff` | bool | `true` = 等人手（Assist） |

- 同 `goal`/`path`/`milestones` 一樣喺 `Decision.to_dict()` 省略 `None`，`from_dict()` 讀舊 log 冇問題。
- Frontend：顯示 HP 條同選項分數；`handoff` 嗰陣，Assist 鍵盤加「揀呢個 option」掣。
  - 個掣送出嘅係普通 `Action`，經同一個 compiler 生成，server 端處理。
- runlog：`battle_options` / `battle` 可以照 PR #11 嘅方式「有變先寫」。由 runlog owner 決定。

## 6. Log／replay、測試、milestone

### Determinism

- Log 嘅 `executed_action` 永遠係真按鍵，replay 唔使理 BattleAction，亦唔使再叫 LLM。
- LLM／Jev 本身唔 deterministic，但 log 記低咗佢揀咗乜，replay 照樣 0 mismatch。

### 測試計劃

- `MockBattleAdapter`（唔使 ROM），state machine 包括：
  - 對白（要撳 A）、action／move 2×2 選單（游標會記住上次位置）；
  - HP、PP、trainer 戰 RUN 會被拒；
  - 輸出 §3 嘅 key（`in_battle`、`battle.*`）。
- 單元測試：
  - compiler 由任何游標位置都去到正確格；
  - 冇 `battle.menu` 時行 open-loop；
  - `options()` 唔包 PP 0 嘅招，亦唔包 trainer 戰嘅 RUN；
  - 傷害估算數值；
  - `BrainUnavailable` 分流（battle / path / rule）。
- End-to-end：mock 戰鬥打到 `outcome == "win"`，replay `[]`。
- 真 ROM（冇 ROM 就 skip）：
  - 由 `/tmp` 嘅「啱啱攞完妙蛙種子」save state 起步（唔 commit）；
  - 或者照 PR #12 由開機行，大約 1100 步之後再行向出口。

### Milestone `rival_battle`

- 位置：喺研究所 4/3 入面，攞完御三家之後（Backend run：戰後返到 (7,8)）。
  - 目標：由 (8,5) 行向研究所出口觸發戰鬥。確實觸發格要喺 ROM 確認。
  - PR #12 而家喺 (8,5) 停低 idle。下一步係 `rival_battle` 唔再係 placeholder，target 係出口方向。
- done：見過 `in_battle is True`，之後 `in_battle is False` 而且返到 overworld（4/3）。
  - 有 `battle.outcome` 就順便記低輸贏。
- **FireRed 第一場勁敵戰輸咗都會繼續劇情**（唔會 black out 返屋企）。呢個係大家記得嘅講法，**要喺呢隻 ROM 驗證**：
  1. 用 save state 故意輸一次（例如淨係撳 Growl）；
  2. 睇之後係咪返 4/3、`party_count` 仍然係 1，同埋 HP 有冇回復。
- 如果真係咁，done 唔理輸贏；但 dashboard 要顯示輸贏。

### Caveat：`npcs`

- Backend 重新驗證 `npcs` 嗰陣，8 個 NPC 入面只有 1 個可以用「行埋去撞」驗證。其餘企喺 `#` 格或者行唔到埋去。
- 所以 NPC 位置大部分只係「同畫面吻合」，未逐個撞過。
- 戰鬥 brain 唔靠 `npcs`，但戰前行向出口嘅路線會用到。

## 7. Open questions

1. **Backend**：戰前過場窗口（約 20 步）有冇比 `scene == "other"` 更準嘅訊號？`battle.menu`／游標狀態讀唔讀到？讀唔到嘅話，第一場戰可唔可以接受 open-loop（固定 frame）？
2. **Backend**：`ram["battle"]` 用巢狀 dict，定係平鋪 key（`battle_player_hp` …）？runlog dedupe 邊種易做？
3. 招式／種族／屬性表嘅資料來源同 license（pokefirered 衍生數據？自己打？）。
4. 戰鬥對白用 A 定 B 推？B 會唔會喺某啲提示取消（例如「要唔要換隻」）？要喺 ROM 試。
5. Confidence 門檻 τ 同 `handoff` 等幾耐，由邊個定？Assist 冇人嘅時候係咪一定要 fallback 去 rule？
6. `Decision.intent` 夠唔夠用？定係 overworld 都要用 intent（例如 `"WALK:(8,5)"`）統一？
7. 第一場勁敵戰：要唔要追求必勝（例如先 Growl 兩次）？定係接受亂數，輸咗都當過關？
8. Jev early access 同費用由邊個申請、邊個批？
