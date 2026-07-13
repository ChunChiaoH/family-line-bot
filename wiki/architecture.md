---
updated: 2026-07-12
verified-against: revision family-line-bot-00006
---

# Architecture

## 元件地圖

```
家人 LINE App → LINE Platform（LY Corp 伺服器）
                    │  HTTPS POST /webhook/<隨機token> + X-Line-Signature
                    ▼
Cloud Run: family-line-bot（asia-east1, max-instances=1）
  app.py ── 簽章驗證（HMAC, channel secret）→ 群組白名單檢查
     │
     ├─ handlers/text.py   觸發判斷 → Claude 回覆（見 [[triggering]]）
     ├─ handlers/image.py  抓圖快取 + 記錄（1:1 可選 auto-describe）
     ├─ handlers/video.py  抓縮圖快取 + 記錄（不呼叫 Claude）
     │
     ├─ store.py / firestore_store.py   對話紀錄 + session 狀態 + 記憶
     ├─ services/claude.py              tool-use 迴圈 → Anthropic API
     └─ services/line_client.py         回覆 / 抓內容 / 查顯示名稱 → LINE API
```

## 呼叫方向與信任邊界

- **唯一打進來的入口**：`POST /webhook/<token>`。三層防護：HMAC 簽章（擋偽造）、
  隨機路徑 token（擋掃描器）、群組白名單（擋陌生群組蹭用）。`GET /health` 公開但無害。
- **全部打出去的呼叫**：Anthropic API（帶 API key）、LINE Messaging API（帶 access token）、
  Firestore（service account ADC）。打出去的呼叫無暴露面，風險只在金鑰保管（見 [[deployment]]）。
- 家人的訊息永遠先經過 LINE 的伺服器再轉發，不會直連本服務。

## 儲存的雙後端設計

`ChatStore`（in-memory，本機開發）和 `FirestoreChatStore`（正式環境，`USE_FIRESTORE=true`）
暴露相同介面：`log_message / log_bot_reply / in_session / get_context / get_message /
get_memory / append_memory`。換儲存後端只要實作這組介面。

Firestore 佈局：

```
chats/{chat_id}                 → last_bot_ts, memory（記憶文字）
chats/{chat_id}/messages/{msg_id} → ts, user, text, type
```

**圖片/影片縮圖 bytes 永遠不進 Firestore**（1MB 文件上限），放 process-local dict，
重啟即失。這是刻意取捨：引用圖片提問通常發生在傳圖後幾分鐘內。

## 單一 instance 假設

`max-instances=1` 不只是省錢——`handlers/text.py` 的 per-chat lock、supersede 追蹤、
圖片快取都是 in-process 狀態，**依賴單一 instance 才正確**。若要 scale out，
這些狀態要搬到 Firestore/Redis，先讀 [[triggering]] 的連發防護一節再動手。
