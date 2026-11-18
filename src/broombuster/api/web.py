"""HTTP plumbing: request logs (JSON lines), per-client rate limits, cache headers,
precompressed static files, and the index page with content-versioned asset URLs."""

import functools
import gzip
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers

from broombuster import config
from broombuster.cities import REGIONS
from broombuster.version import app_version

log = logging.getLogger("broombuster.http")

# ── Logging ───────────────────────────────────────────────────────────────────
_RECORD_FIELDS = ("rid", "method", "path", "status", "ms", "ip", "reason")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so journald output can be filtered with jq."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
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

    The tunnel connects from loopback, so CF-Connecting-IP is trusted only from a
    loopback peer, and only when it parses as an IP address: anything else (a
    spoofed or garbage value) falls back to the socket peer.
    """
    peer = request.client.host if request.client else "-"
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return peer
    except ValueError:
        return peer
    raw = request.headers.get("cf-connecting-ip", "")
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return peer


def client_key(ip: str) -> str:
    """The client a limit counts against: an IPv4 address, or an IPv6 address's /64
    (one home or phone holds a whole /64)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        return ip
    if addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


# ── Rate limits ───────────────────────────────────────────────────────────────
# (path prefix, requests per minute per client). /address, /geocode and /locate also
# call third-party services, so they get less.
LIMITS = (
    ("/api/next-sweep", 30),
    ("/api/address", 20),
    ("/api/geocode", 20),
    ("/api/locate", 20),
    ("/api/check-home", 20),
    ("/api/rules", 60),
    ("/api/calendar", 30),
    ("/api/closures", 30),
    ("/api/vehicle", 10),
    ("/api/alerts", 30),
    ("/api/check", 60),
    ("/api/me", 30),
    ("/api/cars", 30),
    ("/auth/passkey/register", 5),  # each success adds an account
    ("/auth/", 30),
)
_WINDOW_S = 60


