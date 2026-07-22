#!/bin/bash
# Push next 8 days of BR100 workouts to Intervals.icu (syncs to Coros)
set -euo pipefail

# Resolve the repo root from this script's own location so the job works
# regardless of the caller's cwd (launchd runs it from /).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

set -a
source backend/.env
set +a

uv run ultra ultra icu-push --upcoming 8
