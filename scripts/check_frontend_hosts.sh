#!/usr/bin/env bash
# Fails if the frontend build references Supabase, Azure or AWS hosts or SDKs (rule 4).
set -euo pipefail
DIST="${1:-web/dist}"
PATTERN='supabase\.co|supabase\.in|@supabase/|cognitiveservices\.azure\.com|\.azure\.com|amazonaws\.com|bedrock|SUPABASE_ANON|SUPABASE_URL'
if grep -RInE "$PATTERN" "$DIST" web/src 2>/dev/null; then
  echo "Forbidden host or SDK found in the frontend. The browser must talk only to the Avodeck API."
  exit 1
fi
echo "Frontend host check passed."
