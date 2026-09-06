Deploy the bot to Cloud Run.

This procedure keeps credentials in **GCP Secret Manager** and non-secret config in
plain env vars. All steps are idempotent — safe to re-run.

## 0. Variables

```bash
PROJECT_ID="$(gcloud config get-value project)"
REGION="${REGION:-asia-east1}"
SERVICE="${SERVICE:-family-line-bot}"
REPO="${REPO:-family-line-bot}"            # Artifact Registry repo
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(git rev-parse --short HEAD)"
RUNTIME_SA="family-line-bot-run@${PROJECT_ID}.iam.gserviceaccount.com"
```

## 1. Run tests

Only run the regression suite when it's relevant — it makes live API calls (~$0.05).
`tests/` doesn't exist in this repo; guard for it so this stays a no-op until it does.

```bash
# Only if you touched _SKIP_INSTRUCTION in services/claude.py this deploy
python scripts/skip_regression.py

# General test suite, only if a tests/ dir exists
[ -d tests ] && python -m pytest -q || echo "no tests dir, skipping"
```

## 2. Enable APIs & Firestore (one-time, idempotent)

Fresh-project bootstrap. Safe to re-run — API enablement and `|| true` on the database
create make this a no-op once done.

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  firestore.googleapis.com storage.googleapis.com logging.googleapis.com \
  --project="${PROJECT_ID}"

gcloud firestore databases create --location="${REGION}" --type=firestore-native \
  --project="${PROJECT_ID}" || true   # ignore "already exists"
```

## 3. Dedicated runtime service account (one-time, idempotent)

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

# Media bucket (photos / video thumbnails) + object access for the runtime SA
gcloud storage buckets create "gs://${PROJECT_ID}-media" \
  --project="${PROJECT_ID}" --location="${REGION}" \
  --uniform-bucket-level-access --public-access-prevention || true   # ignore "already exists"

gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-media" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin" --project="${PROJECT_ID}"
```

## 4. Secrets in Secret Manager (one-time create, then add a version)

Only three values are always true secrets: `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`,
`ANTHROPIC_API_KEY`. `TDX_CLIENT_SECRET` is a secret too, but **optional** — the THSR tool is
only registered when both `TDX_CLIENT_ID` and `TDX_CLIENT_SECRET` are non-empty
(`family_line_bot/app.py`). `WEBHOOK_PATH_TOKEN` is **defense-in-depth (URL obscurity), not a
credential** — it travels as an env var.

Create each secret once (the `create` is idempotent via `|| true`), then push the current
`.env` value as a new version. Adding a version is always safe to repeat — it just supersedes
the previous version. `gcloud secrets versions add` rejects an empty payload, so skip any
key that's missing or empty in `.env` rather than writing an empty version.

```bash
for NAME in LINE_CHANNEL_SECRET LINE_CHANNEL_ACCESS_TOKEN ANTHROPIC_API_KEY TDX_CLIENT_SECRET; do
  VALUE="$(grep -E "^${NAME}=" .env | cut -d= -f2-)"
  if [ -z "${VALUE}" ]; then
    echo "skipping ${NAME}: empty/missing in .env"
    continue
  fi

  gcloud secrets create "${NAME}" --replication-policy="automatic" \
    --project="${PROJECT_ID}" || true   # ignore "already exists"

  # Read the value from .env without printing it, add as a new version
  printf '%s' "${VALUE}" | gcloud secrets versions add "${NAME}" --data-file=- \
    --project="${PROJECT_ID}"
done
unset VALUE
```

Grant the runtime SA read access to each secret that actually exists (idempotent):

```bash
for NAME in LINE_CHANNEL_SECRET LINE_CHANNEL_ACCESS_TOKEN ANTHROPIC_API_KEY TDX_CLIENT_SECRET; do
  gcloud secrets describe "${NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1 || continue
  gcloud secrets add-iam-policy-binding "${NAME}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}"
done
```

## 5. Build & push the image

```bash
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker --location="${REGION}" \
  --project="${PROJECT_ID}" || true       # one-time, idempotent

gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}"
```

## 6. Deploy to Cloud Run

Secrets are injected with `--set-secrets` (each maps an env var to `SECRET:latest`).
Non-secret config goes through `--set-env-vars`. `--max-instances=1` keeps the in-memory
chat store consistent (single instance) and caps cost.

The `--set-secrets` list is built dynamically from only the secrets that actually exist in
Secret Manager, so a skipped `TDX_CLIENT_SECRET` doesn't break the deploy (a listed secret
with no version would fail the whole command). `TDX_CLIENT_ID` is likewise only added to
`--set-env-vars` when non-empty.

```bash
SECRETS=""
for NAME in LINE_CHANNEL_SECRET LINE_CHANNEL_ACCESS_TOKEN ANTHROPIC_API_KEY TDX_CLIENT_SECRET; do
  gcloud secrets describe "${NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1 || continue
  SECRETS="${SECRETS}${SECRETS:+,}${NAME}=${NAME}:latest"
done

ENV_VARS="WEBHOOK_PATH_TOKEN=$(grep -E '^WEBHOOK_PATH_TOKEN=' .env | cut -d= -f2-)"
ENV_VARS="${ENV_VARS};ALLOWED_CHAT_IDS=$(grep -E '^ALLOWED_CHAT_IDS=' .env | cut -d= -f2-)"

TDX_ID="$(grep -E '^TDX_CLIENT_ID=' .env | cut -d= -f2-)"
if [ -n "${TDX_ID}" ]; then
  ENV_VARS="${ENV_VARS};TDX_CLIENT_ID=${TDX_ID}"
fi

ENV_VARS="${ENV_VARS};USE_FIRESTORE=true;MEDIA_BUCKET=${PROJECT_ID}-media;SESSION_WINDOW_MINUTES=10;CLAUDE_MODEL=claude-sonnet-4-6"

gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --service-account="${RUNTIME_SA}" \
  --max-instances=1 \
  --allow-unauthenticated \
  --set-secrets="${SECRETS}" \
  --set-env-vars="^;^${ENV_VARS}" \
  --project="${PROJECT_ID}"
unset TDX_ID ENV_VARS SECRETS
```

Notes:
- `WEBHOOK_PATH_TOKEN` / `ALLOWED_CHAT_IDS` are read from `.env` at deploy time; edit `.env`
  (or the command) to change them. Empty is fine.
- `--set-env-vars` **replaces** the full env-var set on each deploy; keep all non-secret keys
  in this one flag. Use `--update-env-vars` instead if you want to change just one.

## 7. Point LINE at the new URL and verify

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
