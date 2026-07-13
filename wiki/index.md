# Wiki Index

家庭 LINE bot（「Bot」）的知識庫。每頁一行摘要；讀完 index 就知道去哪找答案。
維護規則見 CLAUDE.md 的 Wiki Schema 一節。

## 架構

- [[architecture]] — 元件地圖、呼叫方向、信任邊界；唯一入口是帶簽章驗證的 webhook
- [[deployment]] — GCP 全套（Cloud Run / Firestore / Secret Manager）、部署與環境變數慣例

## 設計

- [[triggering]] — 混合觸發：mention/reply 硬觸發 + session window 軟判斷 + 連發防護
- [[memory-design]] — 長期記憶：整份 memory 文件進 system prompt，刻意不用 RAG
- [[tools]] — tool-use 迴圈（手寫非 Tool Runner）、web_search、remember、新增 tool 的步驟

## 決策與營運

- [[decisions]] — 關鍵決策紀錄：為什麼不用 Vercel、不用 vector DB、模型選 Sonnet 等
- [[operations]] — 撈 log、除錯流程、成本模型、已知地雷（Firestore KeyError 等）

## 時間軸

- [[log]] — append-only 變更紀錄
