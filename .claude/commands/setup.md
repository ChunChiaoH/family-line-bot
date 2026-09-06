Set up a fresh copy of this bot for the user's own LINE group, end to end.

You (Claude) lead: run the checks, ask for the inputs, deploy, wire the webhook,
whitelist the group, verify. The user's job is only to hand over credentials and to
do the few things that must happen inside the LINE app / consoles.

**Secrets rule, non-negotiable:** never echo a channel secret, access token or API key
back into the chat, never put one in a command you print, never commit one. They go into
`.env` (gitignored) and from there into Secret Manager via `/deploy`. When you must show
a URL containing the webhook path token, mask it.

## 1. Prerequisites (run these yourself, don't ask)

```bash
python --version                     # need >= 3.11 (container runs 3.12)
git --version
gcloud --version
gcloud auth list                     # need an ACTIVE account
gcloud config get-value project      # may be unset; we set it in step 4
```

Docker is **not** needed — `/deploy` builds with `gcloud builds submit` (Cloud Build).
If `gcloud` is missing or unauthenticated, tell the user to install the Google Cloud CLI
and run `gcloud auth login` + `gcloud auth application-default login`, then stop and wait.

## 2. Ask for the inputs — all in one batch

Ask once, as a numbered list, with the one-line "where it comes from" note. Do not walk
the user through console clicks; just say which console and which page.

