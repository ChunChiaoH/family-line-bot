Add (or remove) a LINE group on the bot's whitelist — env-only, no rebuild.

由你（Claude）主導：撈 log 取 chat ID、確認是哪個群、更新 Cloud Run env var、鏡射回 `.env`。
使用者只做 LINE app 裡必須人做的事（把 bot 拉進群、發一則訊息、認群名）。

**硬規定：chat ID 與真實群組名不得寫進 repo 或 `wiki/`**（公開 repo）。
它們只活在本機 `.env`（gitignored）與 Cloud Run env var。回報給使用者時可以講，
寫檔案時不行。channel token 一律不回顯。

## 0. 變數（同 `/deploy`）

```bash
PROJECT_ID="$(gcloud config get-value project)"
REGION="${REGION:-asia-east1}"
SERVICE="${SERVICE:-family-line-bot}"
```

## 1. 請使用者做兩件事

1. 把 bot 加進目標群組（先加好友，再邀進群）。
2. **在該群發一則任意訊息**（一句「哈囉」就好）。

第 2 步不能省：JoinEvent 目前**沒有 handler**（`family_line_bot/app.py` 只註冊了
text / image / video 的 MessageEvent），所以「被加入群組」這件事不會在 log 留下 group ID。
而且 LINE 會重投 join 事件，同一次加入可能看起來像好幾個群——只有人發的訊息能給出可靠的 ID。
（待辦：加 JoinEvent handler 把 group ID 記進 log，就能省掉這步。）

## 2. 撈 log 取 chat ID

```bash
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name='"${SERVICE}" \
  --project="${PROJECT_ID}" --limit=50 --format="value(timestamp,textPayload)" --freshness=10m
```

找這兩行之一（**逐字**，不要靠 severity 過濾——Python logging 走 stderr，常常撈不到）：

- `Ignoring message from non-whitelisted chat Cxxxxxxxx` — 白名單已啟用時的情況（`app.py`，WARNING）
- `Trigger: none (chat=Cxxxxxxxx)` — 白名單為空（全放行）時的情況（`handlers/text.py`，INFO；
  這一行**沒有** `msg=`，有 `msg=` 的是其他 trigger 分支）

群組是 `C` 開頭，多人房間是 `R`，1:1 是 `U`。若同時冒出多個 ID，先確認使用者只在一個群發過話，
再往下走——別猜。

## 3. 確認這個 ID 是哪個群

叫使用者認名字，不要憑印象。用 line-bot-sdk v3（構造方式比照 `family_line_bot/services/line_client.py`）：

```bash
python - <<'PY'
import os
from dotenv import load_dotenv
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi

load_dotenv()
GID = "Cxxxxxxxx"  # 換成上一步撈到的 ID
with ApiClient(Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])) as c:
    print(MessagingApi(c).get_group_summary(GID).group_name)
PY
```

只印 group name，**絕不印 token**。`get_group_summary` 只對 `C` 開頭的群組有效
（room / 1:1 沒有這個 API）。印出來後跟使用者確認：「這個群是不是你要加的？」

## 4. 更新白名單（env-only revision，不重 build）

`ALLOWED_CHAT_IDS` 是逗號分隔（`config.py` 的 `allowed_chat_id_set`），
所以要**先讀現值再附加**，不要整組覆寫：

```bash
# 讀出目前線上的 ALLOWED_CHAT_IDS（用 JSON 取，比 --format 的 filter 投影可靠）
CURRENT="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --format=json \
  | python -c "import json,sys; env=json.load(sys.stdin)['spec']['template']['spec']['containers'][0].get('env',[]); print(next((e.get('value','') for e in env if e['name']=='ALLOWED_CHAT_IDS'), ''))")"

NEW_ID="Cxxxxxxxx"
UPDATED="${CURRENT:+${CURRENT},}${NEW_ID}"

gcloud run services update "${SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars="^;^ALLOWED_CHAT_IDS=${UPDATED}"
```

- `^;^` 自訂分隔符**必須**：值裡有逗號，不然 gcloud 會把它當多個 key，exit 2。
- `--update-env-vars` 只動這個 key；`--set-env-vars` 會整組替換（那是 `/deploy` 在做的事）。
- 這會產生一個新的 **env-only revision**（沒有新 image，秒級生效）。
- 移除某群（bot 被踢出、群解散）就是同一招：從 `UPDATED` 裡拿掉那個 ID 再送一次。
  留著失效 ID 沒有安全問題，但會讓白名單越來越難讀。

## 5. 鏡射回 `.env`（**別漏這步**）

`/deploy` step 6 是 `ENV_VARS="...ALLOWED_CHAT_IDS=$(grep -E '^ALLOWED_CHAT_IDS=' .env | cut -d= -f2-)"`
＋ `--set-env-vars`（整組替換）。所以 `.env` 沒同步的話，**下次 `/deploy` 會把剛加的群悄悄踢掉**。

```bash
grep -E '^ALLOWED_CHAT_IDS=' .env      # 確認現值
```

用 Edit 工具把 `.env` 的 `ALLOWED_CHAT_IDS=` 改成跟第 4 步的 `UPDATED` 一致（別 commit，`.env` 已 gitignored）。

## 6. 驗證

請使用者在該群 `@` bot 說一句話，然後：

```bash
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name='"${SERVICE}" \
  --project="${PROJECT_ID}" --limit=20 --format="value(timestamp,textPayload)" --freshness=5m
```

- 出現 `Trigger: mention (chat=..., msg=...)` 且群裡有回覆 → 成功。
- 還是 `Ignoring message from non-whitelisted chat` → 新 revision 沒在服務，或 ID 打錯。
- 完全沒 log → 是 webhook 問題不是白名單問題，走 `scripts/set_webhook.py`（見 wiki/operations.md）。

## 7. 收尾

- wiki：`[[deployment]]` 的白名單節記**群組數量與 revision**即可，**不記 ID、不記群名**。
  `wiki/log.md` 追加一筆 `change`（同樣不含 ID / 群名）。
- 不需要 commit 程式碼——這整個流程沒有程式變更。
