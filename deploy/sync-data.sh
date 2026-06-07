#!/usr/bin/env bash
# Copy runtime map data (not in git) from this Mac to the Pi.
#   ./deploy/sync-data.sh user@pi-host
# Excludes app.sqlite so the Pi keeps its own saved-car state.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:?usage: sync-data.sh user@host}"
REMOTE="ws/BroomBuster"

rsync -av --exclude app.sqlite data/        "$DEST:$REMOTE/data/"
rsync -av                      frontend/tiles/ "$DEST:$REMOTE/frontend/tiles/"
echo "Synced .fgb data + tiles to $DEST:$REMOTE"
