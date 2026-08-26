#!/usr/bin/env bash
set -euo pipefail

WL_PROJECT_ID="${WL_PROJECT_ID:-ata-2026-waterline}"
WL_RELAY_SECRET="waterline-relay-secret"
WL_RUNTIME_SA="waterline-runtime@${WL_PROJECT_ID}.iam.gserviceaccount.com"
WL_WEB_SA="waterline-web@${WL_PROJECT_ID}.iam.gserviceaccount.com"

project="$(gcloud config get-value project 2>/dev/null)"
if [[ "$project" != "$WL_PROJECT_ID" ]]; then
  echo "Refusing to provision: active project is '$project', expected '$WL_PROJECT_ID'." >&2
  exit 1
fi

if ! gcloud secrets describe "$WL_RELAY_SECRET" --project="$WL_PROJECT_ID" >/dev/null 2>&1; then
  secret_dir="$(mktemp -d /tmp/waterline-relay.XXXXXX)"
  chmod 700 "$secret_dir"
  cleanup() {
    find "$secret_dir" -type f -exec shred -u {} + 2>/dev/null || true
    rmdir "$secret_dir" 2>/dev/null || true
  }
  trap cleanup EXIT
  openssl rand -hex 32 > "$secret_dir/relay-secret"
  chmod 600 "$secret_dir/relay-secret"
  gcloud secrets create "$WL_RELAY_SECRET" \
    --project="$WL_PROJECT_ID" \
    --replication-policy=automatic \
    --data-file="$secret_dir/relay-secret"
fi

for service_account in "$WL_RUNTIME_SA" "$WL_WEB_SA"; do
  gcloud secrets add-iam-policy-binding "$WL_RELAY_SECRET" \
    --project="$WL_PROJECT_ID" \
    --member="serviceAccount:${service_account}" \
    --role=roles/secretmanager.secretAccessor \
    --condition=None \
    --quiet >/dev/null
done

echo "Authenticated relay secret and resource-scoped runtime access are ready."
