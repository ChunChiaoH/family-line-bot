---
updated: 2026-07-12
verified-against: services/claude.py
---

# Tools — bot 的能力擴充

## Tool-use 迴圈（services/claude.py ask_text）

手寫迴圈，**不是** SDK 的 Tool Runner。理由：Python SDK 的 Tool Runner 不會自動接續
server tool 的 `pause_turn`（web search 跑太久時 stop_reason 會是 pause_turn，
Runner 會把它當終局訊息回傳 → 靜默截斷回答）。手寫迴圈明確處理：

- `pause_turn` → append assistant content、重送、繼續
- `tool_use` → 執行 handler、append tool_result、繼續
- 其他 → 結束，取 text blocks 為回覆
- 上限 `_MAX_TOOL_ITERATIONS = 5` 防無限迴圈
- Tool handler 拋例外 → 回 `is_error: true` 的 tool_result 給 Claude 讓它調整，不炸整個回覆

若未來 SDK Runner 修了 pause_turn 自動接續，可重評估換回去（換之前查 SDK changelog）。

## 現有 tools

| Tool | 類型 | 成本特性 |
|---|---|---|
| `web_search`（`web_search_20260209`, max_uses=3）| Anthropic server-side | 每千次搜尋 $10 + 結果 token；**只支援 Sonnet 4.6/5、Opus 4.6+，Haiku 不支援** |
| `memory`（`memory_20250818`）| Anthropic client-side，後端 `services/memory.py` | 只在寫入/整理時多 1-2 輪；讀取走 prompt 注入（見 [[memory-design]]，含反 view 指示的理由）|
| `search_thsr` | custom，`services/thsr.py`（TDX 官方 API）| 免費層 3000 次/月；查時刻+票價+對號座剩位（O/L/X，僅涵蓋約一天內班次），回覆含官方訂票連結。訂票本身高鐵無 API（驗證碼防бот），刻意不繞。**TDX 會 429 限速**，_get 內建退避重試 |

（`remember` 自訂 tool 已於 2026-07-13 退役，被 memory tool 完全取代。）

## 時間感知

模型不知道今天幾號（訓練資料時間感），「明天」會猜錯年份——THSR tool 上線時實測踩到。
解法：`ClaudeService._now_line()` 把現在時間（台灣時區，config 可調）注入**user prompt 開頭**。
刻意不放 system prompt：每秒變動的前綴會讓 prompt caching 永遠失效。

日期語義兩條 persona 規則（config.py）：回覆時攤開解析後的日期（「明天（7/14 週二）」，
讓誤解可見可修正）；凌晨 00:00–05:00 的「明天」以當天優先（live 測試驗證通過）。
Prompt 指令的納入門檻：具體、有觸發條件、加後 live 驗證、記錄於此。

## 媒體訊息的零 token 原則

圖片/影片到達時**不呼叫 Claude**，只抓內容/縮圖進快取 + 記錄。
只有被 quote 提問（或 1:1 開 `AUTO_DESCRIBE_IMAGES`）時媒體才進請求。
控制成本的關鍵設計，加新媒體類型時沿用。

## Tool description = 模型的能力自我認知

模型從 tool description 判斷自己會什麼。實際案例：座位查詢程式碼已上線，
但 description 只寫「時刻與票價」，家人問「還有位子嗎」時模型誠實回答「我不能查」
且不呼叫工具。**每次擴充工具能力，description 必須同步更新**，並用能力相關的
問句 live 測試觸發。

## 新增一個 tool 的步驟

1. `services/claude.py`：加 tool 定義 dict（description 要寫**何時**該用，不只功能——
   對觸發率影響很大），append 進 `tools` 列表。
2. Handler：純函式放對應 service；需要 chat_id 等請求上下文的，
   在 `handlers/text.py` 內用 closure 定義後放進 `tool_handlers`。
3. 需要新的 GCP 資源（如 Cloud Tasks）→ 更新 [[deployment]] 的 SA 權限與 deploy.md。
4. Live 測試：參考 scratchpad 模式——真 key 打一發驗證 tool 會被正確觸發。
5. 更新本頁 + [[log]]。

`.claude/commands/add-tool.md` 有 scaffold 指令可用。

## Roadmap（已討論、未實作）

- **高鐵導訂連結**（等 TDX 申請核准，2026-07-14 送件）：核准後把導訂連結（帶入日期/
  起訖站/車次/票數、具時效性）接進 search_thsr 回覆，取代通用訂票連結。
  官方 MCP（https://github.com/tdxmotc/MCP ，server `https://tdx.transportdata.tw/tdx-mcp/rail`）
  證實此機制存在但未公開 URL 組法——細節應隨核准提供；可先在 Claude Code 加該 MCP
  用同一組金鑰實測，觀察實際連結格式再接。不採用 MCP 進 bot：功能比我們的
  REST 實作少（無座位狀態）且共用同一配額。
- **提醒功能**：`set_reminder` tool + Cloud Tasks 排程 + LINE Push API（推播不佔 reply token）。
  **配額設計（2026-07-15 定案想法）**：Push 免費層 200 則/月，用混合投遞省配額——
  到期提醒先掛起，搭下一則家人訊息的 reply token 捎帶出去（一次 reply 可夾 5 個訊息物件）；
  硬時效提醒設短寬限期，逾期才真 Push。另 postback 事件（Flex 按鈕/rich menu）也附
  reply token，按鈕互動不吃推播配額。
- **每日摘要**：Cloud Scheduler 定時觸發，摘要近 24h 對話推播（與提醒共用推播基建）
- **相片問答強化**：藥單/菜單/通知單場景，純 prompt 工作
- 評估過不做：主動閒聊（噪音）、語音轉文字（價值低）、完整影片理解（抽幀成本高）
