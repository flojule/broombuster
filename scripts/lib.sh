# Shared helpers for run.sh and deploy/*.sh. Source from the repo root.

# Print deploy/<unit> with __USER__ / __REPO__ filled in for this checkout.
render_unit() {
  sed -e "s#__USER__#$(id -un)#g" -e "s#__REPO__#$PWD#g" "deploy/$1"
}

# Health: the app must bind the port AND finish loading every city (slow on a
# Pi). A failed city load fails the rollout immediately; /health alone returns
# "ok" while cities are still loading, so it isn't enough to prove the data is good.
healthy() {
  local body deadline=$((SECONDS + 180))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if body="$(curl -fsS --max-time 5 http://127.0.0.1:8000/health 2>/dev/null)"; then
      case "$body" in
        *'"failed":[]'*) ;;
        *) echo "city load failed: $body" >&2; return 1 ;;
      esac
      case "$body" in
        *'"loading":[]'*) echo "$body"; return 0 ;;
      esac
    fi
    sleep 2
  done
  echo "timed out waiting for cities to load" >&2
  return 1
}
