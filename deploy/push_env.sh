#!/usr/bin/env bash
# Copy the deploy's settings from .env into Vercel's Production environment. Values are piped, never printed.
# Run from the repo root:  bash deploy/push_env.sh      then redeploy:  vercel deploy --prod --yes
set -euo pipefail
cd "$(dirname "$0")/.."
NAMES="ANTHROPIC_API_KEY VEXCEL_API_KEY MATTERPORT_TOKEN_ID MATTERPORT_TOKEN_SECRET PHOTON_API_KEY PHOTON_PROJECT_ID
       PHOTON_WEBHOOK_SECRET SUPABASE_URL SUPABASE_SECRET_KEY PRIMESIGHT_PASSCODE PRIMESIGHT_MAX_LIVE"
val() { { grep -E "^$1=" .env || true; } | tail -1 | tr -d '' | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
set_var() {  # name value
  vercel env rm "$1" production -y >/dev/null 2>&1 || true
  printf %s "$2" | vercel env add "$1" production >/dev/null 2>&1 && echo "set     $1" || echo "FAILED  $1"
}
for n in $NAMES; do
  v="$(val "$n")"
  if [ -n "$v" ]; then set_var "$n" "$v"; else echo "missing $n (not in .env, skipped)"; fi
done
set_var PRIMESIGHT_REPLAY 1  # recorded demo calls answer instantly and free
