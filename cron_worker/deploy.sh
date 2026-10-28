#!/bin/sh
# Puts the hourly clock on Cloudflare: upload worker.js, give it the GitHub token as a secret, set the cron.
# Needs in ../.env: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_WORKERS_TOKEN (Workers Scripts: Edit), GH_DISPATCH_TOKEN.
set -e
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
A="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/naharak-cron"
H="Authorization: Bearer $CLOUDFLARE_WORKERS_TOKEN"
ok() { grep -q '"success": *true' || { echo "FAILED: $1"; exit 1; }; }
curl -s -X PUT "$A" -H "$H" \
  -F 'metadata={"main_module":"worker.js","compatibility_date":"2026-01-01"};type=application/json' \
  -F "worker.js=@cron_worker/worker.js;type=application/javascript+module" | ok upload
printf '{"name":"GH_TOKEN","text":"%s","type":"secret_text"}' "$GH_DISPATCH_TOKEN" |
  curl -s -X PUT "$A/secrets" -H "$H" -H "Content-Type: application/json" --data-binary @- | ok secret
curl -s -X PUT "$A/schedules" -H "$H" -H "Content-Type: application/json" -d '[{"cron":"23 4-21 * * *"}]' | ok cron
echo "clock is on Cloudflare"
