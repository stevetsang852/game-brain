# Dashboard protocol

The dashboard is a local web page served by `python -m game_brain.dashboard`.
It talks to the live loop over one WebSocket: `ws://127.0.0.1:8765/ws`.

## Safety

* The server only binds loopback addresses (`127.0.0.1`, `::1`); `--host 0.0.0.0` is refused.
* WebSocket upgrades with a non-local `Origin` header get `403`, so other sites open in the
  same browser cannot drive the game.
* Inbound frames are capped at 64 KiB, must be masked, and may not be fragmented.
* The page can only send `mode_command`, `action`, the display messages below and the implemented save commands. Any `action` it sends is re-tagged
  `source: "manual"`, so it can never pose as a brain.
* ROM and local save uploads use same-origin HTTP POSTs with a custom header; a local save upload
  includes its JSON sidecar and base64-encoded state (and optional battery save). The server checks
  format/version, file-name-only sidecar references, size limits, and SHA1 before queuing it.
* Manual actions are passed to `Arbiter.submit_manual`, which rejects them unless the mode
  is `manual` or `assist` (in `assist` they preempt the brain for that step). The rejection is shown in the page's event list and recorded in
  `arbiter.rejected`.

## Envelope

Every message, both directions: `{"type", "frame", "ts", "payload"}`.
`payload` is the schema message dict without its `type` (see `to_envelope` / `from_envelope`
in `game_brain/schema/messages.py`). Durations are always frames.

| type | direction | payload |
|---|---|---|
| `observation` | server → page | `Observation`: `frame`, `game`, `ram`, `screenshot_b64` (PNG, when the adapter supports screenshots) |
| `decision` | server → page | `Decision`: `brain`, `plan`, `reason`, `mode`, `executed`, `actor` (brain / human / none; missing = brain) |
| `status` | server → page | dashboard-only, not a schema message: `step`, `mode`, `adapter`, `proposed_action`, `executed_action`, `frames_advanced`, `pending_manual`, `notes` |
| `error` | server → page | dashboard-only: `reason` for a refused inbound message |
| `mode_command` | page → server | `ModeCommand`: `mode` ∈ auto / assist / manual / shadow |
| `action` | page → server | `Action`: `presses: [{button, frames, release_frames}]` — manual and assist modes (server side; see `decision.actor`) |
| `load_saved_game` | page → server | `{save_id}` from the current save-list status |
| `new_game` | page → server | `{}`; reset current adapter and brain on the configured ROM |

Order per step: `observation`, then `decision`, then `status`. A newly opened tab is sent the
latest envelope of each type first, so it shows the current state immediately.

### Local saves and new game

The Dashboard lists recent game sidecars from the configured save directory at `GET /api/saves`.
`POST /api/save/load` accepts a same-origin `{save_id}` request; the identifier is relative to the
save root, and the server checks that the sidecar and state remain inside that root and validates
the state SHA1 before queuing it. With no explicit `--resume`, Dashboard startup automatically
resumes the newest intact save matching the active adapter and ROM. The `new_game` WebSocket
message clears the active in-memory battery save, resets the adapter and brains, and starts a fresh
episode without stopping the current dashboard run.

### Loading a local game save

The page posts to `POST /api/save` with `Content-Type: application/json` and
`X-Game-Brain-Upload: save`. The body contains `source_name`, the parsed v1 game sidecar, and
base64 `state` / optional `battery` bytes. This endpoint uses the same strict same-origin checks
as ROM uploads and queues the request for the next live-loop step. The active adapter and ROM SHA1
must match; loading resets brains, restores milestone progress, and replaces the active battery
save when provided. Errors are returned as status `notes` and logged as `resume_error`; successful
loads are recorded as `local_save_loaded`.

The Dashboard startup auto-resumes the newest complete save matching the active adapter and ROM,
unless an explicit `--resume` was supplied. Status includes up to 20 recent available game saves
with a save-directory-relative `save_id` (and the active save if it is older); the page marks the pointer's
current latest save, and sends `load_saved_game` for a user-selected entry. The server only accepts
a two-component relative ID, resolves it within the configured save directory, and verifies state
and optional battery SHA1s before applying it. `new_game` starts the adapter from power-on, resets
the brain stack, and opens a fresh experience episode without stopping the Dashboard.

## Screenshots

