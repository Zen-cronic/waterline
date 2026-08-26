# Deploying Waterline to Google Cloud

Waterline is two Cloud Run services — the **agent** (FastAPI + ADK) and the **frontend** (Next.js standalone) — plus **PostGIS** on **Cloud SQL for PostgreSQL**. Both images are reproducible from committed manifests; the backend dependency graph is locked by Poetry and the frontend API URL is deliberately frozen into its client bundle at build time.

## Prerequisites
- `gcloud` CLI installed and authenticated (`gcloud auth login`), project selected (`gcloud config set project <PROJECT_ID>`).
- APIs enabled: `run.googleapis.com`, `cloudbuild.googleapis.com`, `sqladmin.googleapis.com`, `artifactregistry.googleapis.com`, `aiplatform.googleapis.com`, and `secretmanager.googleapis.com`.
- A Cloud Run runtime service account with Vertex AI User, Cloud SQL Client, and Secret Manager Secret Accessor roles. Do not create a service-account key.
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

gcloud run deploy waterline-agent \
  --source . \
  --region="${WATERLINE_REGION}" \
  --service-account="${WATERLINE_RUNTIME_SA}" \
  --allow-unauthenticated \
  --add-cloudsql-instances="${WATERLINE_SQL_CONNECTION}" \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${WATERLINE_PROJECT},GOOGLE_CLOUD_LOCATION=${WATERLINE_REGION}" \
  --set-secrets="DATABASE_URL=waterline-database-url:latest,WATERLINE_SESSION_DB=waterline-session-db:latest" \
  --port=8080 \
  --min-instances=0 \
  --max-instances=2
```
- Create `waterline-database-url` and `waterline-session-db` in Secret Manager before deploy; never put database passwords in the command or repository.
- `min-instances 0` and a small `--max-instances` cap per the hackathon cost guidance.
- `WATERLINE_SESSION_DB` (SQLAlchemy URL) enables `DatabaseSessionService` = durable sessions = the crash-resume beat.
- For the real-world loop, add `WATERLINE_SMTP_HOST/PORT/USER/PASS/FROM` to send real flight-following email.
- `dispatch_receipts` claims the itinerary before SMTP. This is intentionally **at-most-once**: if SMTP fails ambiguously after the claim, automatic retry remains suppressed and an operator must reconcile the receipt.
- The deployed service fetches NAV CANADA data live. Local frozen captures are intentionally excluded from its image; an unavailable live source therefore fails visibly instead of silently redistributing copied payloads.

## 3. Frontend

`NEXT_PUBLIC_AGENT_URL` is a browser-exposed Next.js variable and is frozen during `next build`. The checked-in `web/cloudbuild.yaml` passes it as a Docker build argument; changing only a Cloud Run runtime variable does not update the client bundle.

```bash
export WATERLINE_AGENT_URL="https://<agent>.run.app"
export WATERLINE_WEB_IMAGE="us-central1-docker.pkg.dev/<PROJECT>/waterline/waterline-web:latest"

cd web
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions="_NEXT_PUBLIC_AGENT_URL=${WATERLINE_AGENT_URL},_IMAGE=${WATERLINE_WEB_IMAGE}" \
  .
gcloud run deploy waterline-web \
  --image="${WATERLINE_WEB_IMAGE}" \
  --region="${WATERLINE_REGION}" \
  --allow-unauthenticated \
  --port=8080
```

The standalone container listens on `0.0.0.0:$PORT` as required by Cloud Run. For a local production check:

```bash
docker build --build-arg NEXT_PUBLIC_AGENT_URL=http://127.0.0.1:8088 \
  -t waterline-web ./web
docker run --rm -p 8080:8080 -e PORT=8080 waterline-web
```

## 4. On-camera proof (a scored requirement)
The demo video must show the backend running on Google Cloud. Film the live `https://<agent>.run.app/health` in the browser, and the Cloud Run + Cloud SQL instances in the Cloud Console. After recording, **turn services off** (`min-instances 0` already helps) per the cost guidance.

> Vercel note (if the frontend goes to Vercel instead of Cloud Run): deploy with `--scope zencronics-projects` and verify the deployed URL is not behind Vercel SSO before treating it as judge-reachable.
