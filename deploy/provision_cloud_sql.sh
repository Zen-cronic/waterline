#!/usr/bin/env bash
set -euo pipefail

WL_PROJECT_ID="${WL_PROJECT_ID:-ata-2026-waterline}"
WL_REGION="${WL_REGION:-us-central1}"
WL_SQL_INSTANCE="${WL_SQL_INSTANCE:-waterline-pg}"
WL_SQL_DATABASE="${WL_SQL_DATABASE:-waterline}"
WL_SQL_USER="${WL_SQL_USER:-waterline_app}"
WL_RUNTIME_SA="waterline-runtime@${WL_PROJECT_ID}.iam.gserviceaccount.com"
WL_SQL_CONNECTION="${WL_PROJECT_ID}:${WL_REGION}:${WL_SQL_INSTANCE}"
WL_DB_SECRET="waterline-database-url"
WL_SESSION_SECRET="waterline-session-db"

project="$(gcloud config get-value project 2>/dev/null)"
if [[ "$project" != "$WL_PROJECT_ID" ]]; then
  echo "Refusing to provision: active project is '$project', expected '$WL_PROJECT_ID'." >&2
  exit 1
fi

if ! command -v cloud-sql-proxy >/dev/null 2>&1; then
  if [[ -x /tmp/cloud-sql-proxy ]]; then
    PATH="/tmp:${PATH}"
    export PATH
  else
    echo "Cloud SQL Auth Proxy is required; place the executable at /tmp/cloud-sql-proxy." >&2
    exit 1
  fi
fi

gcloud sql instances describe "$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" \
  --format='value(state)' | grep -qx RUNNABLE

gcloud sql databases describe "$WL_SQL_DATABASE" \
  --instance="$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" >/dev/null

has_user=false
if gcloud sql users list \
  --instance="$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" \
  --filter="name=${WL_SQL_USER}" \
  --format='value(name)' | grep -qx "$WL_SQL_USER"; then
  has_user=true
fi

has_db_secret=false
if gcloud secrets describe "$WL_DB_SECRET" --project="$WL_PROJECT_ID" >/dev/null 2>&1; then
  has_db_secret=true
fi

has_session_secret=false
if gcloud secrets describe "$WL_SESSION_SECRET" --project="$WL_PROJECT_ID" >/dev/null 2>&1; then
  has_session_secret=true
fi

if [[ "$has_db_secret" != "$has_session_secret" ]]; then
  echo "Refusing to rotate partial database credentials; reconcile the two secrets first." >&2
  exit 1
fi

if [[ "$has_db_secret" == true && "$has_user" != true ]]; then
  echo "Refusing to continue: database secrets exist but the application user is missing." >&2
  exit 1
fi

secret_dir="$(mktemp -d /tmp/waterline-cloudsql.XXXXXX)"
chmod 700 "$secret_dir"
proxy_pid=""
cleanup() {
  if [[ -n "$proxy_pid" ]]; then
    kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
  fi
  find "$secret_dir" -type f -exec shred -u {} + 2>/dev/null || true
  rmdir "$secret_dir" 2>/dev/null || true
}
trap cleanup EXIT

make_prompt_file() {
  local password_file="$1"
  local prompt_file="$2"
  {
    tr -d '\n' < "$password_file"
    printf '\n'
    tr -d '\n' < "$password_file"
    printf '\n'
  } > "$prompt_file"
  chmod 600 "$prompt_file"
}

if [[ ! "$WL_SQL_USER" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "Refusing unsafe SQL role name '$WL_SQL_USER'." >&2
  exit 1
fi

if [[ "$has_db_secret" != true ]]; then
  openssl rand -hex 32 > "$secret_dir/app-password"
  chmod 600 "$secret_dir/app-password"
fi

openssl rand -hex 32 > "$secret_dir/postgres-password"
chmod 600 "$secret_dir/postgres-password"
make_prompt_file "$secret_dir/postgres-password" "$secret_dir/postgres-password-prompt"
gcloud sql users set-password postgres \
  --instance="$WL_SQL_INSTANCE" \
  --project="$WL_PROJECT_ID" \
  --prompt-for-password < "$secret_dir/postgres-password-prompt"

{
  printf '*:*:*:postgres:'
  tr -d '\n' < "$secret_dir/postgres-password"
  printf '\n'
} > "$secret_dir/pgpass"
chmod 600 "$secret_dir/pgpass"

{
  if [[ "$has_db_secret" != true ]]; then
    if [[ "$has_user" == true ]]; then
      printf 'ALTER ROLE %s WITH LOGIN PASSWORD ' "$WL_SQL_USER"
    else
      printf 'CREATE ROLE %s WITH LOGIN PASSWORD ' "$WL_SQL_USER"
    fi
    printf "'"
    tr -d '\n' < "$secret_dir/app-password"
    printf "';\n"
  fi
  printf '\\ir %s/db/cloud_setup.sql\n' "$(pwd -P)"
} > "$secret_dir/setup.sql"
chmod 600 "$secret_dir/setup.sql"

cloud-sql-proxy \
  --gcloud-auth \
  --address=127.0.0.1 \
  --port=9470 \
  "$WL_SQL_CONNECTION" > "$secret_dir/proxy.log" 2>&1 &
proxy_pid="$!"
for _ in {1..30}; do
  if pg_isready --host=127.0.0.1 --port=9470 --timeout=1 >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$proxy_pid" 2>/dev/null; then
    sed -n '1,120p' "$secret_dir/proxy.log" >&2
    exit 1
  fi
  sleep 1
done
pg_isready --host=127.0.0.1 --port=9470 --timeout=1 >/dev/null

PGPASSFILE="$secret_dir/pgpass" psql \
  --host=127.0.0.1 \
  --port=9470 \
  --username=postgres \
  --dbname="$WL_SQL_DATABASE" \
  --set="app_user=$WL_SQL_USER" \
  --file="$secret_dir/setup.sql"

if [[ "$has_db_secret" != true ]]; then
  {
    printf 'postgresql://%s:' "$WL_SQL_USER"
    tr -d '\n' < "$secret_dir/app-password"
    printf '@/%s?host=/cloudsql/%s' "$WL_SQL_DATABASE" "$WL_SQL_CONNECTION"
  } > "$secret_dir/database-url"
  {
    printf 'postgresql+pg8000://%s:' "$WL_SQL_USER"
    tr -d '\n' < "$secret_dir/app-password"
    printf '@/%s?unix_sock=/cloudsql/%s/.s.PGSQL.5432' "$WL_SQL_DATABASE" "$WL_SQL_CONNECTION"
  } > "$secret_dir/session-db"
  chmod 600 "$secret_dir/database-url" "$secret_dir/session-db"

  gcloud secrets create "$WL_DB_SECRET" \
    --project="$WL_PROJECT_ID" \
    --replication-policy=automatic \
    --data-file="$secret_dir/database-url"
  gcloud secrets create "$WL_SESSION_SECRET" \
    --project="$WL_PROJECT_ID" \
    --replication-policy=automatic \
    --data-file="$secret_dir/session-db"
fi

for secret_name in "$WL_DB_SECRET" "$WL_SESSION_SECRET"; do
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --project="$WL_PROJECT_ID" \
    --member="serviceAccount:${WL_RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor \
    --condition=None \
    --quiet >/dev/null
done

echo "Cloud SQL user, current schema, database secrets, and resource-scoped IAM are ready."
