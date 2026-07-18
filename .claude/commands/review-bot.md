Review the bot's recent real-world behavior in a chat and drive improvements.

適用時機：使用者說「檢視某群組」「看看 bot 最近表現」，或新功能上線後想驗收。
原則：先取證、再判斷、再修；修必回歸；一切落 wiki；部署由使用者拍板。

## 1. 取證（一條指令）

```bash
python scripts/review_chat.py <chat_id> --hours 24
```

chat_id 在 wiki/deployment.md 白名單節。輸出是統一時間軸（訊息 × 觸發判斷 ×
工具呼叫 × token/延遲 × 重投/冷啟動/錯誤）＋統計摘要。

讀時間軸的已知常態（不是 bug，別誤報）：
- 每則處理 >2s 的回覆後面跟一個 🔁 redelivery skipped——LINE webhook 超時重投，
  護欄吸收，正常。非同步化排在提醒功能批次。
- ⚡ trigger=none = 規則層沒觸發（沒 @、沒 quote、不在 session window）——設計行為。
- ❄️ cold start 後第一則訊息靠 redelivery 補投。

## 2. 深挖（視需要）

- 記憶品質：Firestore `chats/{id}/memory_files`（有沒有記垃圾、漏記、格式怪）
- 媒體落地：`gcloud storage ls gs://${PROJECT_ID}-media/chats/{id}/`
- 個別時段完整 log：wiki/operations.md 的撈 log 指令加 timestamp 過濾

## 3. 判斷與報告

對每個異常標記：真 bug / 設計行為被誤解 / 可改善的體驗。好的表現也要記
（語氣、接話、家人反應）。向使用者報告：整體評價 → 問題清單 → 想做的修正
→ 明確說哪些「看過但不動」及理由。

## 4. 修正的既定軌道

- 動 `_SKIP_INSTRUCTION` → **必跑** `scripts/skip_regression.py`（全案例 3/3 才過）；
  新誤判先加進測資再調 prompt。調校教訓（蹺蹺板、@≠對話對象、降溫）見 wiki/triggering.md。
- 動 persona / 行為 → scratchpad 一發 live 驗證新行為。
- 修完：wiki ingest（受影響頁 + log.md 一筆）→ commit → build（tag = git SHA）
  → **AskUserQuestion 請使用者拍板部署** → set_webhook.py 驗證 → 更新 auto-memory。
