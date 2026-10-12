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

echo
echo "deployed $(git log -1 --format='%h %s (%cd)' --date=short)"
