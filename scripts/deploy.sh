#!/usr/bin/env bash
# Deploy the current `main` on the VPS. Refuses to run on a dirty tree or any branch other than main.
# Usage: scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."

branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ]; then
  echo "refusing to deploy: on branch '$branch', not 'main'" >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "refusing to deploy: the working tree has local changes (no edits on the VPS):" >&2
  git status --short >&2
  exit 1
fi

git pull --ff-only origin main

compose=(docker compose -f infra/docker-compose.yml)
"${compose[@]}" up --build -d
"${compose[@]}" ps

# Every rebuild moves the `:local` tags and leaves the previous image untagged ("dangling"), and BuildKit keeps a
# cache. On the small VPS disk these piled up until a build failed with "no space left on device" (19.10.2026).
# Only dangling images are removed (never tagged or running ones, so no re-pulls from Docker Hub) and the cache is
# capped, not wiped, so rebuilds stay fast. A cleanup failure must not turn a good deploy into a failed one.
docker image prune -f >/dev/null || echo "warning: docker image prune failed" >&2
docker builder prune -f --keep-storage "${DCLH_BUILD_CACHE_KEEP:-2GB}" >/dev/null || echo "warning: docker builder prune failed" >&2
echo "disk after cleanup: $(df -h / | awk 'NR==2 {print $4 " free (" $5 " used)"}')"

echo
echo "deployed $(git log -1 --format='%h %s (%cd)' --date=short)"