When the adapter implements `screenshot()`, a frame is attached every `--screenshot-every`
steps (default 1) as base64 PNG in `observation.payload.screenshot_b64`. Frames are written
to a temp dir outside the repo and are never logged (`Observation.summary()` drops them).
Without screenshots (e.g. `MockAdapter`) the page draws a grid from `ram.player_x/player_y`.

## Goal / path / milestones (PR #8 optional `Decision` fields)

The page renders these when present and falls back gracefully when absent (old logs, RuleBrain):

- `goal` -> headline of the "目標與里程碑" panel.
- `milestones` `[{id, label, done}]` -> chips (done = green, first not-done = current) plus a progress bar.
- `path` `[[x, y], ...]` -> green line on the "地圖與規劃路徑" mini-map; first cell is the player, dot marks the end.
- The mini-map draws `ram.collision` (`#` wall / `.` walkable) and `ram.warps` (filled = `enter` set, outlined = unverified).
  Without collision (mock) it draws a plain grid. When the adapter has no screenshot, the main screen shows the same map.
- The RAM table summarises `collision` and `warps` instead of dumping them.

The live wire still carries collision/warps on every observation; log dedupe (map-change-only) does not affect the page.

## NPCs and party count

- `ram.npcs` `[{x, y, local_id, elevation, gfx, ...}]` -> purple squares on the mini-map; hovering one shows its
  `local_id`, position, elevation and gfx. Entries outside the map or without integer x/y are skipped.
- `ram.party_count` -> "隊伍：n 隻" pill in the header ("–" when the adapter does not report it).
- The RAM table shows `npcs` as a count. The live wire carries the full NPC list every step; the log dedupe
  from PR #14 (`npcs_same` / `npcs_delta`) only affects the file.

## Display speed (FPS): `view_config` and `frame_ack`

Display-only messages from the page; they are not part of the game schema, never reach the
arbiter and are never written to the run log. Pacing only changes the sleep between steps and
whether a PNG is attached to the live `observation`, so logs and replay are identical at any FPS.

- `{"type": "view_config", "payload": {"mode": "manual" | "auto", "fps": <number>}}`
  - `manual`: run `fps` steps per second; a screenshot every `--screenshot-every` steps.
  - `auto`: `fps` is a ceiling. A screenshot is only sent once the page acknowledged the previous one,
    and the step rate follows the page's send-to-ack time (1 / latency, clamped 1..ceiling).
    With no tab open, auto sends no screenshots and runs at the ceiling. An unacknowledged frame
    stops blocking after 1 s (closed tab).
  - `fps` outside 1..60 is clamped; a non-number, NaN, bool or unknown `mode` is refused with an `error` envelope.
- `{"type": "frame_ack", "frame": <int >= 0>}`: sent by the page after it has drawn a screenshot (auto only).
- Until the page sends `view_config`, the CLI `--step-delay` / `--screenshot-every` apply unchanged (`mode: "cli"`).
- Every `status` carries `display: {mode, fps, target_fps, actual_fps, frame_fps, page_ms}`; the page shows it under the slider.
- Every `status` carries `starter: {requested, picked, seed}`: `requested` = `random` | `bulbasaur` | `charmander` | `squirtle` (`--starter`, default `random`), `picked` = the starter taken in Oak's lab or `null` before that, `seed` = the seed a random pick comes from: `--seed`, or (no `--seed` with `--starter random`) a seed drawn at random at start, never a placeholder; on `--resume` the save's. The same object is in the log header, the run summary and every save sidecar.

## 存檔 / 續玩（save / resume）協定

> 狀態：**只係協定（docs only）**，未有 code。決定已由 Mannger／Frontend／Backend 定案（2026-10-02）。
> 存檔格式見 [`savestate-format.md`](savestate-format.md)；呢份文件引入 **format_version 2**（加 `brain_state`
> 同 `ai_status` 檔案），v1 照樣讀得。**Backend 請 review**（gym wrapper `reset()` 會跟同一個格式）。

### 分工

