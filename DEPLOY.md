# Deploying Waterline to Google Cloud

Waterline is two Cloud Run services — a **private agent** (FastAPI + ADK) and a public **frontend relay** (Next.js standalone) — plus **PostGIS** on **Cloud SQL for PostgreSQL**. The browser has no agent URL or credential. The frontend's service identity invokes the private agent; its exact-path relay issues a tamper-evident HttpOnly pilot session and signs that opaque owner plus the normalized command body.

For the guarded preview, run `./deploy/deploy_preview.sh` from a committed runtime tree. It builds both images with the current 12-character Git revision, deploys the private-agent/public-web boundary, grants only the web identity service-level invocation, and forces `WATERLINE_OUTBOUND_MODE=outbox` so preview verification cannot send an external message. The manual commands below document the same pieces and remain useful for recovery.

## Prerequisites
- `gcloud` CLI installed and authenticated (`gcloud auth login`), project selected (`gcloud config set project <PROJECT_ID>`).
- APIs enabled: `run.googleapis.com`, `cloudbuild.googleapis.com`, `sqladmin.googleapis.com`, `artifactregistry.googleapis.com`, `aiplatform.googleapis.com`, and `secretmanager.googleapis.com`.
- Separate `waterline-runtime` and `waterline-web` service accounts. The runtime needs Vertex AI User and Cloud SQL Client; both receive resource-scoped access to `waterline-relay-secret`. Do not create a service-account key.
- Docker for the local container preflight. Do not deploy until both image smoke checks pass.
- Run every local and cloud gate in `TESTING.md`. The external operator checklist is the authoritative live-resource record and contains direct Console links for project `ata-2026-waterline`.

The reusable Cargo Release incident ledger informed this contract: quote every `--format='…'` expression, inspect source bundles before build submission, keep routable endpoints separate from ID-token audiences, and treat Cloud Run ingress and IAM invocation as independent controls. Waterline resource names and roles remain project-specific; no Cargo identifiers or permissions are copied.

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
# tracked public-domain airport reference and synthetic condition-card evidence,
# but excludes local NAV CANADA captures and env files)
export WATERLINE_PROJECT="<PROJECT>"
export WATERLINE_REGION="us-central1"
export WATERLINE_SQL_CONNECTION="${WATERLINE_PROJECT}:${WATERLINE_REGION}:waterline-pg"
export WATERLINE_RUNTIME_SA="waterline-runtime@${WATERLINE_PROJECT}.iam.gserviceaccount.com"
export WATERLINE_WEB_SA="waterline-web@${WATERLINE_PROJECT}.iam.gserviceaccount.com"

test "$(gcloud config get-value project)" = "${WATERLINE_PROJECT}"
./deploy/verify_cloud_foundation.sh

gcloud run deploy waterline-agent \
  --source . \
  --region="${WATERLINE_REGION}" \
  --service-account="${WATERLINE_RUNTIME_SA}" \
  --no-allow-unauthenticated \
  --add-cloudsql-instances="${WATERLINE_SQL_CONNECTION}" \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${WATERLINE_PROJECT},WATERLINE_MODEL_LOCATION=global,WATERLINE_EVIDENCE_MODE=gemini,WATERLINE_OUTBOUND_MODE=outbox" \
  --set-secrets="DATABASE_URL=waterline-database-url:latest,WATERLINE_SESSION_DB=waterline-session-db:latest,WATERLINE_RELAY_SECRET=waterline-relay-secret:latest" \
  --port=8080 \
  --min-instances=0 \
  --max-instances=2
