#!/usr/bin/env bash
# Prints NAME=value lines for running the API against the local Supabase (CI and `make dev`).
# Local-only values: the seed's role passwords, a throwaway master key, the local Auth keys.
# Usage: scripts/local_api_env.sh >> "$GITHUB_ENV"   or   set -a; eval "$(scripts/local_api_env.sh)"; set +a
set -euo pipefail
status=$(supabase status -o env)
get() { printf '%s\n' "$status" | sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p"; }
db_host="127.0.0.1:$(printf '%s\n' "$(get DB_URL)" | sed -E 's#.*@[^:]+:([0-9]+)/.*#\1#')"
cat <<ENV
REGION=uk
DATABASE_URL=postgresql://app_api:app_api_local@${db_host}/postgres
DATABASE_URL_DIRECT=postgresql://app_worker:app_worker_local@${db_host}/postgres
SUPABASE_URL=$(get API_URL)
SUPABASE_ANON_KEY=$(get ANON_KEY)
SUPABASE_SERVICE_ROLE_KEY=$(get SERVICE_ROLE_KEY)
APP_ORIGIN=${APP_ORIGIN:-http://localhost:5173}
KEY_PROVIDER=local
LOCAL_MASTER_KEY=${LOCAL_MASTER_KEY:-$(head -c 32 /dev/urandom | base64)}
PACKS_DIR=${PACKS_DIR:-$(cd "$(dirname "$0")/.." && pwd)/packs}
ENV
