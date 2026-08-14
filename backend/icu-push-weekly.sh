#!/bin/bash
# Push the next 8 days of workouts to Intervals.icu (syncs to Coros).
# Points at the active race cycle — currently the Columbus Marathon block.
# When the next cycle starts, change the subgroup below (`marathon` / `ultra`).
set -euo pipefail

# Resolve the repo root from this script's own location so the job works
# regardless of the caller's cwd (launchd runs it from /).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f backend/.env ]; then
  echo "backend/.env missing — cannot load Intervals.icu credentials" >&2
  exit 1
fi

set -a
source backend/.env
set +a

uv run ultra marathon icu-push --upcoming 8
