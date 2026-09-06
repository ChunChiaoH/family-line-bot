---
updated: 2026-09-06
verified-against: revision family-line-bot-00017 / .claude/commands/deploy.md
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

| 去處 | 內容 | 理由 |
|---|---|---|
| Secret Manager（`--set-secrets`）| LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / ANTHROPIC_API_KEY | 真金鑰 |
| 環境變數（`--set-env-vars`）| WEBHOOK_PATH_TOKEN / ALLOWED_CHAT_IDS / USE_FIRESTORE / SESSION_WINDOW_MINUTES / CLAUDE_MODEL | WEBHOOK_PATH_TOKEN 是 URL 混淆（defense-in-depth），不是憑證 |

本機 `.env` 是這些值的來源（deploy 時讀取），**已在 .gitignore 和 .gcloudignore**——
後者防止 `gcloud builds submit` 把 secrets 上傳進 build context，動 ignore 檔前先想到這件事。

## 只改環境變數不用重 build

```
gcloud run services update family-line-bot --region=asia-east1 \
  --update-env-vars='^;^ALLOWED_CHAT_IDS=Cxxx,Cyyy'
```

`^;^` 是 gcloud 的自訂分隔符語法，**值裡有逗號時必須用**（白名單就是）。
注意 `--set-env-vars` 會整組替換、`--update-env-vars` 只改指定的。

## 白名單

群組 chat ID **只存在於 `ALLOWED_CHAT_IDS`**（本機 `.env` → deploy 時進 Cloud Run env var），
不寫進本 repo 的任何檔案或 wiki 頁——它們是可識別的個資，公開 repo 一律不留。
格式是逗號分隔的 `C...` ID；空白名單 = 全放行（開發用）。
新群組 ID 的取得方式見 [[operations]]（撈 log 那節）。
目前線上白名單有五個家庭群組（2026-08-11 起，第五個為 env-only 變更 revision 00017）。

## 部署後自動接 webhook

`scripts/set_webhook.py <cloud-run-url>`：用 LINE API 設定 webhook endpoint（含隨機路徑）
並實測連通性。**每次服務 URL 或路徑 token 變動都要跑**；忘了跑的症狀是 bot 完全不回
（LINE 還指著舊 URL——歷史上曾指向失效的 ngrok 網址）。

## 已評估不採用的替代方案

Vercel（Python 二等公民、與 Firestore 認證整合差）、min-instances=1（$10+/月買不到什麼）、
Cloudflare 前置（$0 威脅花 $18/月防）。詳見 [[decisions]]。
