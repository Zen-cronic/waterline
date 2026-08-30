#!/usr/bin/env bash
set -euo pipefail

WL_PROJECT_ID="${WL_PROJECT_ID:-ata-2026-waterline}"
WL_REGION="${WL_REGION:-us-central1}"
WL_SQL_INSTANCE="${WL_SQL_INSTANCE:-waterline-pg}"
WL_ARTIFACT_REPO="${WL_ARTIFACT_REPO:-waterline}"
WL_RUNTIME_SA="waterline-runtime@${WL_PROJECT_ID}.iam.gserviceaccount.com"
WL_WEB_SA="waterline-web@${WL_PROJECT_ID}.iam.gserviceaccount.com"
WL_FIREBASE_API_KEY="${WL_FIREBASE_API_KEY:?Set WL_FIREBASE_API_KEY from the registered Firebase web app}"
WL_FIREBASE_AUTH_DOMAIN="${WL_FIREBASE_AUTH_DOMAIN:-${WL_PROJECT_ID}.firebaseapp.com}"
WL_FIREBASE_PROJECT_ID="${WL_FIREBASE_PROJECT_ID:-$WL_PROJECT_ID}"
WL_FIREBASE_STORAGE_BUCKET="${WL_FIREBASE_STORAGE_BUCKET:-${WL_PROJECT_ID}.firebasestorage.app}"
WL_FIREBASE_MESSAGING_SENDER_ID="${WL_FIREBASE_MESSAGING_SENDER_ID:?Set WL_FIREBASE_MESSAGING_SENDER_ID}"
WL_FIREBASE_APP_ID="${WL_FIREBASE_APP_ID:?Set WL_FIREBASE_APP_ID}"

project="$(gcloud config get-value project 2>/dev/null)"
if [[ "$project" != "$WL_PROJECT_ID" ]]; then
  echo "Refusing to deploy: active project is '$project', expected '$WL_PROJECT_ID'." >&2
  exit 1
fi

if [[ "$(gcloud config get-value run/region 2>/dev/null)" != "$WL_REGION" ]]; then
  echo "Refusing to deploy: configured Cloud Run region is not '$WL_REGION'." >&2
  exit 1
fi

revision="$(git rev-parse --short=12 HEAD)"
if ! [[ "$revision" =~ ^[0-9a-f]{12}$ ]]; then
  echo "Refusing to deploy: could not resolve a 12-character Git revision." >&2
  exit 1
fi

runtime_paths=(
  .dockerignore .gcloudignore Dockerfile
  agent data db firebase.json firestore.rules firestore.indexes.json
  web/.dockerignore web/.gcloudignore web/Dockerfile web/cloudbuild.yaml
  web/next.config.ts web/package.json web/pnpm-lock.yaml web/src web/public
)
if ! git diff --quiet -- "${runtime_paths[@]}" || \
   ! git diff --cached --quiet -- "${runtime_paths[@]}"; then
  echo "Refusing to deploy: runtime files differ from commit $revision." >&2
  exit 1
fi

agent_image="${WL_REGION}-docker.pkg.dev/${WL_PROJECT_ID}/${WL_ARTIFACT_REPO}/waterline-agent:${revision}"
web_image="${WL_REGION}-docker.pkg.dev/${WL_PROJECT_ID}/${WL_ARTIFACT_REPO}/waterline-web:${revision}"
sql_connection="${WL_PROJECT_ID}:${WL_REGION}:${WL_SQL_INSTANCE}"

gcloud builds submit . \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --tag="$agent_image" \
  --quiet

