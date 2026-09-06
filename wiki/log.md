# Log

## [2026-09-06] change | 離線單元測試 167 個（tests/）

原本 repo 只有要花 API 的 `scripts/skip_regression.py`。新增 pytest 套件，全部離線，約 9 秒：
觸發三層規則與 supersede／redelivery guard（test_triggering）、tool loop 在 client 邊界 mock
（test_claude_service：SKIP 哨兵、pause_turn、iteration 上限、cache_control）、memory 工具
指令與 4000 字預算（test_memory_backend）、THSR 站名解析與排版（test_thsr）、store 視窗修剪、
webhook HMAC／白名單／path token 走真 TestClient（test_webhook）、媒體 handler、config。
分工：離線測試管「水管」，SKIP 判斷品質仍歸 skip_regression。生產碼零改動；只加
pyproject `dev` extras 與 pytest 設定。`/deploy` step 1 現在有東西可跑。
寫測試時發現、尚未處理的四點：(1) hard trigger 下 tool loop 耗盡 iteration 或停在 pause_turn
時 `ask_text` 回 None，text.py 把 None 當 SKIP 而沉默——正是 [[triggering]] 記的「在嗎／壞掉了」
症狀，應改為 may_skip=False 時退回道歉文；(2) `ChatStore._messages` 索引（含 image_bytes）
從不修剪；(3) LINE 沒回 sent message id 時 bot 回覆無 id，之後引用它不會成為 hard trigger；
(4) Firestore messages 集合只限讀不刪，無限成長（成本）。

## [2026-09-06] lint | env/secret 表與 Firestore 佈局校正 + 新增 /add-group

對照程式碼修掉三處過期宣稱：
- [[deployment]] 的 env/secret 表只列五個 key，漏掉 `MEDIA_BUCKET` / `USE_FIRESTORE` /
  `TDX_CLIENT_ID` / `TDX_CLIENT_SECRET`。改成逐 key 的表，標明「Secret Manager vs 純 env」
  「必要 / 可空 / 可選」，並補上「deploy 不設、吃 config.py 預設」那一組
  （`BOT_PERSONA` / `CONTEXT_WINDOW_HOURS=12` / `MAX_HISTORY=50` / `AUTO_DESCRIBE_IMAGES` /
  `GCP_PROJECT`）——這組要改就得重 build，不能只發 env-only revision，是實務上最常搞錯的一刀。
  另記 deploy.md 的兩個防呆（空 key 跳過、`--set-secrets` 動態組），與 step 2 的
  fresh-project bootstrap（API enable + Firestore create）。
- [[architecture]] 還寫著 `chats/{chat_id} → last_bot_ts, memory`（2026-07-13 記憶重構後就過期了）。
  改為實況：`memory_files` 子集合、messages 的 `media_path`、媒體 bytes 在 GCS、
  `memory_legacy` 只在遷移過的群留著。store 介面清單同步換成 `list/write/delete_memory_file`，
  並註明 `get_memory` / `append_memory` 已無呼叫端（只剩 `scripts/migrate_memory.py` 的脈絡）。
  frontmatter 的 verified-against 從 `revision 00006` 改為 commit sha（revision 號跟程式無關，
  env-only revision 會讓它虛長）。
- [[operations]] 的 `Trigger:` 行格式寫得像統一格式，實際上 `Trigger: none (chat=...)` **沒有** `msg=`
  （`handlers/text.py:94`）——撈 log 時照舊格式 grep 會漏。順手修「記憶內容在 `chats/{id}.memory`」。

新增 `/add-group`（`.claude/commands/add-group.md`）：把一直以來人機互動做的「新群上白名單」
固化成指令——撈 log 取 ID → `get_group_summary` 認群名 → `--update-env-vars` 發 env-only
revision → **鏡射回 `.env`**（deploy.md 用 `--set-env-vars` 整組替換，`.env` 沒同步下次部署會
把群悄悄踢掉，這是最容易中的坑）。移除群組走同一條路。取 ID 的兩個地雷（JoinEvent 沒 handler、
LINE 重投 join）從 2026-08-11 條目搬進 [[operations]] 專節。[[deployment]] 白名單節、
`setup.md` step 7（原本重抄一遍迴圈）、README 的指令清單都改為指向它。

