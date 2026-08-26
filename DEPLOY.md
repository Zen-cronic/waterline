# Deploying Waterline to Google Cloud

Waterline is two Cloud Run services — a **private agent** (FastAPI + ADK) and a public **frontend relay** (Next.js standalone) — plus **PostGIS** on **Cloud SQL for PostgreSQL**. The browser has no agent URL or credential. The frontend's service identity invokes the private agent; its exact-path relay issues a tamper-evident HttpOnly pilot session and signs that opaque owner plus the normalized command body.

## Prerequisites
- `gcloud` CLI installed and authenticated (`gcloud auth login`), project selected (`gcloud config set project <PROJECT_ID>`).
- APIs enabled: `run.googleapis.com`, `cloudbuild.googleapis.com`, `sqladmin.googleapis.com`, `artifactregistry.googleapis.com`, `aiplatform.googleapis.com`, and `secretmanager.googleapis.com`.
- Separate `waterline-runtime` and `waterline-web` service accounts. The runtime needs Vertex AI User and Cloud SQL Client; both receive resource-scoped access to `waterline-relay-secret`. Do not create a service-account key.
- Docker for the local container preflight. Do not deploy until both image smoke checks pass.

## 1. Cloud SQL (PostGIS)
```bash
gcloud sql instances create waterline-pg \
  --database-version=POSTGRES_16 --tier=db-f1-micro --region=us-central1
gcloud sql databases create waterline --instance=waterline-pg
gcloud sql users set-password postgres --instance=waterline-pg --password=<PW>
# enable PostGIS once, via the connection:
#   CREATE EXTENSION IF NOT EXISTS postgis;
# then load db/schema.sql (including the dispatch_receipts idempotency ledger)
```
PostGIS ships with Cloud SQL for PostgreSQL — no build needed. Apply `db/schema.sql` through the Cloud SQL connection.

## 2. Agent on Cloud Run
```bash
# from waterline/ (build context = project root; .gcloudignore includes the
# tracked public-domain reference data, but excludes local NAV CANADA captures
# and env files)
export WATERLINE_PROJECT="<PROJECT>"
export WATERLINE_REGION="us-central1"
export WATERLINE_SQL_CONNECTION="${WATERLINE_PROJECT}:${WATERLINE_REGION}:waterline-pg"
export WATERLINE_RUNTIME_SA="waterline-runtime@${WATERLINE_PROJECT}.iam.gserviceaccount.com"
export WATERLINE_WEB_SA="waterline-web@${WATERLINE_PROJECT}.iam.gserviceaccount.com"

gcloud run deploy waterline-agent \
  --source . \
  --region="${WATERLINE_REGION}" \
  --service-account="${WATERLINE_RUNTIME_SA}" \
  --no-allow-unauthenticated \
  --add-cloudsql-instances="${WATERLINE_SQL_CONNECTION}" \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${WATERLINE_PROJECT},GOOGLE_CLOUD_LOCATION=${WATERLINE_REGION}" \
  --set-secrets="DATABASE_URL=waterline-database-url:latest,WATERLINE_SESSION_DB=waterline-session-db:latest,WATERLINE_RELAY_SECRET=waterline-relay-secret:latest" \
  --port=8080 \
  --min-instances=0 \
  --max-instances=2
```
- Run `deploy/provision_cloud_sql.sh` and `deploy/provision_relay_identity.sh` first. Never put database passwords or the relay HMAC secret in a command or repository.
- `min-instances 0` and a small `--max-instances` cap per the hackathon cost guidance.
- `WATERLINE_SESSION_DB` (SQLAlchemy URL) enables `DatabaseSessionService` = durable sessions = the crash-resume beat.
- For the real-world loop, add `WATERLINE_SMTP_HOST/PORT/USER/PASS/FROM` to send real flight-following email.
- `dispatch_receipts` claims the itinerary before SMTP. This is intentionally **at-most-once**: if SMTP fails ambiguously after the claim, automatic retry remains suppressed and an operator must reconcile the receipt.
- `mission_events` is the append-only lifecycle proof. Each deterministic edge carries an event ID, trace ID, reason code, and bounded JSON evidence; raw contact PII and model reasoning are excluded.
- The deployed service fetches NAV CANADA data live. Local frozen captures are intentionally excluded from its image; an unavailable live source therefore fails visibly instead of silently redistributing copied payloads.

## 3. Frontend relay

The agent URL is a server-only runtime variable. The browser calls same-origin `/api/waterline/*`; the relay permits only mission creation, owner-bound timeline restore, bounded same-session recovery, and the owner-attestation command. For a remote agent, `google-auth-library` obtains a service-identity ID token and sends it as `X-Serverless-Authorization`. The HMAC additionally binds the injected pilot actor to the exact method, path, normalized body, and timestamp.

```bash
export WATERLINE_AGENT_URL="https://<agent>.run.app"
export WATERLINE_WEB_IMAGE="us-central1-docker.pkg.dev/<PROJECT>/waterline/waterline-web:latest"

cd web
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions="_IMAGE=${WATERLINE_WEB_IMAGE}" \
  .
gcloud run deploy waterline-web \
  --image="${WATERLINE_WEB_IMAGE}" \
  --region="${WATERLINE_REGION}" \
  --service-account="${WATERLINE_WEB_SA}" \
  --allow-unauthenticated \
  --set-env-vars="WATERLINE_AGENT_URL=${WATERLINE_AGENT_URL},WATERLINE_AGENT_AUDIENCE=${WATERLINE_AGENT_URL}" \
  --set-secrets="WATERLINE_RELAY_SECRET=waterline-relay-secret:latest" \
  --port=8080

gcloud run services add-iam-policy-binding waterline-agent \
  --region="${WATERLINE_REGION}" \
  --member="serviceAccount:${WATERLINE_WEB_SA}" \
  --role="roles/run.invoker"
```

The standalone container listens on `0.0.0.0:$PORT` as required by Cloud Run. For a local production check:

```bash
docker build -t waterline-web ./web
docker run --rm -p 8080:8080 -e PORT=8080 waterline-web
```

For local development, the relay and agent share a clearly local-only default signing secret when the agent URL is localhost. The relay still issues an HttpOnly owner session. A Cloud Run environment (`K_SERVICE`) has no secret fallback and fails closed unless `WATERLINE_RELAY_SECRET` is mounted.

## 4. On-camera proof (a scored requirement)
The demo video must show the backend running on Google Cloud. Film the public Waterline URL exercising the signed relay, then show the private `waterline-agent` revision and its request logs plus the Cloud SQL instance in Cloud Console. The agent URL intentionally rejects an anonymous browser request, so do not present direct browser access to `/health` as the proof path. After recording, **turn services off** (`min-instances 0` already helps) per the cost guidance.

Do not deploy the frontend to Vercel without replacing the Cloud Run service-identity relay; the current boundary intentionally relies on the `waterline-web` Google service account.
