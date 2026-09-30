#!/usr/bin/env bash
# Refresh frontend/vendor/ from npm. Versions are pinned here (single source).
#   ./scripts/update_vendor.sh
# After running: bump CACHE in frontend/sw.js and smoke-test the map.
set -euo pipefail
cd "$(dirname "$0")/.."

MAPLIBRE_VERSION=6.11.2
PMTILES_VERSION=4.5.0

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
(cd "$tmp" && npm pack --silent "maplibre-gl@${MAPLIBRE_VERSION}" "pmtiles@${PMTILES_VERSION}")
for t in "$tmp"/*.tgz; do
  d="$tmp/$(basename "$t" .tgz)"; mkdir "$d"; tar xzf "$t" -C "$d"
done

mkdir -p frontend/vendor
ml="$tmp/maplibre-gl-${MAPLIBRE_VERSION}/package/dist"
cp "$ml"/maplibre-gl.css "$ml"/maplibre-gl.mjs "$ml"/maplibre-gl-shared.mjs \
   "$ml"/maplibre-gl-worker.mjs frontend/vendor/
cp "$tmp/pmtiles-${PMTILES_VERSION}/package/dist/pmtiles.js" frontend/vendor/
echo "vendored maplibre-gl ${MAPLIBRE_VERSION}, pmtiles ${PMTILES_VERSION}"