gcloud run deploy waterline-agent \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --platform=managed \
  --image="$agent_image" \
  --service-account="$WL_RUNTIME_SA" \
  --ingress=all \
  --no-allow-unauthenticated \
  --set-cloudsql-instances="$sql_connection" \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${WL_PROJECT_ID},WATERLINE_MODEL_LOCATION=global,WATERLINE_EVIDENCE_MODE=gemini,WATERLINE_EMBEDDING_MODEL=gemini-embedding-001,WATERLINE_GEMMA_RANKER_ENABLED=true,WATERLINE_GEMMA_MODEL=google/gemma-4-26b-a4b-it-maas,WATERLINE_GEMMA_LOCATION=global" \
  --set-secrets="DATABASE_URL=waterline-database-url:latest,WATERLINE_SESSION_DB=waterline-session-db:latest,WATERLINE_RELAY_SECRET=waterline-relay-secret:latest,WATERLINE_HANDOFF_SECRET=waterline-handoff-secret:latest" \
  --port=8080 \
  --cpu=1 \
  --memory=1Gi \
  --concurrency=2 \
  --timeout=900 \
  --min-instances=0 \
  --max-instances=2 \
  --labels="commit-sha=${revision},deployment=preview" \
  --quiet

agent_url="$(gcloud run services describe waterline-agent \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --format='value(status.url)')"
if [[ "$agent_url" != https://* ]]; then
  echo "Refusing to continue: Cloud Run returned an invalid agent URL." >&2
  exit 1
fi

gcloud run services add-iam-policy-binding waterline-agent \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --member="serviceAccount:${WL_WEB_SA}" \
  --role=roles/run.invoker \
  --condition=None \
  --quiet >/dev/null

gcloud builds submit web \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --config=web/cloudbuild.yaml \
  --substitutions="_IMAGE=${web_image}" \
  --quiet

WL_ACCESS_TOKEN="$(gcloud auth print-access-token)" \
  WL_PROJECT_ID="$WL_PROJECT_ID" \
  node deploy/deploy_firestore_rules.mjs
for collection_group in handoff_threads members messages; do
  gcloud firestore fields ttls update expiresAt \
    --project="$WL_PROJECT_ID" \
    --database='(default)' \
    --collection-group="$collection_group" \
    --enable-ttl \
    --async \
    --quiet
done

gcloud run deploy waterline-web \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --platform=managed \
  --image="$web_image" \
  --service-account="$WL_WEB_SA" \
  --ingress=all \
  --allow-unauthenticated \
  --set-env-vars="WATERLINE_AGENT_URL=${agent_url},WATERLINE_AGENT_AUDIENCE=${agent_url},GOOGLE_CLOUD_PROJECT=${WL_PROJECT_ID},FIREBASE_API_KEY=${WL_FIREBASE_API_KEY},FIREBASE_AUTH_DOMAIN=${WL_FIREBASE_AUTH_DOMAIN},FIREBASE_PROJECT_ID=${WL_FIREBASE_PROJECT_ID},FIREBASE_STORAGE_BUCKET=${WL_FIREBASE_STORAGE_BUCKET},FIREBASE_MESSAGING_SENDER_ID=${WL_FIREBASE_MESSAGING_SENDER_ID},FIREBASE_APP_ID=${WL_FIREBASE_APP_ID}" \
  --set-secrets="WATERLINE_RELAY_SECRET=waterline-relay-secret:latest,WATERLINE_HANDOFF_SECRET=waterline-handoff-secret:latest" \
  --port=8080 \
  --cpu=1 \
  --memory=512Mi \
  --concurrency=40 \
  --timeout=300 \
  --min-instances=0 \
  --max-instances=2 \
  --labels="commit-sha=${revision},deployment=preview" \
  --quiet

web_url="$(gcloud run services describe waterline-web \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --format='value(status.url)')"

printf 'Preview deployment complete.\n'
printf 'Commit: %s\n' "$revision"
printf 'Agent image: %s\n' "$agent_image"
printf 'Agent URL (private): %s\n' "$agent_url"
printf 'Web image: %s\n' "$web_image"
printf 'Web URL (public): %s\n' "$web_url"
printf 'Follower room: Firestore with anonymous-auth authorization bridge\n'
