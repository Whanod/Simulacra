#!/usr/bin/env bash
# Wipe every run from the API's SQLite store via POST /admin/purge.
#
# Reads:
#   DEFI_SIM_ADMIN_TOKEN  (required) — must match the token configured on the API
#   DEFI_SIM_API_URL      (optional) — defaults to http://localhost:8000
#
# Usage:
#   DEFI_SIM_ADMIN_TOKEN=xxx scripts/purge.sh
#   DEFI_SIM_API_URL=https://prod.example.com DEFI_SIM_ADMIN_TOKEN=xxx scripts/purge.sh

set -euo pipefail

API_URL="${DEFI_SIM_API_URL:-http://localhost:8000}"
TOKEN="${DEFI_SIM_ADMIN_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "error: DEFI_SIM_ADMIN_TOKEN is not set" >&2
  exit 2
fi

echo "[purge] target: $API_URL"
read -r -p "[purge] this deletes ALL runs. type 'purge' to continue: " CONFIRM
if [[ "$CONFIRM" != "purge" ]]; then
  echo "[purge] aborted"
  exit 1
fi

HTTP_OUT="$(mktemp)"
trap 'rm -f "$HTTP_OUT"' EXIT

STATUS="$(curl -sS -o "$HTTP_OUT" -w '%{http_code}' \
  -X POST \
  -H "X-Admin-Token: $TOKEN" \
  "$API_URL/admin/purge")"

echo "[purge] HTTP $STATUS"
cat "$HTTP_OUT"
echo

if [[ "$STATUS" != "200" ]]; then
  exit 1
fi
