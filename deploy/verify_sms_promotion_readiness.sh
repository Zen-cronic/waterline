#!/usr/bin/env bash
set -euo pipefail

WL_PROJECT_ID="${WL_PROJECT_ID:-ata-2026-waterline}"
WL_REGION="${WL_REGION:-us-central1}"
WL_GCLOUD_BIN="${WL_GCLOUD_BIN:-gcloud}"

runtime_sa="waterline-runtime@${WL_PROJECT_ID}.iam.gserviceaccount.com"
web_sa="waterline-web@${WL_PROJECT_ID}.iam.gserviceaccount.com"
runtime_member="serviceAccount:${runtime_sa}"
web_member="serviceAccount:${web_sa}"
secret_accessor_role="roles/secretmanager.secretAccessor"
failures=0

fail_check() {
  printf 'FAIL: %s\n' "$1" >&2
  failures=$((failures + 1))
}

has_secret_accessor() {
  local policy="$1"
  local member="$2"
  jq -e \
    --arg role "$secret_accessor_role" \
    --arg member "$member" \
    'any(.bindings[]?; .role == $role and any(.members[]?; . == $member))' \
    <<<"$policy" >/dev/null
}

for dependency in "$WL_GCLOUD_BIN" jq; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$dependency" >&2
    exit 2
  fi
done

active_project="$($WL_GCLOUD_BIN config get-value project 2>/dev/null)"
if [[ "$active_project" != "$WL_PROJECT_ID" ]]; then
  fail_check "active gcloud project is ${active_project:-unset}; expected ${WL_PROJECT_ID}"
else
  printf 'PASS: active project %s\n' "$WL_PROJECT_ID"
fi

declare -A web_access_required=(
  [waterline-twilio-account-sid]=false
  [waterline-twilio-auth-token]=true
  [waterline-twilio-from-number]=false
  [waterline-demo-sms-to]=false
  [waterline-handoff-secret]=true
)

for secret_name in \
  waterline-twilio-account-sid \
  waterline-twilio-auth-token \
  waterline-twilio-from-number \
  waterline-demo-sms-to \
  waterline-handoff-secret
do
  if ! "$WL_GCLOUD_BIN" secrets describe "$secret_name" \
    --project="$WL_PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
    fail_check "secret resource ${secret_name} is missing"
    continue
  fi

  enabled_version="$("$WL_GCLOUD_BIN" secrets versions list "$secret_name" \
    --project="$WL_PROJECT_ID" \
    --filter='state=ENABLED' \
    --limit=1 \
    --format='value(name)')"
  if [[ -z "$enabled_version" ]]; then
    fail_check "secret ${secret_name} has no enabled version"
    continue
  fi

  policy="$("$WL_GCLOUD_BIN" secrets get-iam-policy "$secret_name" \
    --project="$WL_PROJECT_ID" --format=json)"
  if ! has_secret_accessor "$policy" "$runtime_member"; then
    fail_check "runtime identity cannot access ${secret_name}"
  fi

  if [[ "${web_access_required[$secret_name]}" == "true" ]]; then
    if ! has_secret_accessor "$policy" "$web_member"; then
      fail_check "web identity cannot access ${secret_name}"
    fi
  elif has_secret_accessor "$policy" "$web_member"; then
    fail_check "web identity has unnecessary access to ${secret_name}"
  fi

  printf 'PASS: %s has an enabled version and scoped IAM\n' "$secret_name"
done

agent_iam="$("$WL_GCLOUD_BIN" run services get-iam-policy waterline-agent \
  --project="$WL_PROJECT_ID" --region="$WL_REGION" --format=json)"
agent_invokers="$(jq -c \
  '[.bindings[]? | select(.role == "roles/run.invoker") | .members[]?] | sort' \
  <<<"$agent_iam")"
expected_agent_invokers="$(jq -cn --arg member "$web_member" '[$member]')"
if [[ "$agent_invokers" != "$expected_agent_invokers" ]]; then
  fail_check "private agent invokers are not exactly the web service account"
else
  printf 'PASS: private agent invoker is exactly %s\n' "$web_sa"
fi

web_iam="$("$WL_GCLOUD_BIN" run services get-iam-policy waterline-web \
  --project="$WL_PROJECT_ID" --region="$WL_REGION" --format=json)"
web_invokers="$(jq -c \
  '[.bindings[]? | select(.role == "roles/run.invoker") | .members[]?] | sort' \
  <<<"$web_iam")"
if [[ "$web_invokers" != '["allUsers"]' ]]; then
  fail_check "public web invokers are not exactly allUsers"
else
  printf 'PASS: public web invoker is exactly allUsers\n'
fi

agent_service="$("$WL_GCLOUD_BIN" run services describe waterline-agent \
  --project="$WL_PROJECT_ID" --region="$WL_REGION" --format=json)"
outbound_mode="$(jq -r \
  '.spec.template.spec.containers[0].env[]? | select(.name == "WATERLINE_OUTBOUND_MODE") | .value' \
  <<<"$agent_service")"
if [[ "$outbound_mode" != "outbox" ]]; then
  fail_check "agent outbound mode is ${outbound_mode:-unset}; expected safe pre-promotion mode outbox"
else
  printf 'PASS: agent remains in outbox mode\n'
fi

web_service="$("$WL_GCLOUD_BIN" run services describe waterline-web \
  --project="$WL_PROJECT_ID" --region="$WL_REGION" --format=json)"
web_url="$(jq -r '.status.url // empty' <<<"$web_service")"
web_ready="$(jq -r \
  '.status.conditions[]? | select(.type == "Ready") | .status' \
  <<<"$web_service")"
if [[ "$web_url" != https://* || "$web_ready" != "True" ]]; then
  fail_check "public web service is not Ready with an HTTPS URL"
else
  printf 'PASS: public callback origin is ready at %s\n' "$web_url"
fi

if (( failures > 0 )); then
  printf 'SMS promotion readiness: BLOCKED (%d failed checks). No configuration changed.\n' \
    "$failures" >&2
  exit 1
fi

printf 'SMS promotion readiness: READY. No secret payload was read and no configuration changed.\n'
