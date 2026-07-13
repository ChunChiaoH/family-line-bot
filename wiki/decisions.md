---
updated: 2026-07-12
---

# Decisions — 關鍵決策紀錄

推翻任何一條之前，先確認當初的前提是否已改變。

## D1: Cloud Run 而非 Vercel / 自架

LINE webhook 是 request-driven 低流量場景，Cloud Run free tier（200 萬 req/月）全罩。
Vercel 是 JS 優先平台，Python 要重構成 serverless function、沒有 Secret Manager 等級的
金鑰管理、連 Firestore 要自管 credentials。**前提**：流量維持家庭規模。

## D2: Firestore 持久化、圖片 bytes 例外

Scale-to-zero 會掉 in-memory context。Firestore 免費層（5 萬讀/日）用不到 1%。
圖片 bytes 因 1MB 文件上限留在記憶體，接受重啟即失。**前提**：引用圖片提問的
時效性短；若家人常翻舊圖提問，考慮 GCS。

## D3: 記憶 = 整份文件進 prompt，不用 RAG

知識量幾 KB，整份注入比檢索便宜且無檢索誤差。見 [[memory-design]]。
**前提**：單群記憶 < 4000 字元上限撐得住實際用量。

## D4: 手寫 tool 迴圈，不用 SDK Tool Runner

Python Tool Runner 不自動接續 `pause_turn`，web search 長跑會被靜默截斷。見 [[tools]]。
**前提**：SDK 未修此行為（換 SDK 版本時查 changelog）。

## D5: 模型用 Sonnet（claude-sonnet-4-6），不降 Haiku

月成本差距僅幾美金，但 SKIP 判斷（社交語境判讀）和多步搜尋整合是 Sonnet 顯著較強的
領域，且新版 `web_search_20260209` 不支援 Haiku。若用量暴增，優化方向是
「SKIP 判斷拆給 Haiku 做二元分類」而非整隻換。**前提**：家庭規模用量。

## D6: max-instances=1

成本上限（被打也只有一台的錢）+ per-chat lock 等 in-process 狀態的正確性前提。
Scale out 前必讀 [[architecture]] 單一 instance 一節。

## D7: 混合觸發而非純規則或全模型判斷

純 mention-only 續聊體驗差；每則全判斷有成本與插嘴風險。折衷見 [[triggering]]。

## D8: 媒體訊息零 token 原則

圖片/影片到達不呼叫 Claude，被引用才處理。使用者明確要求控 token（儲值制、餘額有限）。

## D9: 不做的功能

主動閒聊/每日問候（bot 訊息稀有才有價值）、語音轉文字（家庭語音是講給人聽的）、
完整影片理解（抽幀 + 轉錄成本撐不起長輩轉傳影片的資訊密度——縮圖就夠判斷）。

## 使用者偏好（影響技術選型）

- 第一方方案 > 框架 > 業界實踐 > 論文（CLAUDE.md 有明文）
- 成本敏感：儲值制當硬上限、web_search max_uses=3、媒體零 token 原則