| 負責 | 內容 |
|---|---|
| Fullstack | `game_brain/savestate.py`：存檔函數（同 `--save-every` 自動存檔係**同一個** `SaveManager.save()`，包括 `adapter_state`）、v2 sidecar、`ai_status` 檔、`brain_state`（每個 brain 嘅 `export_state()`／`import_state()`）、`save_id` 解析同驗證、resume 函數 |
| Frontend | 兩個按鈕、存檔列表、續玩 UI；`live.py` 收發下面嘅訊息、排隊、喺正確時間點叫 Fullstack 嘅函數 |
| Backend | review `adapter_state`、`party_hp`、v2 格式；gym wrapper `reset()` 對齊 |

### 兩個按鈕

1. **「存遊戲」（game save）**：完整存檔點 = mGBA save state（`.state`）＋ sidecar（`.json`）＋ `brain_state`
   （＋有就 `.sav` 備份）。同 `--save-every`／里程碑自動存檔**完全一樣**，只係 `reason` = `manual`，
   sidecar 多一個 `label`。
2. **「存 AI 狀態」（AI status save）**：另一種檔案，`kind: "ai_status"`，副檔名 `.ai.json`。內容只有
   AI 嘅嘢：`brain_state`、里程碑進度、信心門檻等設定；**冇遊戲 state**。可以套落**任何**一個遊戲存檔
   （`resume_request` 帶 `ai_status_id`），用嚟比較「同一個 AI、唔同起點」。

兩種檔案都係 **format_version 2**。

### 訊息

沿用上面嘅 envelope：`{"type", "frame", "ts", "payload"}`，下面列嘅係 `payload`。所有 request 都要有
`req_id`（字串，1–64 個 `[A-Za-z0-9_-]`，由頁面產生）；所有回覆都**原樣帶返** `req_id`，頁面靠佢配對。
`label`（可選）：最多 64 個字元，去掉控制字元；只寫入 sidecar／ai_status 檔，**唔會**用嚟做檔名。

| type | 方向 | payload | 會改狀態？ |
|---|---|---|---|
| `save_request` | page → server | `{req_id, label?, token?}` | ✅ 要 token（見「安全」） |
| `ai_status_save_request` | page → server | `{req_id, label?, token?}` | ✅ 要 token |
| `list_saves` | page → server | `{req_id}` | ❌ 只係睇（pull） |
| `resume_request` | page → server | `{req_id, save_id, ai_status_id?, milestones?, token?}` | ✅ 要 token |
| `saved` | server → page | `{req_id, save_id, kind, step, frame, map, xy, milestone}` | |
| `saves` | server → page | `{req_id, game: [...], ai_status: [...], truncated}` | |
| `resumed` | server → page | 見下面 | |
| `save_error` | server → page | `{req_id, reason, detail?}` | |

* `saved`：`kind` = `"game"` 或 `"ai_status"`；`ai_status` 時 `save_id` 係嗰個 `ai_status_id`。
  `map` = `[map_bank, map_id]`，`xy` = `[x, y]`（戰鬥中／轉場時係 `null`），`milestone` = 當時第一個未完成嘅里程碑 id。
* `saves`：`live.py` 讀 save dir 入面嘅 sidecar（**唔讀** `.state`），遊戲存檔同 AI 狀態**分開兩個 list**，
  新到舊排，每個 list 最多 200 個（多過就 `truncated: true`）。**唔會傳任何路徑**俾頁面，只有 id。
  * `game[]`：`{save_id, run_id, reason, label, step, frame, map, xy, milestone, in_battle, timestamp,
    format_version, has_brain_state, rom_match, milestones_done}`（`rom_match` = sidecar `rom_sha1` 同而家個 ROM 一樣）。
  * `ai_status[]`：`{ai_status_id, label, brains, milestone, step, frame, timestamp, source_save_id, milestones_done}`。
  * `milestones_done` = `[id, ...]`：**完整**嘅已完成里程碑 id list（照 sidecar／ai_status 檔嘅 `milestones_done`，
    次序同 planner 一樣），唔係淨係最新嗰個。v1 存檔都有呢個欄位（#28 已經寫）。頁面用佢嚟並排比較兩邊進度
    （見下面「里程碑：跟 AI 狀態定跟存檔」）。
