---
updated: 2026-09-06
verified-against: 304060d / .claude/commands/deploy.md / config.py
---

# Deployment — GCP 設定與部署

完整可執行流程在 `.claude/commands/deploy.md`（`/deploy` 指令）；本頁記**為什麼這樣設**。

## 基本盤

- GCP 專案 `<your-project-id>`（deploy.md 以 `${PROJECT_ID}` 代入，取自 `gcloud config`），
  region asia-east1，服務 `family-line-bot`
- Runtime SA：`family-line-bot-run@...`（最小權限：`roles/datastore.user` +
  各 secret 的 `secretmanager.secretAccessor` + media bucket 的 `storage.objectAdmin`
  （僅該 bucket，2026-07-16）；不要用預設 compute SA）
- `--max-instances=1`：成本上限 + in-process 狀態的正確性前提（見 [[architecture]]）
- Firestore Native mode，asia-east1，與 Cloud Run 同區
- 媒體 bucket `gs://${PROJECT_ID}-media`：asia-east1、uniform access、
  禁公開；env `MEDIA_BUCKET` 指向它（留空 = 關閉媒體持久化）

## Secrets 與環境變數的分界

分界原則：**真金鑰進 Secret Manager，其他一律 plain env var**。
`WEBHOOK_PATH_TOKEN` 看起來像憑證但不是——它只是 URL 混淆（defense-in-depth），
外洩不等於被入侵（還有 HMAC 簽章與白名單兩層），為了 deploy/set_webhook 流程好讀直接走 env。

| 變數 | 去處 | 必要性 | 備註 |
|---|---|---|---|
| `LINE_CHANNEL_SECRET` | Secret Manager（`--set-secrets`）| 必要 | HMAC 簽章驗證用 |
| `LINE_CHANNEL_ACCESS_TOKEN` | Secret Manager | 必要 | 回覆 / 抓內容 / 查顯示名稱 |
| `ANTHROPIC_API_KEY` | Secret Manager | 必要 | |
| `TDX_CLIENT_SECRET` | Secret Manager | **可選** | 空 = 不建 secret、不進 `--set-secrets` |
| `WEBHOOK_PATH_TOKEN` | env（`--set-env-vars`）| 可空 | 空字串 = 路徑退回 `/webhook`（app.py）|
| `ALLOWED_CHAT_IDS` | env | 可空 | 逗號分隔；空 = 全放行（僅開發用）|
| `TDX_CLIENT_ID` | env | **可選** | 只有非空時才加進 `--set-env-vars`；ID 與 secret 都非空才註冊 `search_thsr`（app.py）|
| `USE_FIRESTORE` | env | deploy 固定 `true` | 本機 `.env` 通常是 `false`（in-memory store，無需 GCP）|
| `MEDIA_BUCKET` | env | **可選** | deploy 固定 `${PROJECT_ID}-media`；留空 = 關閉 GCS 媒體持久化 |
| `SESSION_WINDOW_MINUTES` | env | deploy 固定 `10` | 調參見 [[triggering]] |
| `CLAUDE_MODEL` | env | deploy 固定 `claude-sonnet-4-6` | 見 [[decisions]] D5 |

deploy 時**不設**、吃 `config.py` 預設的：`BOT_PERSONA`（config.py 的 `_DEFAULT_PERSONA`）、
`CONTEXT_WINDOW_HOURS`（12）、`MAX_HISTORY`（50）、`AUTO_DESCRIBE_IMAGES`（false）、
`GCP_PROJECT`（空 = 讓 client 走 ADC 自動偵測）。要改這些得加進 deploy.md 的 `ENV_VARS`
或改 config.py 預設——**改 config.py 預設要重 build**，加 env var 則可只發 env-only revision。

deploy.md 的兩個防呆（都是公開化時踩出來的）：secret 迴圈**跳過 .env 裡空/缺的 key**
（`gcloud secrets versions add` 拒收空 payload），`--set-secrets` 清單也**動態只列真的存在的 secret**
（列了沒有版本的 secret 會讓整個 deploy 失敗）。所以 TDX 可以完全不設而不影響部署。

全新專案的 bootstrap（`gcloud services enable` 七個 API + `firestore databases create`）
現在是 **deploy.md step 2**，冪等，不必另外手動做。`REGION` / `SERVICE` / `REPO`
可用同名環境變數覆寫（預設 asia-east1 / family-line-bot / family-line-bot）。

本機 `.env` 是這些值的來源（deploy 時 grep 讀取），**已在 .gitignore 和 .gcloudignore**——
後者防止 `gcloud builds submit` 把 secrets 上傳進 build context，動 ignore 檔前先想到這件事。

## 只改環境變數不用重 build

```
gcloud run services update family-line-bot --region=asia-east1 \
  --update-env-vars='^;^ALLOWED_CHAT_IDS=Cxxx,Cyyy'
```

`^;^` 是 gcloud 的自訂分隔符語法，**值裡有逗號時必須用**（白名單就是）。
注意 `--set-env-vars` 會整組替換（deploy.md step 6 就是這樣，所以它一次帶齊全部非 secret key）、
`--update-env-vars` 只改指定的。加白名單的完整流程見 `/add-group`（`.claude/commands/add-group.md`）。

## 白名單

群組 chat ID **只存在於 `ALLOWED_CHAT_IDS`**（本機 `.env` → deploy 時進 Cloud Run env var），
不寫進本 repo 的任何檔案或 wiki 頁——它們是可識別的個資，公開 repo 一律不留。
格式是逗號分隔的 `C...` ID；空白名單 = 全放行（開發用）。
新增/移除群組走 `/add-group`（`.claude/commands/add-group.md`）：撈 log 取 ID → 確認群名 →
`--update-env-vars` 發 env-only revision，同時把值鏡射回 `.env`（否則下次 `/deploy` 的
`--set-env-vars` 會把整組蓋掉）。取 ID 的地雷見 [[operations]]。
目前線上白名單有五個家庭群組（2026-08-11 起，第五個為 env-only 變更 revision 00017）。

## 部署後自動接 webhook

`scripts/set_webhook.py <cloud-run-url>`：用 LINE API 設定 webhook endpoint（含隨機路徑）
並實測連通性。**每次服務 URL 或路徑 token 變動都要跑**；忘了跑的症狀是 bot 完全不回
（LINE 還指著舊 URL——歷史上曾指向失效的 ngrok 網址）。

## 已評估不採用的替代方案

Vercel（Python 二等公民、與 Firestore 認證整合差）、min-instances=1（$10+/月買不到什麼）、
Cloudflare 前置（$0 威脅花 $18/月防）。詳見 [[decisions]]。
