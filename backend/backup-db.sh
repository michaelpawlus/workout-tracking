#!/bin/bash
# Snapshot the training database.
#
# Why this exists: the entire 20-week BR100 record — plan, runs, feedback, adaptive
# targets, benchmark results — was lost in the macOS migration because workouts.db
# lived on exactly one machine with no copy anywhere. The Obsidian vault survived.
# So backups go into the vault by default, riding along with whatever keeps it safe.
#
# Override the destination with WORKOUT_DB_BACKUP_DIR, retention with
# WORKOUT_DB_BACKUP_KEEP (default 30 snapshots).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DB="backend/workouts.db"
[ -f "$DB" ] || { echo "no database at $DB — nothing to back up" >&2; exit 1; }

# .env carries OBSIDIAN_VAULT_PATH on some setups; the shell env carries it on others.
if [ -f backend/.env ]; then set -a; source backend/.env; set +a; fi

if [ -n "${WORKOUT_DB_BACKUP_DIR:-}" ]; then
  DEST="$WORKOUT_DB_BACKUP_DIR"
elif [ -n "${OBSIDIAN_VAULT_PATH:-}" ]; then
  # Dot-prefixed so Obsidian ignores it rather than indexing binaries as notes.
  DEST="$OBSIDIAN_VAULT_PATH/.workout-db-backups"
else
  echo "set OBSIDIAN_VAULT_PATH or WORKOUT_DB_BACKUP_DIR" >&2; exit 1
fi

KEEP="${WORKOUT_DB_BACKUP_KEEP:-30}"
mkdir -p "$DEST"

OUT="$DEST/workouts-$(date +%Y%m%d-%H%M%S).db"

# sqlite3 .backup takes a consistent snapshot of a live database; a plain cp can
# catch a half-written page mid-transaction and produce a corrupt file.
sqlite3 "$DB" ".backup '$OUT'"
gzip -f "$OUT"

# Verify the snapshot opens and is internally consistent before trusting it. An
# untested backup is the same assumption that lost the last database.
TMP="$(mktemp -t workoutsbak)"
trap 'rm -f "$TMP"' EXIT
gzip -dc "$OUT.gz" > "$TMP"
CHECK="$(sqlite3 "$TMP" 'PRAGMA integrity_check;' 2>&1 || echo "unreadable")"
if [ "$CHECK" != "ok" ]; then
  echo "snapshot failed integrity check ($CHECK): $OUT.gz" >&2
  rm -f "$OUT.gz"
  exit 1
fi
ROWS="$(sqlite3 "$TMP" 'SELECT COUNT(*) FROM daily_workouts;' 2>/dev/null || echo 0)"

# Keep the newest N, drop the rest. `command ls` so an aliased/shadowed ls in a
# user profile cannot change the sort order and silently delete the wrong files.
command ls -1t "$DEST"/workouts-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
HELD="$(command ls -1 "$DEST"/workouts-*.db.gz 2>/dev/null | wc -l | tr -d ' ')"

echo "$(date -Iseconds)  $OUT.gz  (${ROWS} planned workouts, ${HELD} snapshots retained)"