* `resumed`：`{req_id, save_id, ai_status_id, autosave_id, run_id, step, frame, map, xy, milestone,
  brain_state: "save" | "ai_status" | "none", milestones: "save" | "ai_status", milestones_done}`。`autosave_id` = 切換之前幫目前個 run 自動存嘅檔；`run_id` =
  新 run（新 log 目錄）。`brain_state` 講明 AI 狀態由邊度嚟（v1 存檔又冇 `ai_status_id` 就係 `"none"`）。
  `milestones` 講明**實際用咗**邊邊嘅里程碑（冇 `ai_status_id` 一定係 `"save"`），`milestones_done` 係套用之後
  嘅完整 list，頁面要用佢更新「目標與里程碑」panel，唔好估。
* `save_error.reason`（固定字串，頁面可以翻譯）：

| reason | 意思 |
|---|---|
| `bad_request` | 欄位缺少／型別錯／`req_id`、`label` 唔合規格；`milestones` 唔係 `"ai_status"`／`"save"`；有 `milestones` 但冇 `ai_status_id` |
| `token_required` / `bad_token` | 綁 `0.0.0.0` 時冇 token／token 錯（見「安全」） |
| `invalid_id` | `save_id`／`ai_status_id` 格式唔啱（有 `/`、`..` 等） |
| `not_found` | 格式啱但 save dir 入面搵唔到對應 sidecar |
| `sha1_mismatch` | `.state` 嘅 SHA1 同 sidecar 唔夾 |
| `rom_mismatch` / `adapter_mismatch` | 存檔唔係呢隻 ROM／呢個 adapter |
| `version_unsupported` | `format_version` 比程式新 |
| `ai_status_incompatible` | `ai_status` 嘅 `brains`（名同次序）同目前個 run 唔一樣 |
| `no_save_support` | adapter 冇 save state（例如 `mock`） |
| `saving_disabled` | 冇 save dir（`--no-save`） |
| `busy` | 排隊已滿（最多 8 個未處理 request） |
| `internal` | 其他錯誤；`detail` 只係簡短訊息，唔會有路徑或 stack trace |

### `save_id` / `ai_status_id`：只係名，唔係路徑

* `save_id` = `<run_id>_<sidecar 檔名去 .json>`，例如 `20261002T070604Z_0003268_milestone-oaks_parcel`。
  `run_id` 係 **UTC**、ISO 8601 basic 加 `Z`（`20261002T070604Z`）；舊 run 用本地時間 `20261002-150604`，照樣接受。
  必須 full-match `^(\d{8}(?:T\d{6}Z|-\d{6})(?:-\d+)?)_(\d{7}_[A-Za-z0-9_-]{1,64})$`；對應檔案係
  `<save_dir>/<group1>/<group2>.json`。v1 同 v2 存檔用同一個規則（目錄結構冇變）。
