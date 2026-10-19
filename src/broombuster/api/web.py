"""HTTP plumbing: request logs (JSON lines), per-client rate limits, cache headers,
and the index page with content-versioned asset URLs."""

import hashlib
import ipaddress
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from broombuster import config
from broombuster.version import app_version

log = logging.getLogger("broombuster.http")

# ── Logging ───────────────────────────────────────────────────────────────────
_RECORD_FIELDS = ("rid", "method", "path", "status", "ms", "ip", "reason")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so journald output can be filtered with jq."""

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
               "level": record.levelname, "logger": record.name,
               "msg": record.getMessage()}
        out.update({k: getattr(record, k) for k in _RECORD_FIELDS if hasattr(record, k)})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Our request log replaces uvicorn's access log; uvicorn's own loggers keep
    # their handlers, so route them through the JSON one too.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers, lg.propagate = [], True


def client_ip(request: Request) -> str:
    """The visitor behind Cloudflare (the app only listens on localhost).

    Only Cloudflare's tunnel may reach the app, so CF-Connecting-IP is trusted,
    but only when it parses as an IP address: anything else (a spoofed or
    garbage value) falls back to the socket peer.
    """
    raw = request.headers.get("cf-connecting-ip", "")
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return request.client.host if request.client else "-"


# ── Rate limits ───────────────────────────────────────────────────────────────
# (path prefix, requests per minute per client). /address, /geocode and /locate also
# call third-party services, so they get less.
LIMITS = (("/api/address", 20), ("/api/geocode", 20), ("/api/locate", 20), ("/api/check-home", 20),
          ("/api/rules", 60), ("/api/calendar", 30), ("/api/closures", 30), ("/api/vehicle", 10),
          ("/api/around", 30), ("/api/alerts", 30),
          ("/api/check", 60), ("/api/me", 30), ("/api/cars", 30), ("/auth/logout", 30))
_WINDOW_S = 60


class RateLimiter:
    """Sliding-window counter per (client, bucket)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, bucket: str, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 10_000:  # bound memory: drop idle clients
                self._hits = {k: q for k, q in self._hits.items() if now - q[-1] < _WINDOW_S}
            q = self._hits.setdefault((client, bucket), deque())
            while q and now - q[0] > _WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


_limiter = RateLimiter()
RATE_LIMIT = config.rate_limit_enabled()  # tests turn it off


def _limit_for(path: str) -> tuple[str, int] | None:
    return next(((p, n) for p, n in LIMITS if path.startswith(p)), None)


# ── Cache headers ─────────────────────────────────────────────────────────────
# Cloudflare caches .js/.css at the edge for hours when the origin sends no
# Cache-Control, which served stale scripts against fresh HTML. So: the page and
# unversioned files always revalidate (ETag makes that a cheap 304); files
# requested with ?v=<content hash> never change and may be cached for good.
API_PATHS = ("/api", "/auth", "/health")


def cache_control(path: str, versioned: bool) -> str:
    if path.startswith(API_PATHS):
        return "no-store"
    if versioned:
        return "public, max-age=31536000, immutable"
    if path.startswith("/tiles/"):
        return "public, max-age=3600, must-revalidate"
    return "no-cache"


# ── Versioned index page ──────────────────────────────────────────────────────
_FRONTEND = Path(config.REPO_ROOT) / "frontend"
_ASSET = re.compile(r"""(["'])/?((?:js|vendor)/[\w.\-]+\.(?:js|mjs|css)|styles\.css)\1""")
_index_html: str | None = None
_sw_js: str | None = None


def _asset_version() -> str:
    """Hash of every script and stylesheet: changes exactly when one does."""
    h = hashlib.sha256()
    for f in sorted([*_FRONTEND.glob("js/*.js"), *_FRONTEND.glob("vendor/*"),
                     _FRONTEND / "styles.css"]):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def index_response() -> HTMLResponse:
    global _index_html
    if _index_html is None:
        v = _asset_version()
        html = (_FRONTEND / "index.html").read_text()
        html = _ASSET.sub(lambda m: f"{m[1]}/{m[2]}?v={v}{m[1]}", html)
        _index_html = (html.replace("__BASE_URL__", config.base_url())
                       .replace("__APP_VERSION__", app_version()))
    return HTMLResponse(_index_html, headers={"Cache-Control": "no-cache"})


def service_worker_response() -> Response:
    """sw.js with the asset hash as its cache name (so every asset change installs a
    new worker) and the server's API path list."""
    global _sw_js
    if _sw_js is None:
        shell = ["/", "/styles.css", "/manifest.json", "/icon-192.png", "/icon-512.png",
                 "/apple-touch-icon.png", "/favicon-32.png", "/icon.svg", "/icon-maskable-512.png",
                 *(f"/{f.relative_to(_FRONTEND)}" for f in sorted(
                     [*_FRONTEND.glob("js/*.js"), *_FRONTEND.glob("vendor/*.*js"),
                      *_FRONTEND.glob("vendor/*.css")]))]
        _sw_js = ((_FRONTEND / "sw.js").read_text()
                  .replace("__VERSION__", _asset_version())
                  .replace("__API_PATHS__", json.dumps(API_PATHS))
                  .replace("__SHELL__", json.dumps(shell)))
    return Response(_sw_js, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# ── Middleware ────────────────────────────────────────────────────────────────
# Report-only until BB_CSP=enforce: the map's third-party requests are listed
# from reading the code, so check the browser console for violations first.
# No inline or remote scripts. Inline style attributes are allowed (the UI sets
# colours that way). OpenFreeMap serves the basemap style, tiles, glyphs and
# sprites; address searches go through our own /api/geocode.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https://tiles.openfreemap.org",
    "connect-src 'self' https://tiles.openfreemap.org",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
))
SECURITY_HEADERS = {
    "X-Content-Type-Options":  "nosniff",
    "Referrer-Policy":         "strict-origin-when-cross-origin",
    "X-Frame-Options":         "DENY",
}
CSP_ENFORCE = config.csp_enforced()


async def http_middleware(request: Request, call_next):
    rid, ip, t0 = uuid.uuid4().hex[:12], client_ip(request), time.perf_counter()
    path = request.url.path
    shown = "/api/calendar/…" if path.startswith("/api/calendar/") else path  # a feed's secret
    rule = _limit_for(path) if request.method != "OPTIONS" else None
    if rule and RATE_LIMIT and not _limiter.allow(ip, rule[0], rule[1]):
        response = JSONResponse({"detail": "Too many requests."}, status_code=429,
                                headers={"Retry-After": str(_WINDOW_S)})
    else:
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"rid": rid, "method": request.method,
                                                    "path": shown, "ip": ip})
            response = JSONResponse({"detail": "Internal error."}, status_code=500)
    response.headers.update(SECURITY_HEADERS)
    response.headers["Content-Security-Policy" if CSP_ENFORCE
                     else "Content-Security-Policy-Report-Only"] = CSP
    response.headers["X-Request-ID"] = rid
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = cache_control(path, "v" in request.query_params)
    extra = {"rid": rid, "method": request.method, "path": shown, "status": response.status_code,
             "ms": round((time.perf_counter() - t0) * 1000), "ip": ip}
    if reason := getattr(request.state, "log_reason", None):  # why a request was refused
        extra["reason"] = reason
    log.info("request", extra=extra)
    return response