class RateLimiter:
    """Sliding-window counter per (client, bucket), over `window_s` seconds."""

    def __init__(self, window_s: float = _WINDOW_S) -> None:
        self._window = window_s
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._pruned = time.monotonic()

    def allow(self, client: str, bucket: str, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            # Bound memory: drop idle clients, at most once per window.
            if len(self._hits) > 10_000 and now - self._pruned > self._window:
                self._hits = {k: q for k, q in self._hits.items() if now - q[-1] < self._window}
                self._pruned = now
            q = self._hits.setdefault((client, bucket), deque())
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


_limiter = RateLimiter()
RATE_LIMIT = config.rate_limit_enabled()  # tests turn it off


def _limit_for(path: str) -> tuple[str, int] | None:
    return next(((p, n) for p, n in LIMITS if path.startswith(p)), None)


PHONE_PATH = "/api/cars/location"  # a phone's report: every refusal carries a `summary`


def refusal(path: str, status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    """An error body; on the phone path also its `summary`, which the phone shows."""
    body = {"detail": detail, **({"summary": detail} if path == PHONE_PATH else {})}
    return JSONResponse(body, status, headers=headers)


# ── Request size ──────────────────────────────────────────────────────────────
MAX_BODY_BYTES = 64 * 1024  # the largest body is a passkey credential or the saved prefs


class BodyLimit:
    """ASGI middleware: refuses a request declaring a body over MAX_BODY_BYTES (413) or
    sending one chunked, without a length (411)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            h = dict(scope["headers"])
            if b"transfer-encoding" in h:
                refused = (411, "Send a Content-Length.")
            elif int(h.get(b"content-length", b"0") or 0) > MAX_BODY_BYTES:
                refused = (413, "Request too large.")
            else:
                refused = None
            if refused:
                await refusal(scope["path"], *refused)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ── Cache headers ─────────────────────────────────────────────────────────────
# Cloudflare caches .js/.css at the edge for hours when the origin sends no
# Cache-Control, which served stale scripts against fresh HTML. So: the page and
# unversioned files always revalidate (ETag makes that a cheap 304); files
# requested with ?v=<content hash> never change and may be cached for good.
API_PATHS = ("/api", "/auth", "/health")


_IMMUTABLE = "public, max-age=31536000, immutable"
_TILES = re.compile(r"/tiles/([\w-]+)\.pmtiles")
_LAYER_TILES = re.compile(r"/tiles/[\w-]+/[\w-]+\.([0-9a-f]{12})\.pmtiles")  # named by GeoJSON hash


def cache_control(path: str, version: str | None) -> str:
    """version: the request's ?v=; a tile set is immutable only under its own tiles_version, a
    layer archive only under the GeoJSON hash in its name."""
    if path.startswith(API_PATHS):
        return "no-store"
    if path.startswith("/tiles/"):
        m = _TILES.fullmatch(path)
        if version and m and m[1] in REGIONS and version == tiles_version(m[1]):
            return _IMMUTABLE
        layer = _LAYER_TILES.fullmatch(path)
        if version and layer and version == layer[1]:
            return _IMMUTABLE
        return "public, max-age=3600, must-revalidate"
    if version is not None:
        return _IMMUTABLE
    return "no-cache"


@functools.cache
def tiles_version(region_key: str) -> str | None:
    """Content hash of frontend/tiles/<region>.pmtiles (None when there is none)."""
    path = _FRONTEND / "tiles" / f"{region_key}.pmtiles"
    if not path.is_file():
        return None
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()[:12]


# ── Static files ──────────────────────────────────────────────────────────────
# Text files are gzipped once (level 9) and kept in memory; GZipMiddleware skips them.
_GZIP_SUFFIXES = (".js", ".mjs", ".css", ".html", ".json", ".geojson", ".svg", ".txt")
_GZIP_MIN_BYTES = 1024
_gzipped: dict[str, tuple[str, bytes]] = {}  # file path -> (its ETag, gzip bytes)


def _gzip_file(path: str, etag: str) -> bytes:
    hit = _gzipped.get(path)
    if hit is None or hit[0] != etag:
        data = gzip.compress(Path(path).read_bytes(), compresslevel=9, mtime=0)
        hit = _gzipped[path] = (etag, data)
    return hit[1]


class Static(StaticFiles):
    """StaticFiles that answers a full GET of a text file with its stored gzip bytes (weak
    ETag, same validators); ranges, 304s and other files are StaticFiles' own."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if not (
            isinstance(response, FileResponse)
            and response.status_code == 200
            and str(response.path).endswith(_GZIP_SUFFIXES)
            and response.stat_result.st_size >= _GZIP_MIN_BYTES
        ):
            return response
        response.headers["vary"] = "Accept-Encoding"
        h = Headers(scope=scope)
        if "range" in h or "gzip" not in h.get("accept-encoding", ""):
            return response
        etag = response.headers["etag"]
        body = await run_in_threadpool(_gzip_file, str(response.path), etag)
        headers = {k: v for k, v in response.headers.items() if k != "content-length"}
        headers.update({"content-encoding": "gzip", "etag": "W/" + etag})
        return Response(body, headers=headers)


# ── Versioned index page ──────────────────────────────────────────────────────
_FRONTEND = Path(config.REPO_ROOT) / "frontend"
_ASSET = re.compile(r"""(["'])/?((?:js|vendor)/[\w.\-]+\.(?:js|mjs|css)|styles\.css)\1""")
_index_html: str | None = None
_sw_js: str | None = None
_module_js: str | None = None


def _files_hash(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(str(f.relative_to(_FRONTEND)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _assets() -> list[Path]:
    return [*_FRONTEND.glob("js/*.js"), *_FRONTEND.glob("vendor/*"), _FRONTEND / "styles.css"]


def _asset_version() -> str:
    """Hash of every script and stylesheet: changes exactly when one does."""
    return _files_hash(_assets())


# The app shell's files that are cached offline under their plain URL.
_SHELL_FILES = (
    "manifest.json",
    "icon-192.png",
    "icon-512.png",
    "apple-touch-icon.png",
    "favicon-32.png",
    "icon.svg",
    "icon-maskable-512.png",
)


def _shell_version() -> str:
    """Hash of the assets, index.html and the shell files: the offline cache's name."""
    return _files_hash(
        [*_assets(), _FRONTEND / "index.html", *(_FRONTEND / f for f in _SHELL_FILES)]
    )


def index_response() -> HTMLResponse:
    global _index_html
    if _index_html is None:
        v = _asset_version()
        html = (_FRONTEND / "index.html").read_text()
        html = _ASSET.sub(lambda m: f"{m[1]}/{m[2]}?v={v}{m[1]}", html)
        _index_html = html.replace("__BASE_URL__", config.base_url()).replace(
            "__APP_VERSION__", app_version()
        )
    return HTMLResponse(_index_html, headers={"Cache-Control": "no-cache"})


def module_response() -> Response:
    """js/maplibre-global.js with its imports versioned like the page's assets, so the
    map library is cached for good instead of revalidated at every launch."""
    global _module_js
    if _module_js is None:
        v = _asset_version()
        src = (_FRONTEND / "js" / "maplibre-global.js").read_text()
        _module_js = _ASSET.sub(lambda m: f"{m[1]}/{m[2]}?v={v}{m[1]}", src)
    return Response(_module_js, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


def service_worker_response() -> Response:
    """sw.js with the shell hash as its cache name (so every change to a cached file
    installs a new worker) and the server's API path list."""
    global _sw_js
    if _sw_js is None:
        v = _asset_version()
        shell = [
            "/",
            f"/styles.css?v={v}",
            *(f"/{f}" for f in _SHELL_FILES),
            *(  # as the page asks for them (index_response), so each downloads once
                f"/{f.relative_to(_FRONTEND)}?v={v}"
                for f in sorted(
                    [
                        *_FRONTEND.glob("js/*.js"),
                        *_FRONTEND.glob("vendor/*.*js"),
                        *_FRONTEND.glob("vendor/*.css"),
                    ]
                )
            ),
        ]
        _sw_js = (
            (_FRONTEND / "sw.js")
            .read_text()
            .replace("__VERSION__", _shell_version())
            .replace("__API_PATHS__", json.dumps(API_PATHS))
            .replace("__SHELL__", json.dumps(shell))
        )
    return Response(_sw_js, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# ── Middleware ────────────────────────────────────────────────────────────────
# Enforced; BB_CSP=report-only turns it into a console-only report for debugging.
# No inline or remote scripts. Inline style attributes are allowed (the UI sets
# colors that way). OpenFreeMap serves the basemap style, tiles, glyphs and
# sprites; address searches go through our own /api/geocode.
CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https://tiles.openfreemap.org",
        "connect-src 'self' https://tiles.openfreemap.org",
        "worker-src 'self' blob:",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    )
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}
CSP_ENFORCE = config.csp_enforced()


async def http_middleware(request: Request, call_next):
    rid, ip, t0 = uuid.uuid4().hex[:12], client_ip(request), time.perf_counter()
    path = request.url.path
    shown = "/api/calendar/…" if path.startswith("/api/calendar/") else path  # a feed's secret
    rule = _limit_for(path) if request.method != "OPTIONS" else None
    if rule and RATE_LIMIT and not _limiter.allow(client_key(ip), rule[0], rule[1]):
        response = refusal(path, 429, "Too many requests.", {"Retry-After": str(_WINDOW_S)})
    else:
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "unhandled error",
                extra={"rid": rid, "method": request.method, "path": shown, "ip": ip},
            )
            response = JSONResponse({"detail": "Internal error."}, status_code=500)
    response.headers.update(SECURITY_HEADERS)
    if config.base_url().startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers[
        "Content-Security-Policy" if CSP_ENFORCE else "Content-Security-Policy-Report-Only"
    ] = CSP
    response.headers["X-Request-ID"] = rid
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = cache_control(path, request.query_params.get("v"))
    extra = {
        "rid": rid,
        "method": request.method,
        "path": shown,
        "status": response.status_code,
        "ms": round((time.perf_counter() - t0) * 1000),
        "ip": ip,
    }
    if reason := getattr(request.state, "log_reason", None):  # why a request was refused
        extra["reason"] = reason
    log.info("request", extra=extra)
    return response
