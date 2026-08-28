#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${MOCK_GCLOUD_LOG:-}" ]]; then
  printf '%s\n' "$*" >>"$MOCK_GCLOUD_LOG"
fi

case "$1 $2 $3" in
  "config get-value project")
    printf '%s\n' "${MOCK_PROJECT:-ata-2026-waterline}"
    ;;
  "secrets describe "*)
    if [[ "${MOCK_SMS_SECRETS:-ready}" == "missing" ]]; then
      exit 1
    fi
    printf 'projects/test/secrets/%s\n' "$3"
    ;;
  "secrets versions list")
    printf '1\n'
    ;;
  "secrets get-iam-policy "*)
    secret_name="$3"
    if [[ "$secret_name" == "waterline-twilio-auth-token" || \
      "$secret_name" == "waterline-handoff-secret" ]]; then
      members='["serviceAccount:waterline-runtime@ata-2026-waterline.iam.gserviceaccount.com","serviceAccount:waterline-web@ata-2026-waterline.iam.gserviceaccount.com"]'
    else
      members='["serviceAccount:waterline-runtime@ata-2026-waterline.iam.gserviceaccount.com"]'
    fi
    jq -cn --argjson members "$members" \
      '{bindings:[{role:"roles/secretmanager.secretAccessor",members:$members}]}'
    ;;
  "run services get-iam-policy")
    if [[ "$4" == "waterline-agent" ]]; then
      members='["serviceAccount:waterline-web@ata-2026-waterline.iam.gserviceaccount.com"]'
    else
      members='["allUsers"]'
    fi
    jq -cn --argjson members "$members" \
      '{bindings:[{role:"roles/run.invoker",members:$members}]}'
    ;;
  "run services describe")
    if [[ "$4" == "waterline-agent" ]]; then
      jq -cn '{spec:{template:{spec:{containers:[{env:[{name:"WATERLINE_OUTBOUND_MODE",value:"outbox"}]}]}}}}'
    elif [[ "$*" == *"--format=value(status.url)"* ]]; then
      printf 'https://waterline-web.example.test\n'
    else
      jq -cn '{status:{url:"https://waterline-web.example.test",conditions:[{type:"Ready",status:"True"}]}}'
    fi
    ;;
  "run services update")
    printf 'updated %s\n' "$4"
    ;;
  *)
    printf 'Unexpected mock gcloud invocation: %s\n' "$*" >&2
    exit 64
    ;;
esac