```
- Run `deploy/provision_cloud_sql.sh` and `deploy/provision_relay_identity.sh` first. Never put database passwords or the relay HMAC secret in a command or repository.
- `min-instances 0` and a small `--max-instances` cap per the hackathon cost guidance.
- `WATERLINE_SESSION_DB` (SQLAlchemy URL) enables `DatabaseSessionService` = durable sessions = the crash-resume beat.
- For the real-world loop, add `WATERLINE_SMTP_HOST/PORT/USER/PASS/FROM` to send real flight-following email.
- Preview deployment sets `WATERLINE_OUTBOUND_MODE=outbox` explicitly. This wins even if SMTP-looking variables are present and prevents accidental external delivery. Switch to `smtp` only with an operator-owned sender/destination and a separately approved secret.
- `dispatch_receipts` claims the itinerary before SMTP. This is intentionally **at-most-once**: if SMTP fails ambiguously after the claim, automatic retry remains suppressed and an operator must reconcile the receipt.
- `mission_events` is the append-only lifecycle proof. Each deterministic edge carries an event ID, trace ID, reason code, and bounded JSON evidence; raw contact PII and model reasoning are excluded.
- `WATERLINE_EVIDENCE_MODE=gemini` makes the deployed agent use Vertex AI for the allowlisted condition-card image. Local development defaults to deterministic, digest-bound fixture extraction. Neither mode exposes a public upload surface; both keep model authority false and store hostile content as a hash-only quarantine receipt.
- `WATERLINE_MODEL_LOCATION=global` is intentionally separate from the `us-central1` infrastructure region. Both the ADK fallback chain and typed visual extractor pass the global publisher location explicitly; this avoids the verified regional Gemini 3.5 `404 NOT_FOUND` failure without moving Cloud Run or Cloud SQL.
- The deployed service fetches NAV CANADA data live. Local frozen captures are intentionally excluded from its image; an unavailable live source therefore fails visibly instead of silently redistributing copied payloads.

## 3. Frontend relay

The agent URL is a server-only runtime variable. The browser calls same-origin `/api/waterline/*`; the relay permits only mission creation, owner-bound timeline restore, bounded same-session recovery, and the owner-attestation command. For a remote agent, `google-auth-library` obtains a service-identity ID token and sends it as `X-Serverless-Authorization`. The HMAC additionally binds the injected pilot actor to the exact method, path, normalized body, and timestamp.

```bash
export WATERLINE_AGENT_URL="https://<agent>.run.app"
export WATERLINE_AGENT_AUDIENCE="https://<canonical-agent>.run.app"
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
  --set-env-vars="WATERLINE_AGENT_URL=${WATERLINE_AGENT_URL},WATERLINE_AGENT_AUDIENCE=${WATERLINE_AGENT_AUDIENCE}" \
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

Keep `WATERLINE_AGENT_URL` (the routable request endpoint) and `WATERLINE_AGENT_AUDIENCE` (the exact canonical audience accepted by Cloud Run) separate even when they happen to be identical. Do not normalize away a required trailing slash. Verify the audience through a successful signed relay request and the private agent's Cloud Run request logs; never log the token itself.

## 4. Post-deploy verification

Run the preview-deployment acceptance section in `TESTING.md`. Use `/health` for a deployed health proof and corroborate it in Cloud Run request logs; do not assume a generic edge 404 came from application routing. Confirm the agent has no `allUsers` invoker binding, the web identity has only service-level `roles/run.invoker`, and both revisions point to immutable image digests.

The project-resolved Console pages are maintained in `/home/zin-kg/code/hackathons/allthingsagentic-2026/submission/waterline/google-cloud-setup-checklist.md`. That file, not this generic command template, records the actual revision names, URLs, image digests, live receipts, and verification timestamps.

## 5. On-camera proof (a scored requirement)
The demo video must show the backend running on Google Cloud. Film the public Waterline URL exercising the signed relay, then show the private `waterline-agent` revision and its request logs plus the Cloud SQL instance in Cloud Console. The agent URL intentionally rejects an anonymous browser request, so do not present direct browser access to `/health` as the proof path. After recording, **turn services off** (`min-instances 0` already helps) per the cost guidance.

Do not deploy the frontend to Vercel without replacing the Cloud Run service-identity relay; the current boundary intentionally relies on the `waterline-web` Google service account.
