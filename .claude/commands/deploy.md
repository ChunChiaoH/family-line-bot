Deploy the bot to Cloud Run.

This procedure keeps credentials in **GCP Secret Manager** and non-secret config in
plain env vars. All steps are idempotent — safe to re-run.

## 0. Variables

```bash
PROJECT_ID="$(gcloud config get-value project)"
REGION="asia-east1"
SERVICE="family-line-bot"
REPO="family-line-bot"                     # Artifact Registry repo
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(git rev-parse --short HEAD)"
RUNTIME_SA="family-line-bot-run@${PROJECT_ID}.iam.gserviceaccount.com"
```

## 1. Run tests

```bash
python -m pytest -q        # skip if no tests present
```

## 2. Dedicated runtime service account (one-time, idempotent)

Cloud Run should run as its own least-privilege SA, not the default compute SA.

```bash
# Create the SA (ignore "already exists")
gcloud iam service-accounts create family-line-bot-run \
  --display-name="Family LINE Bot (Cloud Run runtime)" \
  --project="${PROJECT_ID}" || true

# Firestore access for the bot's chat store (idempotent — re-adding a binding is a no-op)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## 3. Secrets in Secret Manager (one-time create, then add a version)

Only three values are true secrets: `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`,
`ANTHROPIC_API_KEY`. `WEBHOOK_PATH_TOKEN` is **defense-in-depth (URL obscurity), not a
credential** — it travels as an env var.

Create each secret once (the `create` is idempotent via `|| true`), then push the current
`.env` value as a new version. Adding a version is always safe to repeat — it just supersedes
the previous version.

```bash
for NAME in LINE_CHANNEL_SECRET LINE_CHANNEL_ACCESS_TOKEN ANTHROPIC_API_KEY TDX_CLIENT_SECRET; do
  gcloud secrets create "${NAME}" --replication-policy="automatic" \
    --project="${PROJECT_ID}" || true   # ignore "already exists"

  # Read the value from .env without printing it, add as a new version
  VALUE="$(grep -E "^${NAME}=" .env | cut -d= -f2-)"
  printf '%s' "${VALUE}" | gcloud secrets versions add "${NAME}" --data-file=- \
    --project="${PROJECT_ID}"
done
unset VALUE
```

Grant the runtime SA read access to each secret (idempotent):

```bash
for NAME in LINE_CHANNEL_SECRET LINE_CHANNEL_ACCESS_TOKEN ANTHROPIC_API_KEY TDX_CLIENT_SECRET; do
  gcloud secrets add-iam-policy-binding "${NAME}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}"
done
```

## 4. Build & push the image

```bash
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker --location="${REGION}" \
  --project="${PROJECT_ID}" || true       # one-time, idempotent

gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}"
```

## 5. Deploy to Cloud Run

Secrets are injected with `--set-secrets` (each maps an env var to `SECRET:latest`).
Non-secret config goes through `--set-env-vars`. `--max-instances=1` keeps the in-memory
chat store consistent (single instance) and caps cost.

```bash
gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --service-account="${RUNTIME_SA}" \
  --max-instances=1 \
  --allow-unauthenticated \
  --set-secrets="LINE_CHANNEL_SECRET=LINE_CHANNEL_SECRET:latest,LINE_CHANNEL_ACCESS_TOKEN=LINE_CHANNEL_ACCESS_TOKEN:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,TDX_CLIENT_SECRET=TDX_CLIENT_SECRET:latest" \
  --set-env-vars="^;^WEBHOOK_PATH_TOKEN=$(grep -E '^WEBHOOK_PATH_TOKEN=' .env | cut -d= -f2-);ALLOWED_CHAT_IDS=$(grep -E '^ALLOWED_CHAT_IDS=' .env | cut -d= -f2-);TDX_CLIENT_ID=$(grep -E '^TDX_CLIENT_ID=' .env | cut -d= -f2-);USE_FIRESTORE=true;SESSION_WINDOW_MINUTES=10;CLAUDE_MODEL=claude-sonnet-4-6" \
  --project="${PROJECT_ID}"
```

Notes:
- `WEBHOOK_PATH_TOKEN` / `ALLOWED_CHAT_IDS` are read from `.env` at deploy time; edit `.env`
  (or the command) to change them. Empty is fine.
- `--set-env-vars` **replaces** the full env-var set on each deploy; keep all non-secret keys
  in this one flag. Use `--update-env-vars` instead if you want to change just one.

## 6. Point LINE at the new URL and verify

Capture the deployed URL and run the webhook helper. It sets LINE's webhook endpoint to
`<URL>/webhook/<WEBHOOK_PATH_TOKEN>` (or `/webhook` when the token is empty), then calls
LINE's test-webhook API and prints a pass/fail report. It never prints secrets — the URL is
shown with the path token masked.

```bash
URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" \
  --project="${PROJECT_ID}" --format='value(status.url)')"

python scripts/set_webhook.py "${URL}"
```

A `PASS` line means LINE reached the endpoint (HTTP 200). On `FAIL`, the report includes
LINE's reported reason/detail — check that the service is public (`--allow-unauthenticated`),
the token in `.env` matches the deployed `WEBHOOK_PATH_TOKEN`, and the revision is serving.
