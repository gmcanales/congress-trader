#!/usr/bin/env bash
set -euo pipefail

GIT_HASH=$(git rev-parse --short HEAD)
export GIT_HASH

echo "Building with asset version: $GIT_HASH"
docker compose up -d --build "$@"
echo "Done. Purge Cloudflare cache if serving via goosebase.org."
