---
updated: 2026-07-13
verified-against: services/memory.py / services/claude.py
---

# Memory Design — 長期記憶

KB 是真正的 wiki for LLM：**每群一組 markdown 檔案**，Claude 用 Anthropic 第一方
memory tool（`memory_20250818`）自主編輯——新增、改寫、刪除、改名、整理。
不再是 append-only 清單。

## 讀寫分離的混合架構（核心決策）

- **讀：整份 render 進 system prompt**（`MemoryToolBackend.render()`）。零額外呼叫、
  零延遲，被動事實（過敏、忌口）永遠生效——按需讀取會漏掉「不知道該查」的場景，
  且在小 KB 時每查一次比整份塞更貴（重送全部 context + 多一輪）。
- **寫：memory tool 指令**（create / str_replace / insert / delete / rename）。
  只在 Claude 決定要記或整理時才發生，低頻。
- **反 view 指示**：memory tool 掛上後 API 會自動注入「先 view 記憶目錄」協議，
  會讓每則訊息多 1-2 輪呼叫。`_MEMORY_INSTRUCTION`（services/claude.py）明確反指示
  「內容已附上、不要 view」，live 測試驗證有效（純問答零工具呼叫）。
  **升級模型後要重驗**這個行為。

## 檔案結構與上限

- Firestore：`chats/{chat_id}/memory_files/{filename}`，欄位 content / updated。
- 骨架檔名（在 prompt 中建議、非強制）：members.md（成員與稱謂）、
  preferences.md（偏好與禁忌）、dates.md（重要日期）、agreements.md（約定事項）。
- 總量上限 **4000 字元**（`services/memory.py _TOTAL_LIMIT`），寫入超額時回錯誤
  提示 Claude 先刪舊條目——垃圾回收交給模型「園藝」，不再盲丟最舊行。
- 路徑防護：只接受 `/memories/<平面檔名>`，拒絕巢狀與 traversal。

## 成本護欄與升級條件

三道閘門：上限 4000 字 + Claude 自主整理 + （KB 超過 Sonnet 最低可快取門檻
~2048 tokens 後）prompt caching。**若 KB 逼近 8-10K tokens**，升級路徑不是全按需，
而是中間態：index + 摘要常駐 prompt、細節頁按需 view——那時才啟用 wiki 閱讀模式。

## 舊資料（migration 已完成）

2026-07-13 前的扁平 `memory` 欄位已遷移至 memory_files（Claude 分類進骨架頁），
原文備份在 `memory_legacy` 欄位。確認新版穩定後可清除 legacy 欄位。
`get_memory` / `append_memory` 兩個舊方法保留在 store 供參考，程式已不再呼叫。

## 冷啟動

- 機械層：user ID → 顯示名稱解析（LineService，process-local 快取）。
- 知識層：骨架檔名寫在 prompt 指示裡，Claude 首次記錄時自然建立對應檔
  （live 測試中它主動建了 dates.md）。