1. **LINE channel secret** — LINE Developers console (https://developers.line.biz/console/),
   a **Messaging API** channel → Basic settings.
2. **LINE channel access token (long-lived)** — same channel → Messaging API tab.
3. **Anthropic API key** — https://console.anthropic.com/ → API keys. The account is
   prepaid; a zero balance makes the bot answer "ran into an issue".
4. **GCP project ID** — a project with **billing enabled** (https://console.cloud.google.com/).
   Everything here stays inside free tiers except Anthropic tokens; still, billing must be on.
5. **Region** — default `asia-east1`. Firestore, the media bucket and Cloud Run must all
   be in the same region; asia-east1 is right for a Taiwan/Asia group.
6. *(optional)* **TDX client id + secret** — Taiwan transport open data
   (https://tdx.transportdata.tw/), used only by the `search_thsr` (高鐵) tool. Skip it and
   the tool is simply not registered.
7. *(optional)* **Persona tweaks** — the default `BOT_PERSONA` in `family_line_bot/config.py`
   is Traditional-Chinese, Taiwanese-family voice. Tell the user it can be changed later by
   editing that default or setting a `BOT_PERSONA` env var; don't block setup on it.

Then write `.env` yourself (see `.env.example` for the full key list). Use the Write tool,
not a printed heredoc, so the values never appear in the transcript. Generate the webhook
path token randomly:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Minimum `.env` for a first deploy:

```
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
ANTHROPIC_API_KEY=...
WEBHOOK_PATH_TOKEN=<the random token>
ALLOWED_CHAT_IDS=
USE_FIRESTORE=false
TDX_CLIENT_ID=
TDX_CLIENT_SECRET=
```

`ALLOWED_CHAT_IDS` empty = every chat allowed. That is fine for the minutes between
deploying and step 7; close it as soon as you have the real chat ID.

Confirm `.env` is gitignored (`git check-ignore -v .env`) before going further.

## 3. Local smoke test (quick, optional)

```bash
python -m pip install -e .
python -c "from family_line_bot.app import create_app; from family_line_bot.config import Settings; create_app(Settings())" && echo APP_OK
```

For a live check, run `python main.py` in the background and `curl http://localhost:8080/health`
(expect `{"status":"ok"}`), then stop it. Keep `USE_FIRESTORE=false` locally — the in-memory
`ChatStore` needs no GCP at all.

## 4. GCP project + APIs

```bash
gcloud config set project <PROJECT_ID>
```

API enablement and the one-time Firestore database creation are step 2 of
`.claude/commands/deploy.md` (idempotent, region-aware via `REGION=`). Export
`REGION` if the user chose something other than `asia-east1`; `/deploy` handles the rest.

## 5. Deploy

Follow `.claude/commands/deploy.md` — do **not** re-derive it here. It is idempotent and on a
first run it creates the runtime service account (`family-line-bot-run@…`, least privilege:
`roles/datastore.user`, per-secret `secretAccessor`, `storage.objectAdmin` on the media
bucket only), the media bucket, the Secret Manager secrets, the Artifact Registry repo, and
the Cloud Run service (`--max-instances=1`, `--allow-unauthenticated`).

Two first-run gotchas to handle before you run it:

- Step 1 runs `python -m pytest -q`; there is no `tests/` directory in this repo — skip it.
- Step 3 loops over `TDX_CLIENT_SECRET` as well. `gcloud secrets versions add` rejects an
  **empty** payload, and step 5's `--set-secrets` requires every listed secret to have a
  version. If the user skipped TDX, either drop `TDX_CLIENT_SECRET` from both the loop and
  the `--set-secrets` list, or store a placeholder value (`unused`) — the bot only registers
  the THSR tool when both `TDX_CLIENT_ID` and `TDX_CLIENT_SECRET` are non-empty
  (`family_line_bot/app.py`), so a placeholder is harmless but the ID must stay empty.

Also note `USE_FIRESTORE=true` and `MEDIA_BUCKET=${PROJECT_ID}-media` are set inside the
deploy command's `--set-env-vars`, so the deployed service uses Firestore + GCS even though
your local `.env` says `USE_FIRESTORE=false`. That is intended.

## 6. Register the webhook with LINE

`/deploy` step 6 already does this; if you are running it separately:

```bash
URL="$(gcloud run services describe family-line-bot --region=asia-east1 \
  --format='value(status.url)')"
python scripts/set_webhook.py "${URL}"
```

The script reads `LINE_CHANNEL_ACCESS_TOKEN` and `WEBHOOK_PATH_TOKEN` from `.env`, sets the
endpoint to `<URL>/webhook/<token>`, calls LINE's test-webhook API and prints `PASS`/`FAIL`.
It masks the token in its output — keep it that way when you quote the result.

Then tell the user, in one line, to open the same Messaging API channel's settings and:
**turn off "auto-reply messages" (自動回覆訊息) and any greeting message, and turn on
"Use webhook" (使用 Webhook)**. Also switch off "Allow bot to join group chats" only if they
don't want group use — for a family group it must stay **on**. LINE's own auto-reply will
otherwise answer before the bot does.

Re-run `scripts/set_webhook.py` any time the service URL or `WEBHOOK_PATH_TOKEN` changes;
"bot is completely silent, no logs at all" is almost always LINE still pointing at an old URL.

## 7. Whitelist the group (loop, once per group)

Chat IDs are **never** written into the repo or `wiki/` — env var only.

1. Ask the user to add the bot to the family group (invite it as a friend first, then add it
   to the group) and to send any message there.
2. Read the logs and find the chat ID:

```bash
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=family-line-bot' \
  --limit=50 --format="value(timestamp,textPayload)" --freshness=10m
```

   Look for `Trigger: none (chat=Cxxxxxxxx, msg=...)` (whitelist still open) or
   `Ignoring message from non-whitelisted chat Cxxxxxxxx` (whitelist already set). Python
   logging goes to stderr and severity filters often miss it — pull everything and grep the
   text, as `wiki/operations.md` says.

3. Set the whitelist without a rebuild:

```bash
gcloud run services update family-line-bot --region=asia-east1 \
  --update-env-vars='^;^ALLOWED_CHAT_IDS=Cxxxx,Cyyyy'
```

   The `^;^` custom-delimiter prefix is **required** because the value contains commas.
   `--update-env-vars` changes only that key; `--set-env-vars` would replace the whole set.

4. Repeat from 1 for each extra group, always passing the full comma-separated list.
5. Mirror the same value into `.env` so the next `/deploy` (which reads `ALLOWED_CHAT_IDS`
   from `.env` and uses `--set-env-vars`) does not wipe it. Do not commit `.env`.

## 8. End-to-end verification

Ask the user to `@`-mention the bot in the group ("@<bot> 在嗎"). Expect a Traditional-Chinese
reply within a few seconds. If it is silent:

- No log lines at all → webhook URL wrong (re-run step 6) or the chat is not whitelisted.
- `Ignoring message from non-whitelisted chat` → step 7's env var did not take; check the
  new revision is serving.
- `Trigger: none` → the mention did not register; LINE only counts a real mention picked
  from the autocomplete list.
- "Sorry, I ran into an issue" → Anthropic key or balance; check the log line right before it.
- First message after >15 min idle is dropped → Cloud Run cold start. Enable webhook
  redelivery in the LINE console (free); the redelivery guard in `handlers/text.py`
  de-duplicates.

The full troubleshooting table is `wiki/operations.md` → 已知地雷.

## 9. Hand-off

Tell the user, briefly:

- `/review-bot` — after a few days of real use, pulls a unified timeline
  (`scripts/review_chat.py <chat_id> --hours 24`) and drives evidence-based tuning.
- `/add-tool` — scaffolding for a new tool, and `wiki/tools.md` for the conventions
  (a tool's description is the model's self-knowledge of its own abilities).
- `wiki/` — the design log (Chinese). Read `wiki/index.md` before changing architecture.
- `scripts/skip_regression.py` must pass (5 real cases, 3 votes each, all must agree)
  **before** any edit to `_SKIP_INSTRUCTION` in `services/claude.py` ships. It makes live
  API calls (~$0.05).
- Cost: token spend is on the Anthropic account, not the GCP bill; set a GCP budget alert
  and consider Anthropic auto-reload so a zero balance doesn't look like a broken bot.
- Tuning knobs: `SESSION_WINDOW_MINUTES` (how long the bot stays "in conversation"),
  `CLAUDE_MODEL`, `BOT_PERSONA`.
