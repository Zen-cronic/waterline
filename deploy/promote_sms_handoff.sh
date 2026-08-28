#!/usr/bin/env bash
set -euo pipefail

required_approval="I_APPROVE_SMS_HANDOFF_CONFIGURATION"
if [[ "${WATERLINE_SMS_PROMOTION_APPROVED:-}" != "$required_approval" ]]; then
  echo "SMS handoff configuration not changed." >&2
  echo "Set WATERLINE_SMS_PROMOTION_APPROVED=$required_approval to bind existing secrets and enable SMS mode." >&2
  exit 3
fi

WL_PROJECT_ID="${WL_PROJECT_ID:-ata-2026-waterline}"
WL_REGION="${WL_REGION:-us-central1}"
WL_GCLOUD_BIN="${WL_GCLOUD_BIN:-gcloud}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$("$WL_GCLOUD_BIN" config get-value project 2>/dev/null)" != "$WL_PROJECT_ID" ]]; then
  echo "Refusing to update services: active project does not match $WL_PROJECT_ID." >&2
  exit 1
fi

WL_PROJECT_ID="$WL_PROJECT_ID" \
WL_REGION="$WL_REGION" \
WL_GCLOUD_BIN="$WL_GCLOUD_BIN" \
  "$script_dir/verify_sms_promotion_readiness.sh"

web_url="$("$WL_GCLOUD_BIN" run services describe waterline-web \
  --project="$WL_PROJECT_ID" --region="$WL_REGION" --format='value(status.url)')"
if [[ "$web_url" != https://* ]]; then
  echo "Refusing to enable SMS: public Waterline URL is invalid." >&2
  exit 1
fi

# Public callback verification and the read-only signed summary must be ready first.
"$WL_GCLOUD_BIN" run services update waterline-web \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --update-env-vars="WATERLINE_PUBLIC_WEB_URL=${web_url}" \
  --update-secrets="TWILIO_AUTH_TOKEN=waterline-twilio-auth-token:latest,WATERLINE_HANDOFF_SECRET=waterline-handoff-secret:latest" \
  --quiet

"$WL_GCLOUD_BIN" run services update waterline-agent \
  --project="$WL_PROJECT_ID" \
  --region="$WL_REGION" \
  --update-env-vars="WATERLINE_OUTBOUND_MODE=sms,WATERLINE_PUBLIC_WEB_URL=${web_url}" \
  --update-secrets="TWILIO_ACCOUNT_SID=waterline-twilio-account-sid:latest,TWILIO_AUTH_TOKEN=waterline-twilio-auth-token:latest,TWILIO_FROM_NUMBER=waterline-twilio-from-number:latest,WATERLINE_DEMO_SMS_TO=waterline-demo-sms-to:latest,WATERLINE_HANDOFF_SECRET=waterline-handoff-secret:latest" \
  --quiet

printf 'SMS handoff configuration enabled.\n'
printf 'Public callback origin: %s\n' "$web_url"
printf 'No message was sent. A separate authenticated mission attestation is still required.\n'