* `ai_status_id` 必須 full-match `^ai_\d{8}(?:T\d{6}Z|-\d{6})_\d{7}(?:-\d+)?$`（新嘅用 UTC `…T…Z`）；檔案係 `<save_dir>/ai_status/<id>.ai.json`。
* 解析步驟（全部要過，否則 `invalid_id`／`not_found`）：
  1. 唔可以有 `/`、`\`、`..`、NUL；要 full-match 上面嘅 regex。
  2. 拼出路徑之後 `Path.resolve()`，結果一定要喺 `save_dir.resolve()` 入面（`is_relative_to`），
     防 symlink 走出去。
  3. sidecar 要存在，`format` 啱（`game-brain-savestate`／`game-brain-ai-status`），`format_version` ≤ 2。
  4. 遊戲存檔：sidecar 嘅 `state_file` 都要喺同一個目錄、存在、SHA1 夾。
* `run_id` 同一秒有兩個 run 時，`SaveManager` 加 `-2`、`-3`… 後綴（regex 已經容許）。

### 時間點（同 log 對齊）

* `live.py` 收到 request 只係**排隊**（FIFO，最多 8 個，多咗回 `busy`），唔會即刻做。
* 每一步嘅次序：`observe` → `arbiter.step` → `act` → `log.step(...)` → 自動存檔（`after_step`）→
  **處理排隊嘅 request** → 下一步 `observe`。
* 所以按鈕存檔嘅 `step` = 已經執行咗嘅步數（同自動存檔一樣），`frame` = 嗰一刻 adapter 嘅 frame，
  同 log 入面下一個 step 記錄嘅 `frame` 完全一樣；replay 照樣 0 mismatch。
* 每個處理咗嘅 request 都寫一個 log event：`{"kind": "save", reason: "manual", req_id, actor: "human", …}`、
  `{"kind": "ai_status_save", req_id, ai_status_id, step, frame}`、`{"kind": "resume_switch", …}`（見下面）。
* MANUAL 模式下 loop 停咗等人撳掣時，request 一樣喺「下一步 observe 之前」處理（即係即刻），
  因為兩步之間冇其他嘢發生。

### `resume_request` 做咩

1. 驗證 `save_id`（同 `ai_status_id`）——有錯就回 `save_error`，**目前個 run 照行，乜都冇改**。
2. **先幫目前個 run 自動存檔**（`reason: "pre-resume"`），id 放喺回覆嘅 `autosave_id`。
3. 目前 log 寫 `{"kind": "resume_switch", to_save_id, ai_status_id, autosave_id}` 同 `summary`，然後關閉。
4. 開新 run（新 `run_id`、新 log 目錄），header 有 `resumed_from`（同 CLI `--resume` 一樣，再加
   `ai_status_id`（冇就 `null`）同 `has_brain_state`，見 [`savestate-format.md`](savestate-format.md)「Run log header」），
   另外加 `ai_status_from`（有 `ai_status_id` 先有）。
5. `adapter.reset()` → `adapter.load_state(state, frame, adapter_state)`。
6. AI 狀態：有 `ai_status_id` → 用 `ai_status` 嘅 `brain_state`、里程碑、設定；冇 → 用存檔 sidecar 嘅
   `brain_state`（v2）；v1 存檔 → brains `reset()`，只還原里程碑（同 #28 一樣）。
   * 里程碑：有 `ai_status_id` 時睇 `milestones`（冇填 = `"ai_status"`）。`"save"` = 先 `import_state`
     `ai_status` 嘅 `brain_state`，**然後**用遊戲存檔 sidecar 嘅 `milestones_done` 蓋過
     （`planner.restore(...)`，即係 brain_state 入面 PathBrain 嘅 `planner.milestones_done` 都會被換走）。
     冇 `ai_status_id` 時一律用存檔嘅里程碑，唔接受 `milestones` 欄位（`bad_request`）。
7. Arbiter 模式（auto/assist/manual/shadow）**保持唔變**；未處理嘅手動按鍵清空；排隊中嘅其他 request
   喺新 run 照次序處理。
8. 回 `resumed`（broadcast，見下面「回覆送去邊個 tab」）；之後嘅 `observation`/`decision`/`status` 係新 run 嘅。

### 里程碑：跟 AI 狀態定跟存檔

里程碑係 sticky，**預設跟 AI 狀態走**（Mannger 決定：刻意設計，方便比較「同一個 AI、唔同起點」）。
例如將「已經攞咗包裹」嘅 AI 狀態套落一個未入過商店嘅存檔，PathBrain 會當包裹已經攞咗，直接行去研究所。

所以頁面喺**套用之前**（撳「續玩」、帶 `ai_status_id` 嗰陣）一定要：

1. 用 `saves` 入面兩邊嘅 `milestones_done`，**並排**顯示「AI 狀態嘅里程碑」同「遊戲存檔嘅里程碑」
   （同一個 id 對齊；一邊有一邊冇嘅要標出嚟）。
2. 兩個 list 唔一樣就顯示**警告**（例如「AI 狀態話已完成 `oaks_parcel`，但呢個遊戲存檔未做到」）。
3. 俾用戶揀：
   * **「保留 AI 進度」** → `milestones: "ai_status"`（預設選項）；
   * **「重設為遊戲存檔進度」** → `milestones: "save"`。
4. 兩邊一樣時唔使警告，照送 `milestones: "ai_status"`（結果一樣）。
5. 收到 `resumed` 後用回覆嘅 `milestones` 同 `milestones_done` 顯示實際用咗邊邊。

### 回覆送去邊個 tab

* `saved`、`saves`、`save_error`：**只回俾發 request 嗰個 tab**（同一條 WebSocket 連線）。`live.py` 排隊時
  記住來源連線；嗰條連線已經斷咗就唔送（request 照做，log 照寫）。其他 tab 唔會見到。
* `resumed`：**broadcast 俾所有 tab**，因為成個 run 換咗。`req_id` 照帶（發 request 嗰個 tab 靠佢配對）；
  其他 tab 收到都要：
  * **清空 step table**（舊 run 嘅步數唔再連續）；
  * **清空戰鬥 panel 狀態**（HP、對手、intent 等，等新 run 嘅 `observation` 再畫）；
  * 用 `milestones_done` 更新里程碑 panel，事件列表加一行「已切換到 `<save_id>`」。
* 「新開 tab 先收最新 envelope」只限 `observation`／`decision`／`status`；`saved`／`saves`／`save_error`／`resumed`
  **唔會**重播俾新 tab（新 tab 要自己 `list_saves`）。

### 安全

* 平時 server 只綁 loopback（見上面「Safety」）；唯一例外係 container 入面綁 `0.0.0.0`
  （`GAME_BRAIN_IN_CONTAINER=1` 加 container marker，host 只 publish `127.0.0.1`）。
* **綁 `0.0.0.0` 時，會改狀態嘅訊息（`save_request`、`ai_status_save_request`、`resume_request`）預設拒絕，
  除非帶啱 token**。只係睇嘅訊息（`list_saves`、`view_config`、`frame_ack`）唔使 token。
  `mode_command`／`action` 維持現狀（唔喺今次範圍）。
* Token：
  * 啟動時用 `secrets.token_urlsafe(32)` 產生（或者用 `$GAME_BRAIN_CONTROL_TOKEN`，至少 32 個字元，太短就拒絕啟動）。
  * 只喺啟動時**印一次到 stderr**：`control token: <token>` 同埋
    `open http://127.0.0.1:8765/#token=<token>`（放喺 URL fragment，fragment 唔會出現喺 HTTP request／log）。
  * 頁面由 `location.hash` 讀 token，存 `sessionStorage`，然後用 `history.replaceState` 清走 hash；
    每個會改狀態嘅 request 放喺 `payload.token`。
  * Server 用 `hmac.compare_digest` 比較；token **唔會**寫入 run log、`status`、sidecar 或者任何回覆。
    Run log 寫 request 時會刪走 `token` 欄位。
  * 錯 token 回 `bad_token`；冇 token 回 `token_required`。
