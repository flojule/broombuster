#!/usr/bin/env bash
# Checks of a running deploy, from the deployed revision: a car check per region and the app page.
set -euo pipefail
app=http://127.0.0.1:8000

# A car check at a fixed point of region $1 finds a street (tests/test_deploy.py checks the points).
street() {
  local out
  out="$(curl -fsS --max-time 10 -H 'Content-Type: application/json' \
    -d "{\"region\":\"$1\",\"lat\":$2,\"lon\":$3}" "$app/api/check")"
  [[ $out == *'"covered":true'* ]] || { echo "/api/check finds no street in $1" >&2; exit 1; }
}

street bay_area 37.8044 -122.2712
street chicago 41.9400 -87.6550

out="$(curl -fsS --max-time 10 "$app/")"
[[ $out == *'<title>BroomBuster</title>'* ]] || { echo "/ is not the app page" >&2; exit 1; }
