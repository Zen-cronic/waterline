#!/usr/bin/env bash
set -euo pipefail

WL_PROJECT_ID="${WL_PROJECT_ID:-ata-2026-waterline}"
WL_REGION="${WL_REGION:-us-central1}"
WL_SQL_INSTANCE="${WL_SQL_INSTANCE:-waterline-pg}"
WL_RUNTIME_SA="waterline-runtime@${WL_PROJECT_ID}.iam.gserviceaccount.com"

required_apis=(
  run.googleapis.com
  cloudbuild.googleapis.com
  artifactregistry.googleapis.com
  aiplatform.googleapis.com
  sqladmin.googleapis.com
  secretmanager.googleapis.com
  iam.googleapis.com
  firestore.googleapis.com
  identitytoolkit.googleapis.com
  firebaserules.googleapis.com
)
enabled_apis="$(gcloud services list --enabled --project="$WL_PROJECT_ID" --format='value(config.name)')"
for api in "${required_apis[@]}"; do
  grep -qx "$api" <<< "$enabled_apis"
done
printf 'Required APIs: %s/%s enabled\n' "${#required_apis[@]}" "${#required_apis[@]}"

gcloud artifacts repositories describe waterline \
  --project="$WL_PROJECT_ID" \
  --location="$WL_REGION" \
  --format='table(name.basename(),format,location)'

gcloud iam service-accounts list \
  --project="$WL_PROJECT_ID" \
  --filter='email:waterline-' \
  --format='table(email,displayName,disabled)'

gcloud projects get-iam-policy "$WL_PROJECT_ID" \
  --flatten='bindings[].members' \
  --filter="bindings.members:${WL_RUNTIME_SA}" \
  --format='table(bindings.role)'

gcloud sql instances describe "$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" \
  --format='table(name,state,databaseVersion,region,settings.tier,settings.dataDiskSizeGb,connectionName)'
gcloud sql databases list \
  --instance="$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" \
  --filter='name=waterline' \
  --format='table(name,charset)'
gcloud sql users list \
  --instance="$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" \
  --filter='name=waterline_app' \
  --format='table(name,type)'

for secret_name in waterline-database-url waterline-session-db; do
  gcloud secrets versions list "$secret_name" \
    --project="$WL_PROJECT_ID" \
    --filter='state=ENABLED' \
    --format='table(name,state,createTime)'
  gcloud secrets get-iam-policy "$secret_name" \
    --project="$WL_PROJECT_ID" \
    --flatten='bindings[].members' \
    --filter="bindings.members:${WL_RUNTIME_SA}" \
    --format='table(bindings.role)'
done

for service_account in "$WL_RUNTIME_SA" "waterline-web@${WL_PROJECT_ID}.iam.gserviceaccount.com"; do
  gcloud secrets get-iam-policy waterline-handoff-secret \
    --project="$WL_PROJECT_ID" \
    --flatten='bindings[].members' \
    --filter="bindings.members:${service_account}" \
    --format='table(bindings.role,bindings.members)'
done

gcloud projects get-iam-policy "$WL_PROJECT_ID" \
  --flatten='bindings[].members' \
  --filter="bindings.role:roles/datastore.user AND bindings.members:waterline-web@${WL_PROJECT_ID}.iam.gserviceaccount.com" \
  --format='table(bindings.role,bindings.members)'

gcloud secrets versions list waterline-relay-secret \
  --project="$WL_PROJECT_ID" \
  --filter='state=ENABLED' \
  --format='table(name,state,createTime)'
for service_account in "$WL_RUNTIME_SA" "waterline-web@${WL_PROJECT_ID}.iam.gserviceaccount.com"; do
  gcloud secrets get-iam-policy waterline-relay-secret \
    --project="$WL_PROJECT_ID" \
    --flatten='bindings[].members' \
    --filter="bindings.members:${service_account}" \
    --format='table(bindings.role,bindings.members)'
done

gcloud run services list \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --format='table(metadata.name,status.url)'