* 綁 loopback 時預設唔使 token（同一部機）；`--require-token` 可以強制要。

#### Docker／`start.bat`：token 由 `start.bat` 產生

用 `start.bat`（Docker Desktop）嗰陣，token **由 `start.bat` 自己產生**，唔使用戶睇 container log 抄：

1. `start.bat` 用 PowerShell 產生（例如 `[Security.Cryptography.RandomNumberGenerator]` 32 bytes → base64url，
   ≥ 32 字元），放入**只喺 `setlocal` 入面**嘅變數 `GAME_BRAIN_CONTROL_TOKEN`。每次啟動都換一個新 token。
2. `docker compose up -d --build`：`compose.yaml` 用 `environment: [GAME_BRAIN_CONTROL_TOKEN]`（**冇值**，
   即係由 host 環境變數傳入），所以 token 唔會出現喺 `compose.yaml`、`.env` 或者 image 入面。
3. 等 dashboard 起好（poll `http://127.0.0.1:8765/`，有 timeout），然後
   `start "" "http://127.0.0.1:8765/#token=%GAME_BRAIN_CONTROL_TOKEN%"` 開瀏覽器。頁面讀完 hash 就
   `history.replaceState` 清走（同上面一樣）。
4. **Token 永遠唔寫入任何檔案或者 log：**
   * `start.bat` 唔 `echo` token（`@echo off`），唔寫 `.env`／暫存檔，唔用 `docker compose config`。
   * Server 見到 token 係由 `$GAME_BRAIN_CONTROL_TOKEN` 嚟，就**唔印**佢（`docker logs` 都係 log），只印
     `control token: (from GAME_BRAIN_CONTROL_TOKEN)`。自己產生 token 嗰陣先照上面印一次到 stderr。
   * Run log、`status`、sidecar、ai_status 檔、任何回覆都冇 token（同上面）。
5. 已知限制（唔係我哋寫嘅檔案，記低俾大家知）：`docker inspect` 睇得到 container 環境變數（只限本機
   Docker 用戶）；瀏覽器開 URL 嗰下可能入咗歷史紀錄（頁面即刻 `replaceState`，而且下次 `start.bat` 就換
   token，舊 token 即失效）。
* 原有限制照舊：非本機 `Origin` 403、frame 上限 64 KiB、只收已知 `type`。