未動、待確認：`setup.md` step 5 的「Two first-run gotchas」（pytest 無 tests/、空 TDX secret）
在 deploy.md 重寫後已由 deploy.md 自己處理，該段可刪，但 step 5 不在本次授權範圍。

## [2026-09-06] change | 公開化包裝：/setup onboarding、README、deploy.md 首次部署修正

目標是讓陌生人 clone 後在 Claude Code 跑 `/setup` 就能長出自己的家庭 bot。
新增 `.claude/commands/setup.md`（訪談式：一次要齊 LINE secret/token、Anthropic key、
GCP 專案、region、可選 TDX → 本地 /health → 委派 `/deploy` → `set_webhook.py` →
用 log 撈 chat ID 填白名單 → 端到端驗證），`README.md`（英文；說明中文內容是為台灣
家人而做的本貌）、`LICENSE`（MIT）、`.env.example`。
`deploy.md` 修掉三個新手首次部署必踩的坑：(1) 空的 TDX secret 讓 `secrets versions add`
與 `--set-secrets` 失敗 → secret 迴圈與 `--set-secrets` 改為只處理 .env 非空的 key，
TDX 端到端可選；(2) 不存在的 `tests/` 讓 pytest 步驟報錯 → 加 `[ -d tests ]` 守衛；
(3) 全新專案沒開 API、沒建 Firestore → 新增 step 2 `services enable` + `firestore databases
create`（冪等）。REGION/SERVICE/REPO 改為可用環境變數覆寫。`.gcloudignore` 末行
`Dockerfile.dockerignore` 是兩行黏在一起的 typo，修正。
Subagent 檢視時另指出但未處理：[[deployment]] 的 env/secret 表未列 MEDIA_BUCKET /
TDX_*；[[architecture]] 仍寫 `chats/{chat_id} → memory`（實際已是 `memory_files`
子集合）。留待下次 lint。

## [2026-09-06] change | 公開化前的隱私清理

Repo 由 private 轉 public 前的一次性清掃：五個群組的 chat ID、真實群組名稱
（原以 emoji／中文名記在 [[deployment]] 白名單節與本頁多筆舊條目）、回歸測資裡的
真實家人姓名與 bot 顯示名稱、GCP 專案 ID 與 media bucket 名稱，全數從工作樹移除。
去處：chat ID 只活在 `ALLOWED_CHAT_IDS`（.env → Cloud Run env var），金鑰仍在
Secret Manager，專案 ID 改由 `GCP_PROJECT` env 或 `gcloud config` 解析
（`scripts/review_chat.py` / `scripts/migrate_memory.py` 的 `_resolve_project()`）；
文件一律用 `${PROJECT_ID}` / `<your-project-id>` 佔位，比照 `.claude/commands/deploy.md`。
`scripts/skip_regression.py` 測資改為匿名人名（小明／阿華），@ 提及結構與語意不變，
回歸案例的判準完全一致。新增 `.env.example`（只有鍵名與註解，無值）。
**今後規約：wiki 不得記錄 chat ID、真實群組名、家人姓名**——舊條目為此破例改寫
（append-only 的隱私例外），技術內容保留。
git 歷史仍含這些字串，需另跑 `git filter-repo --replace-text` 重寫後強推（步驟另備）。

## [2026-08-11] change | 白名單加入第五個群組

只改 env var（revision 00017），無程式變更；chat ID 只進 `ALLOWED_CHAT_IDS`，不記在 wiki（[[deployment]]）。
取 ID 仍走「請人在群裡發一則訊息 → log 撈 `Ignoring message from non-whitelisted chat`」的老路：
JoinEvent 沒有 handler，group ID 進不了 log（本次 join 還被 LINE 重投兩次，更難分辨是幾個群）。
待辦：加 JoinEvent handler 把 group ID 記進 log，省掉這步人工。

## [2026-07-18] change | 檢視流程工具化：review_chat.py + /review-bot

把首夜檢討的手工取證（Firestore 撈對話、log 撈觸發、對時間戳手拼）做成
`scripts/review_chat.py` 統一時間軸；整套 review 流程寫成 `/review-bot` 指令。
工具首跑即揭露新細節：06:54 的 mention 觸發了 memory 寫入迴圈後回空文字被靜默丟棄
（昨日「寫記憶必吭聲」修正正好蓋住此路徑）。詳見 [[operations]]。

## [2026-07-18] change | 第四群首夜實戰檢討 → 四項修正 + 降溫

