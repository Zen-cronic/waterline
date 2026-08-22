# Deploying Waterline to Google Cloud

Waterline is two services: the **agent** (FastAPI + ADK, containerized) on **Cloud Run**, and **PostGIS** on **Cloud SQL for PostgreSQL**. The frontend can go on Cloud Run too (or any static host) pointed at the agent URL.

## Prerequisites
- `gcloud` CLI installed and authenticated (`gcloud auth login`), project selected (`gcloud config set project <PROJECT_ID>`).
- APIs enabled: `run.googleapis.com`, `sqladmin.googleapis.com`, `artifactregistry.googleapis.com`, `aiplatform.googleapis.com` (or a Gemini API key).
- A Gemini API key, or Vertex AI enabled (`GOOGLE_GENAI_USE_VERTEXAI=1`).

## 1. Cloud SQL (PostGIS)
```bash
gcloud sql instances create waterline-pg \
  --database-version=POSTGRES_16 --tier=db-f1-micro --region=us-central1
gcloud sql databases create waterline --instance=waterline-pg
gcloud sql users set-password postgres --instance=waterline-pg --password=<PW>
# enable PostGIS once, via the connection:
#   CREATE EXTENSION IF NOT EXISTS postgis;
# then load db/schema.sql
```
PostGIS ships with Cloud SQL for PostgreSQL — no build needed. Apply `db/schema.sql` through the Cloud SQL connection.

## 2. Agent on Cloud Run
```bash
# from waterline/ (build context = project root; Dockerfile at root)
gcloud run deploy waterline-agent \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --add-cloudsql-instances <PROJECT>:us-central1:waterline-pg \
  --set-env-vars "GOOGLE_API_KEY=<KEY>,GOOGLE_GENAI_USE_VERTEXAI=0,WATERLINE_MODEL_CHAIN=gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash" \
  --set-env-vars "DATABASE_URL=postgresql://postgres:<PW>@/waterline?host=/cloudsql/<PROJECT>:us-central1:waterline-pg" \
  --set-env-vars "WATERLINE_SESSION_DB=postgresql+pg8000://postgres:<PW>@/waterline?unix_sock=/cloudsql/<PROJECT>:us-central1:waterline-pg/.s.PGSQL.5432"
```
- `min-instances 0` and a small `--max-instances` cap per the hackathon cost guidance.
- `WATERLINE_SESSION_DB` (SQLAlchemy URL) enables `DatabaseSessionService` = durable sessions = the crash-resume beat.
- For the real-world loop, add `WATERLINE_SMTP_HOST/PORT/USER/PASS/FROM` to send real flight-following email.

## 3. Frontend
Deploy `web/` to Cloud Run (or any host) with `NEXT_PUBLIC_AGENT_URL=https://<agent>.run.app`.

## 4. On-camera proof (a scored requirement)
The demo video must show the backend running on Google Cloud. Film the live `https://<agent>.run.app/health` in the browser, and the Cloud Run + Cloud SQL instances in the Cloud Console. After recording, **turn services off** (`min-instances 0` already helps) per the cost guidance.

> Vercel note (if the frontend goes to Vercel instead of Cloud Run): deploy with `--scope zencronics-projects` and verify the deployed URL is not behind Vercel SSO before treating it as judge-reachable.
