#!/usr/bin/env bash
# Server-side bootstrap. Run from the directory holding the loaded tar +
# docker-compose.yml + .env on the Linux amd64 server.
#
# Idempotent: safe to re-run.

set -euo pipefail

cd "$(dirname "$0")/.."   # script lives in scripts/, repo root one up

if [[ ! -f docker-compose.yml ]]; then
  echo "ERROR: docker-compose.yml not found in $(pwd)" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "WARNING: no .env found; copy .env.example to .env and fill secrets first" >&2
fi

if [[ -f isbe-images.tar ]]; then
  echo "==> Loading images from isbe-images.tar ..."
  docker load -i isbe-images.tar
elif [[ -f isbe-images.tar.gz ]]; then
  echo "==> Loading images from isbe-images.tar.gz ..."
  gunzip -c isbe-images.tar.gz | docker load
else
  echo "WARNING: no isbe-images.tar(.gz) found; assuming images are already loaded" >&2
fi

echo "==> Starting infra (server-style: only :4200 published) ..."
docker compose -f docker-compose.yml up -d

echo "==> Waiting for postgres ..."
until docker compose -f docker-compose.yml exec -T postgres pg_isready -U "${POSTGRES_USER:-isbe}" >/dev/null 2>&1; do
  sleep 2
done

echo "==> Running alembic migrations ..."
docker compose -f docker-compose.yml --profile worker run --rm \
  --entrypoint alembic radar-worker upgrade head \
  || echo "  (skipped — run manually if needed)"

echo "==> Starting worker ..."
docker compose -f docker-compose.yml --profile worker up -d

echo
echo "Done. Prefect UI: http://<server-ip>:4200"
echo "Other UIs (phoenix, minio, uptime-kuma) are internal-only."
echo "To reach them, SSH-tunnel:  ssh -L 6006:localhost:6006 user@server"
echo "  then forward via:  docker compose exec ... or use socat sidecar"
