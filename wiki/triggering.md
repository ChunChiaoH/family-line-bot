---
updated: 2026-07-12
verified-against: handlers/text.py
---

# Triggering — bot 什麼時候回話

家庭群組 bot 最核心的設計問題。原則：**在家庭群組裡插嘴比沉默失禮**。

## 三層規則（handlers/text.py process()）

1. **硬觸發，必回**：@mention bot，或 quote-reply bot 的訊息（LINE 沒有 thread，
   quote-reply 是「想續聊」最明確的訊號）。1:1 私聊視同 mention。
2. **軟觸發，模型判斷**：bot 上次發言後 `SESSION_WINDOW_MINUTES`（預設 10 分鐘）內的
   群組訊息，帶著 `_SKIP_INSTRUCTION` 送 Claude 判斷「這句話是不是說給我聽的」，
   不是就回 `SKIP`（→ 不回覆）。判斷標準經過一次調整：原版「不確定一律 SKIP」太保守，
   實測家人覺得 bot 不敏感，改為以「是否對我說話」為判準。
3. **其他**：不理，只記錄進對話紀錄。

Session window 只由 bot 的**實際回覆**延長；被 SKIP 的判斷不延長 window。

## 連發防護（per-chat lock + supersede）

Handler 在 thread pool 平行執行，快速連發兩句會同時進判斷、互相看不到。解法：

- `_chat_locks[chat_id]`：同群訊息序列處理。
- Supersede：排隊期間若同群有更新訊息進來，舊訊息（軟觸發限定）直接讓位，
  由最新那句帶完整脈絡做一次判斷。硬觸發不會被 supersede（@ 了就一定回）。
- 這些是 in-process 狀態，依賴 max-instances=1（見 [[architecture]]）。

## 錯誤處理的不對稱

Claude API 失敗時：硬觸發回「Sorry, I ran into an issue」（使用者在等回覆）；
軟觸發靜默放棄（本來就可能不回，不要用錯誤訊息洗版）。

## 調參位置

| 想調什麼 | 改哪裡 |
|---|---|
| 續聊窗口長短 | 環境變數 `SESSION_WINDOW_MINUTES`（太吵調短、太遲鈍調長）|
| 判斷積極度 | `services/claude.py` 的 `_SKIP_INSTRUCTION` 措辭 |
| 觀測觸發行為 | log 裡的 `Trigger: mention/reply_to_bot/session/none/superseded`（見 [[operations]]）|
