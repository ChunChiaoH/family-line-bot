---
updated: 2026-07-17
verified-against: handlers/text.py
---

# Triggering — bot 什麼時候回話

家庭群組 bot 最核心的設計問題。原則：**在家庭群組裡插嘴比沉默失禮**。

## 三層規則（handlers/text.py process()）

1. **硬觸發，必回**：@mention bot，或 quote-reply bot 的訊息（LINE 沒有 thread，
   quote-reply 是「想續聊」最明確的訊號）。1:1 私聊視同 mention。
2. **軟觸發，模型判斷**：bot 上次發言後 `SESSION_WINDOW_MINUTES`（預設 10 分鐘）內的
   群組訊息，帶著 `_SKIP_INSTRUCTION` 送 Claude 判斷「這句話是不是說給我聽的」，
   不是就回 `SKIP`（→ 不回覆）。判斷標準經過兩次調整：
   - 第一次：原版「不確定一律 SKIP」太保守，實測家人覺得 bot 不敏感，
     改為以「是否對我說話」為判準。
   - 第二次（2026-07-17）：實測漏接兩型——(a) bot 以問題結尾、家人回答時句中 @ 了
     其他家人，被誤判成家人互聊；(b) 家人吐槽 bot 本人，bot 已讀不理。
     修法＝兩列式結構（說給你聽的/說給家人聽的）＋針對 (a) 內嵌具體示例
     （試過三版措辭都失敗，**具體示例一次就中**——規則講不贏模型時上例子）。

## SKIP prompt 的調校方法論（2026-07-17 建立，07-18 擴充）

`_SKIP_INSTRUCTION` 是蹺蹺板：加「要回」的案例會讓無關話題也被接（實測發生），
壓「別插嘴」又會把該回的壓死（也實測發生，一個錨點問題讓四案例全 SKIP）。
**每次改動必跑 `scripts/skip_regression.py`**：五個真實場景（三個要回、兩個該 SKIP），
每場景 3 次投票，全數一致才算過。新增誤判案例時把它加進測資再調 prompt。

後續教訓（2026-07-18）：
- **temperature 降到 0.3**（claude.py）：判斷與生成共用同一呼叫，預設 1.0 讓邊界案例
  變擲硬幣——persona 微改幾個字節，同一案例從 3/3 翻到 1/3。降溫後判斷穩定，
  風格由 persona 的範例/負面清單撐，不靠採樣熵。
- **@ ≠ 說話對象**：模型把「@阿華 應該在台北」的 @ 一律讀成「說給阿華聽」，
  但這其實是「向 bot 提及阿華」（跟本人說她自己在哪說不通）。把提及/對話對象的
  區別明寫進 prompt 後，頑固案例 B 才穩定通過。
- **寫記憶不准 SKIP**（案例 E，重演 07-16 事故）：模型曾寫入 memory 後回 SKIP，
  家人以為 bot 壞了。規則加在 _MEMORY_INSTRUCTION：有寫入至少簡短確認。

## 空 @ 的處理（2026-07-18 改）

光桿「@bot」曾被靜默忽略（query 剝掉 mention 後為空 → return），一晚兩次引發
「在嗎」「壞掉了」。現改為：硬觸發的空 query 換成佔位提示讓 Claude 打招呼
（「幹嘛～有事嗎」）；session 訊息剝空（= @ 的是別人）仍然閉嘴。
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
