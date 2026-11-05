#!/usr/bin/env bash
# Dump the production database and keep the newest few dumps. Meant for the VPS, from cron or by hand.
#
#   scripts/backup_db.sh                       # dump to ~/backups, keep the newest 7 nightly dumps
#   BACKUP_DIR=/mnt/other KEEP=14 scripts/backup_db.sh
#
# The dump is pg_dump's custom format (compressed; restore with pg_restore, see docs/RUNBOOK.md). It is written
# under a temporary name and renamed when complete, so a failed run never leaves a half dump that looks good.
# Only files named dclh-nightly-*.dump are rotated; dumps made by hand under other names are never deleted.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP="${KEEP:-7}"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml")

mkdir -p "$BACKUP_DIR"
target="$BACKUP_DIR/dclh-nightly-$(date +%Y%m%d-%H%M).dump"
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

# The variables are expanded inside the db container, which has POSTGRES_USER and POSTGRES_DB set.
"${COMPOSE[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$partial"
# A dump that pg_restore cannot list is useless; check it before it replaces an older good one.
"${COMPOSE[@]}" exec -T db pg_restore --list >/dev/null <"$partial"
mv "$partial" "$target"

# The names carry the date, so name order is age order (mtime would be changed by a copy or a restore test).
ls -1 "$BACKUP_DIR"/dclh-nightly-*.dump | sort -r | tail -n +"$((KEEP + 1))" | xargs -r rm --
echo "$(date -Is) backup $(du -h "$target" | cut -f1) $target; kept $(ls -1 "$BACKUP_DIR"/dclh-nightly-*.dump | wc -l)"
