#!/usr/bin/env bash
# Verify Phoenix trace integration end-to-end. No login required.
#
# Prereq:
# 1. docker compose up -d  (phoenix listens on :6006)
# 2. .env has PHOENIX_COLLECTOR_ENDPOINT set
# 3. LLM keys (DEEPSEEK_API_KEY or ANTHROPIC_API_KEY) set

set -euo pipefail
cd "$(dirname "$0")/.."

set -a
# shellcheck disable=SC1091
source .env
set +a

ENDPOINT="${PHOENIX_COLLECTOR_ENDPOINT:-http://localhost:6006/v1/traces}"
# Derive UI base URL from collector endpoint (strip /v1/traces)
UI_BASE="${ENDPOINT%/v1/traces}"
PROJECT="${PHOENIX_PROJECT_NAME:-isbe}"

if [ -z "${ENDPOINT}" ]; then
  echo "ERROR: PHOENIX_COLLECTOR_ENDPOINT is empty in .env" >&2
  exit 1
fi

echo "[1/3] Trigger a real LLM digest run..."
uv run radar topics run nowcasting --digest

echo
echo "[2/3] Wait 5s for Phoenix to ingest..."
sleep 5

echo
echo "[3/3] Query Phoenix for spans in project '${PROJECT}'..."
# Phoenix exposes a GraphQL API; simplest check: hit projects list and confirm ours has spans.
SPAN_COUNT=$(curl -sf -X POST "${UI_BASE}/graphql" \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"{ projects { edges { node { name traceCount } } } }\"}" \
  | python -c "
import sys, json
data = json.load(sys.stdin)
edges = data.get('data', {}).get('projects', {}).get('edges', [])
for e in edges:
    n = e.get('node', {})
    if n.get('name') == '${PROJECT}':
        print(n.get('traceCount', 0)); sys.exit(0)
print(0)
")

echo "Traces visible in Phoenix project '${PROJECT}': ${SPAN_COUNT}"
if [ "${SPAN_COUNT}" -gt "0" ]; then
  echo "✓ Phoenix end-to-end verified."
  echo "  See traces at ${UI_BASE}"
else
  echo "WARN: no traces found. Check worker logs and PHOENIX_COLLECTOR_ENDPOINT reachability." >&2
  exit 2
fi