自主檢視新群對話後的修正批次：(1) persona 補媒體機制自我說明（「我看不到圖片」
誤導事故）；(2) 寫記憶不准 SKIP（寫入後沉默被當壞掉）；(3) 空 @ 改打招呼（一晚
兩次靜默）；(4) context window 2h→12h（8 小時斷片害 bot 失憶整段對話）。
附帶：temperature 1.0→0.3（判斷穩定性）、@ 提及≠對話對象寫進 SKIP prompt、
回歸測資擴為五案例。教訓詳見 [[triggering]]、[[tools]]。
實證確認：GCS 照片持久化、快取（搜尋呼叫讀 54K）、redelivery 護欄全部在 production
正常工作；每則 >2s 的回覆都會被 LINE 重投（同步處理超時），護欄兜住但非同步化
應與提醒功能同批做（Cloud Tasks）。

## [2026-07-17] change | SKIP 判斷第二次調校 + 回歸測試工具

第四群實測漏接：回答 bot 問題但 @ 了家人被誤 SKIP、吐槽 bot 被已讀。
兩列式重寫 _SKIP_INSTRUCTION ＋ 內嵌示例（三版規則措辭都失敗，示例一次中）。
建立 `scripts/skip_regression.py`（四場景 × 3 次投票），改此 prompt 前必跑。
蹺蹺板教訓與方法論記錄於 [[triggering]]。
同場加映：redelivery 護欄與 prompt caching 均在 production 首次實證有效。

## [2026-07-17] change | 白名單加入第四個群組

只改 env var（revision 00014），無程式變更；chat ID 只進 `ALLOWED_CHAT_IDS`（[[deployment]]）。

## [2026-07-17] change | 資料保護三保險 + 帳單盤點

Firestore PITR（7 天）＋ delete protection、GCS versioning ＋ 90 天 lifecycle。
理由：D10 之後 archive 是不可替代資產，原本的保護姿態卻是「app 資料」等級。
盤點確認 billing account 已有 AUD $10 預算告警（no-more-than-10）。
詳見 [[operations]] 資料保護節。待辦：Firestore 定期 export（PITR 只有 7 天窗）。

## [2026-07-16] change | 媒體 GCS 持久化（D10 選項 A 落地）

照片/影片縮圖收到即存 `gs://<project>-media`，doc 記 `media_path`；
MediaStore 注入 FirestoreChatStore，handler 零改動，get_message 快取 miss 時回 GCS 撈。
D2 的「重啟即失」例外收掉（[[decisions]]）。新 env：MEDIA_BUCKET；
runtime SA 加該 bucket 的 storage.objectAdmin（[[deployment]]）。
零 token 原則不變；選項 B（Haiku 描述）仍待拍板。

## [2026-07-16] change | 人味基線 persona + prompt caching 啟用

`_DEFAULT_PERSONA` 重寫：家人不是客服（訊息形狀、AI 腔負面清單、輕口語、敢有偏好），
日期/markdown 憲法條款保留（[[tools]]）。claude.py system 尾端標 `cache_control`，
tool 迴圈與相鄰請求共用前綴；live 測試首發 cache_read≈16K（web_search server tool
指令有平台側預快取）。加 Usage log 行供部署後驗證（[[operations]]）。
與 redelivery 護欄（07-15）同車部署。

## [2026-07-16] ingest | 語風習得 + 定容重蒸 + KB 快照設計

D10 的第一批落地設計：人味基線（形狀/負面清單/few-shot）、familect 三層習得、
語風檔定容重蒸防 prompt 肥大（覆蓋制＝免費遺忘）、KB 整份快照版本化
（append-only，diff 即家庭編年史）。固化 job 輸出擴為四項。詳見 [[tools]] roadmap。

## [2026-07-16] decision | D10: 定位 = 沉默的紀錄者

價值主軸從「有用」轉為「家庭記憶」：先攢後用、回顧走對話觸發不走日曆、
固化 job 要淘「時刻」與人格側寫、攢/用分離保選擇權。詳見 [[decisions]] D10。
連帶影響：媒體持久化選項 A 升級為必做，固化 job 規格擴充（[[tools]] roadmap）。

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
首次部署到 Cloud Run（專案 ID 見本機 gcloud 設定，不記在 repo）。

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

ALLOWED_CHAT_IDS 填入前三個家庭群組的 chat ID，其餘群組訊息直接忽略（[[deployment]]）。

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
