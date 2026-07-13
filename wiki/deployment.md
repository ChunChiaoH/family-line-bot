---
updated: 2026-07-12
verified-against: revision family-line-bot-00006 / .claude/commands/deploy.md
---

# Deployment — GCP 設定與部署

完整可執行流程在 `.claude/commands/deploy.md`（`/deploy` 指令）；本頁記**為什麼這樣設**。

## 基本盤

- GCP 專案 `<your-project-id>`，region asia-east1，服務 `family-line-bot`
- Runtime SA：`family-line-bot-run@...`（最小權限：`roles/datastore.user` +
  各 secret 的 `secretmanager.secretAccessor`；不要用預設 compute SA）
- `--max-instances=1`：成本上限 + in-process 狀態的正確性前提（見 [[architecture]]）
- Firestore Native mode，asia-east1，與 Cloud Run 同區

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

## 白名單現況（2026-07-12）

三個群組：第一群 `<CHAT_ID_REDACTED>`、第二群 `<CHAT_ID_REDACTED>`、
第三群 `<CHAT_ID_REDACTED>`。空白名單 = 全放行（開發用）。
新群組 ID 的取得方式見 [[operations]]。

## 部署後自動接 webhook

`scripts/set_webhook.py <cloud-run-url>`：用 LINE API 設定 webhook endpoint（含隨機路徑）
並實測連通性。**每次服務 URL 或路徑 token 變動都要跑**；忘了跑的症狀是 bot 完全不回
（LINE 還指著舊 URL——歷史上曾指向失效的 ngrok 網址）。

## 已評估不採用的替代方案

Vercel（Python 二等公民、與 Firestore 認證整合差）、min-instances=1（$10+/月買不到什麼）、
Cloudflare 前置（$0 威脅花 $18/月防）。詳見 [[decisions]]。
