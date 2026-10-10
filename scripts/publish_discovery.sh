#!/usr/bin/env bash
# Run source discovery and push the report to git. Meant for the VPS, from cron or by hand.
#
#   scripts/publish_discovery.sh                 # full run with network capture, pushes to main
#   BRANCH=some-branch scripts/publish_discovery.sh
#   CAPTURE=0 scripts/publish_discovery.sh       # skip the Playwright capture (faster)
#   RAN_FROM="VPS Warszawa" scripts/publish_discovery.sh  # label written into the report
#
# It never force-pushes. If the rebase or push fails, it stops and leaves the commit local.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${BRANCH:-main}"
CAPTURE="${CAPTURE:-1}"
RAN_FROM="${RAN_FROM:-unknown}"

cd "$REPO_ROOT"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked files have local changes; commit or stash them first." >&2
  exit 1
fi

git checkout -q "$BRANCH"
git pull -q --ff-only origin "$BRANCH"

if [[ "$CAPTURE" == "1" ]]; then
  uv run dclh discover --capture-network --write-docs --ran-from "$RAN_FROM"
else
  uv run dclh discover --write-docs --ran-from "$RAN_FROM"
fi

git add data/discovery docs/SOURCES.md
if git diff --cached --quiet; then
  echo "No changes in the discovery report; nothing to push."
  exit 0
fi

git commit -q -m "Discovery run $(date -u +%Y-%m-%dT%H:%MZ)"
git pull -q --rebase origin "$BRANCH"
git push -q origin "$BRANCH"
echo "Discovery report pushed to $BRANCH."
