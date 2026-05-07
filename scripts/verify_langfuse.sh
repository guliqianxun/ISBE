#!/usr/bin/env bash
# Verify Langfuse trace integration end-to-end.
#
# Prereq: complete the UI setup (see docs/superpowers/PROGRESS.md "用户操作清单")
# 1. Open http://localhost:3000 → sign up → create org+project
# 2. Settings → API Keys → create new keys
# 3. Add to .env:
#    LANGFUSE_PUBLIC_KEY=pk-lf-...
#    LANGFUSE_SECRET_KEY=sk-lf-...
# 4. Run this script.

set -euo pipefail
cd "$(dirname "$0")/.."

# Load .env
set -a
# shellcheck disable=SC1091
source .env
set +a

if [ -z "${LANGFUSE_PUBLIC_KEY:-}" ] || [ -z "${LANGFUSE_SECRET_KEY:-}" ]; then
  echo "ERROR: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are empty in .env" >&2
  echo "Visit http://localhost:3000 to create them." >&2
  exit 1
fi

echo "[1/3] Trigger a real LLM digest run..."
uv run radar topics run nowcasting --digest

echo
echo "[2/3] Wait 5s for Langfuse to ingest..."
sleep 5

echo
echo "[3/3] Query Langfuse API for traces..."
AUTH=$(printf "%s:%s" "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 -w0)
TRACE_COUNT=$(curl -sf "http://localhost:3000/api/public/traces?limit=10" \
  -H "Authorization: Basic $AUTH" \
  | python -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('data', [])))")

echo "Traces visible in Langfuse: $TRACE_COUNT"
if [ "$TRACE_COUNT" -gt "0" ]; then
  echo "✓ Langfuse end-to-end verified."
  echo "  See traces at http://localhost:3000"
else
  echo "WARN: no traces found. Check worker logs and verify keys match the project."
  exit 2
fi
