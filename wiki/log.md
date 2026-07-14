# Log

## [2026-07-15] change | 冷啟動丟訊息 → webhook redelivery + 去重護欄

實測踩雷：閒置縮零後冷啟動 ~15s，期間 LINE webhook 被 abort，第一則訊息無聲消失
（bot 端零紀錄）。解法：LINE Console 開 redelivery（人工開關）＋ text handler 加
`is_redelivery` × 已記錄訊息的去重，避免重送造成雙重回覆（[[operations]]）。
拒絕 min-instances=1：家庭量級不值常駐費。

## [2026-07-14] ingest | TDX 官方 MCP 勘查 → 導訂機制證實，roadmap 更新

github.com/tdxmotc/MCP：官方 rail MCP（車次/票價/導訂）。導訂=產生時效性連結自動帶入
車次資訊，非完成交易；權限需另申請（已於 07-14 送件待審）。決定不接 MCP 進 bot，
核准後直接接導訂 API 進 search_thsr（[[tools]] roadmap 有細節與實測捷徑）。

## [2026-07-14] change | 高鐵查詢 tool（TDX）+ 時間注入

`search_thsr`：TDX 官方 API 查時刻/票價，回覆含訂票連結（訂票無 API，最後一哩留給真人）。
附帶修正：注入現在時間到 user prompt，解決「明天」被猜成訓練資料年份的 bug（[[tools]]）。
新 secret：TDX_CLIENT_SECRET；新 env：TDX_CLIENT_ID（[[deployment]]）。image tag 改用 git SHA。

Append-only。格式：`## [YYYY-MM-DD] <type> | <title>`，type ∈ ingest / change / lint / decision。

## [2026-07-11] change | 混合觸發 + Firestore 持久化 + 安全加固上線

Mention-only 觸發改為混合觸發（[[triggering]]）；ChatStore 加 Firestore 後端（[[architecture]]）；
webhook 隨機路徑、群組白名單、Secret Manager、max-instances=1（[[deployment]]）。
首次部署到 Cloud Run 專案 <your-project-id>。

## [2026-07-11] change | 連發防護 + 觸發敏感度調整 + 純文字回覆

Per-chat lock + supersede 機制；SKIP 判斷 prompt 從「不確定就閉嘴」改為「是不是說給我聽的」；
persona 加入 LINE 不支援 markdown 的指示（[[triggering]]）。

## [2026-07-11] change | 第一批 tools：web_search + remember + 真名解析

ClaudeService 改為手寫 tool-use 迴圈（[[tools]]）；per-chat 記憶文件（[[memory-design]]）；
user ID → 顯示名稱解析。決策：不用 SDK Tool Runner，理由見 [[decisions]]。

## [2026-07-11] change | 修 Firestore 缺欄位 KeyError

`DocumentSnapshot.get(field)` 在欄位不存在時丟 KeyError 而非回 None；
get_memory / in_session 改走 to_dict()。教訓記錄於 [[operations]]。

## [2026-07-12] change | 影片訊息支援（零 token 設計）

Video handler 記錄訊息 + 快取縮圖，不主動呼叫 Claude；quote 影片提問時縮圖才進請求（[[tools]]）。

## [2026-07-12] change | 白名單鎖定三個群組

ALLOWED_CHAT_IDS 填入第一群 / 第二群 / 第三群，其餘群組訊息直接忽略（[[deployment]]）。

## [2026-07-12] ingest | 建立本 wiki

依 Karpathy LLM Wiki 模式建立；schema 寫入 CLAUDE.md。

## [2026-07-13] change | 納入版本控制，推上 GitHub

git init + initial commit d792213，private repo：github.com/ChunChiaoH/family-line-bot。
.env 排除於版控（.gitignore 已涵蓋）。今後 deploy 前先 commit，image tag 可改用 git SHA。

## [2026-07-13] change | KB 升級為 wiki 格式（memory tool）+ 資料遷移

`remember` 退役，改用第一方 `memory_20250818`：Claude 可自主編輯/整理每群的
markdown 記憶檔（[[memory-design]]）。讀取仍整份進 prompt（含反 view 指示，
live 測試驗證有效）。舊扁平 memory 欄位由 Claude 分類遷移至骨架頁，
原文備份於 memory_legacy。決策 D3 前提更新：仍整份注入，但寫入語意升級為 wiki。
