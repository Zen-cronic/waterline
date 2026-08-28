#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
verifier="${repo_root}/deploy/verify_sms_promotion_readiness.sh"
promoter="${repo_root}/deploy/promote_sms_handoff.sh"
mock_gcloud="${repo_root}/deploy/tests/mock_gcloud.sh"

WL_GCLOUD_BIN="$mock_gcloud" "$verifier" >/dev/null

failure_output="$(mktemp)"
mock_log="$(mktemp)"
trap 'rm -f "$failure_output" "$mock_log"' EXIT
if MOCK_SMS_SECRETS=missing WL_GCLOUD_BIN="$mock_gcloud" \
  "$verifier" >"$failure_output" 2>&1; then
  printf 'Expected missing SMS secrets to block readiness.\n' >&2
  exit 1
fi

grep -Fq 'SMS promotion readiness: BLOCKED (5 failed checks).' "$failure_output"
grep -Fq 'No configuration changed.' "$failure_output"

if WL_GCLOUD_BIN="$mock_gcloud" "$promoter" >"$failure_output" 2>&1; then
  printf 'Expected promotion without approval to fail closed.\n' >&2
  exit 1
fi
grep -Fq 'SMS handoff configuration not changed.' "$failure_output"

: >"$mock_log"
if MOCK_SMS_SECRETS=missing \
  MOCK_GCLOUD_LOG="$mock_log" \
  WL_GCLOUD_BIN="$mock_gcloud" \
  WATERLINE_SMS_PROMOTION_APPROVED=I_APPROVE_SMS_HANDOFF_CONFIGURATION \
  "$promoter" >"$failure_output" 2>&1; then
  printf 'Expected promotion with missing secrets to fail before rollout.\n' >&2
  exit 1
fi
if grep -Fq 'run services update' "$mock_log"; then
  printf 'Promotion attempted a Cloud Run update after failed readiness.\n' >&2
  exit 1
fi

: >"$mock_log"
MOCK_GCLOUD_LOG="$mock_log" \
WL_GCLOUD_BIN="$mock_gcloud" \
WATERLINE_SMS_PROMOTION_APPROVED=I_APPROVE_SMS_HANDOFF_CONFIGURATION \
  "$promoter" >/dev/null
mapfile -t updates < <(grep -F 'run services update' "$mock_log")
[[ "${#updates[@]}" -eq 2 ]]
[[ "${updates[0]}" == *"run services update waterline-web"* ]]
[[ "${updates[1]}" == *"run services update waterline-agent"* ]]

printf 'SMS promotion readiness tests passed.\n'
