---
updated: 2026-07-15
---

# Operations — 營運與除錯

## 撈 log

```
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=family-line-bot' \
  --project=<your-project-id> --limit=50 --format="value(timestamp,textPayload)" --freshness=1h
```

- Python logging 走 stderr，**severity 過濾常常撈不到**，直接撈全部再文字過濾。
- 觸發診斷靠 `Trigger: mention|reply_to_bot|session|none|superseded (chat=..., msg=...)`，
  可分辨「規則層沒觸發」vs「Claude 判斷 SKIP」。
- 新群組 chat ID 的取得：讓人在該群發任意訊息，log 會留 `Trigger: none (chat=Cxxx)`。
  群組 ID → 名稱用 `MessagingApi.get_group_summary(gid)`。

## 已知地雷

| 地雷 | 症狀 | 解法 |
|---|---|---|
| Firestore `snap.get("欄位")` 欄位不存在時丟 KeyError 而非回 None | 「ran into an issue」且 log 錯誤掛名 Claude API error（其實是 store 炸在 try 內）| 一律走 `(snap.to_dict() or {}).get(...)` |
| Firestore collection_group 查詢需手動建索引 | FailedPrecondition | 已重構避開（get_message 帶 chat_id 直接定位）；新查詢設計時優先避免 collection_group |
| gcloud env var 值含逗號 | `--update-env-vars` exit 2 | 自訂分隔符 `^;^KEY=a,b,c` |
| LINE webhook 指向舊 URL | bot 完全不回、無 log | 跑 `scripts/set_webhook.py`，它會實測連通 |
| PowerShell cp950 印不出 emoji | 測試腳本 UnicodeEncodeError | `$env:PYTHONIOENCODING='utf-8'` |
| line-bot-sdk 在 Python 3.14 的 pydantic v1 警告 | import 時 UserWarning | 無害；container 用 python:3.12 避開 |
| Cloud Run 縮到零後冷啟動丟請求（實例啟動 ~15s）| 閒置 >15 分後第一則訊息偶爾無回應；log 有 `request was aborted because there was no available instance`，連 Trigger 行都沒有 | LINE Console 開 webhook redelivery（免費）＋ handlers/text.py 的 `is_redelivery` 去重護欄。治本是 `min-instances=1`（~$7-15/月），刻意不採 |

## 成本模型

- Token：家庭量級月估 3-8 澳幣。一次 tool 回覆 = 2-4 次 API 呼叫（迴圈每輪重送全部 context）。
- Web search 另計：$10/千次（≈1 美分/次），結果內容以 input token 計費。
- SKIP 判斷也是完整呼叫——session window 越長、判斷越多。
- 帳戶是**儲值制**：餘額歸零 API 停，bot 回「ran into an issue」。這是天然花費上限，
  但家人會以為 bot 壞了——考慮 Console 開 auto-reload。
- 降本順序：web_search `max_uses` 3→1 > 縮短 SESSION_WINDOW_MINUTES > Haiku 分流（工程量大，最後）。
- **Prompt caching 尚未啟用**（2026-07-15 盤點）：claude.py 從未標 `cache_control`——設計上快取安全
  （時間戳在 user prompt）但沒真的開。最受益處是 tool 迴圈（一次查詢 2-4 呼叫共用 system+tools 前綴，
  讀快取 0.1x）。門檻：Sonnet 4.6 最小可快取前綴 2048 tokens，我們約在邊緣；量級小、省的是零錢，
  下次動 claude.py 順手加（system 尾端一個斷點即可），加完看 usage.cache_read_input_tokens 驗證。

## 例行檢查（心智 checklist）

- Console 用量 vs 預期（測試日 ≈ $0.5+/天是正常的，日常應遠低於此）
- log 裡 `Ignoring message from non-whitelisted chat` 出現 = 有人把 bot 拉進陌生群
- 記憶內容（Firestore `chats/{id}.memory`）有沒有記進垃圾——可直接人工編輯
