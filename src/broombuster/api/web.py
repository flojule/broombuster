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

import brotli
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders, QueryParams
from starlette.middleware.gzip import GZipMiddleware

from broombuster import config
from broombuster.cities import REGIONS
from broombuster.version import app_version

log = logging.getLogger("broombuster.http")

# ── Logging ───────────────────────────────────────────────────────────────────
_RECORD_FIELDS = ("rid", "method", "path", "status", "ms", "reason")


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
    if version is not None and version == _current_version(path):
        return _IMMUTABLE
    return "no-cache"


_LAYER = re.compile(r"/layers/[\w-]+/[\w-]+\.geojson")


def _current_version(path: str) -> str | None:
    """The ?v= a file is requested with while it is current: a map layer's own content hash
    (layers.catalog), every other file's the assets' (index_response)."""
    return _layer_version(path) if _LAYER.fullmatch(path) else asset_version()


@functools.cache
def _layer_version(path: str) -> str | None:
    f = _FRONTEND / path.lstrip("/")
    return hashlib.sha256(f.read_bytes()).hexdigest()[:12] if f.is_file() else None


@functools.cache
def tiles_version(region_key: str) -> str | None:
    """Content hash of frontend/tiles/<region>.pmtiles (None when there is none)."""
    path = _FRONTEND / "tiles" / f"{region_key}.pmtiles"
    if not path.is_file():
        return None
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()[:12]


# ── Compression ───────────────────────────────────────────────────────────────
# Static text files are compressed once, at their best (brotli 11, gzip 9), in a background
# thread at startup; until a file's turn, it is gzipped on first request. Bodies the app
# builds once (the page, sw.js, /api/cities, the closures) keep their compressed forms by
# ETag. The rest of /api and /auth goes through GZipMiddleware (ApiGZip).
_COMPRESS_SUFFIXES = (".js", ".mjs", ".css", ".html", ".json", ".geojson", ".svg", ".txt")
_COMPRESS_MIN_BYTES = 1024
# file path -> ((size, mtime_ns), {encoding: bytes})
_static_encoded: dict[str, tuple[tuple[int, int], dict[str, bytes]]] = {}
_body_encoded: dict[str, dict[str, bytes]] = {}  # ETag of a built body -> {encoding: bytes}


def _encoding(accept: str) -> str | None:
    """The best encoding the client accepts: br, else gzip, else None (identity)."""
    ok = set()
    for part in accept.lower().split(","):
        name, _, q = part.partition(";")
        if q.strip().replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000"):
            ok.add(name.strip())
    return next((e for e in ("br", "gzip") if e in ok), None)


def _compress(data: bytes, encoding: str, best: bool = True) -> bytes:
    if encoding == "br":
        return brotli.compress(data, quality=11 if best else 5)
    return gzip.compress(data, compresslevel=9 if best else 6, mtime=0)


def _stat_key(st) -> tuple[int, int]:
    return st.st_size, st.st_mtime_ns


def _compress_file(path: Path) -> None:
    st = path.stat()
    data = path.read_bytes()
    _static_encoded[str(path)] = (_stat_key(st), {e: _compress(data, e) for e in ("br", "gzip")})


def precompress_static() -> threading.Thread:
    """Compress every static text file of frontend/ in a background thread (it takes a few
    seconds; requests meanwhile get gzip made on demand)."""

    def run() -> None:
        for path in sorted(_FRONTEND.rglob("*")):
            if (
                path.suffix in _COMPRESS_SUFFIXES
                and path.is_file()
                and path.stat().st_size >= _COMPRESS_MIN_BYTES
            ):
                try:
                    _compress_file(path)
                except OSError:
                    log.exception("could not precompress %s", path)

    t = threading.Thread(target=run, name="precompress", daemon=True)
    t.start()
    return t


def _static_body(path: str, st, encoding: str) -> bytes | None:
    """The stored `encoding` of a file, if it is current; gzip is made (and kept) on demand."""
    hit = _static_encoded.get(path)
    if hit is not None and hit[0] == _stat_key(st) and encoding in hit[1]:
        return hit[1][encoding]
    if encoding != "gzip":
        return None
    data = _compress(Path(path).read_bytes(), "gzip", best=False)  # fast; the thread redoes it
    _static_encoded[path] = (_stat_key(st), {"gzip": data})
    return data


class Static(StaticFiles):
    """StaticFiles that answers a full GET of a text file with its stored brotli or gzip bytes
    (weak ETag, same validators); ranges, 304s and other files are StaticFiles' own."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if not (
            isinstance(response, FileResponse)
            and response.status_code == 200
            and str(response.path).endswith(_COMPRESS_SUFFIXES)
            and response.stat_result.st_size >= _COMPRESS_MIN_BYTES
        ):
            return response
        response.headers["vary"] = "Accept-Encoding"
        h = Headers(scope=scope)
        encoding = _encoding(h.get("accept-encoding", ""))
        if "range" in h or encoding is None:
            return response
        st, file = response.stat_result, str(response.path)
        body = await run_in_threadpool(_static_body, file, st, encoding)
        if body is None and encoding == "br" and "gzip" in h.get("accept-encoding", ""):
            encoding = "gzip"  # brotli not made yet
            body = await run_in_threadpool(_static_body, file, st, encoding)
        if body is None:
            return response
        headers = {k: v for k, v in response.headers.items() if k != "content-length"}
        headers.update({"content-encoding": encoding, "etag": "W/" + response.headers["etag"]})
        return Response(body, headers=headers)


def body_response(
    request: Request,
    body: bytes,
    media_type: str,
    cache_control: str = "no-cache",
    etag: str | None = None,
) -> Response:
    """A body the app built once (or once per refresh), with an ETag (304 when the client
    has it) and its brotli or gzip form, compressed once per ETag."""
    etag = etag or 'W/"' + hashlib.sha256(body).hexdigest()[:20] + '"'
    headers = {"ETag": etag, "Cache-Control": cache_control, "Vary": "Accept-Encoding"}
    known = request.headers.get("if-none-match", "")
    if etag.removeprefix("W/") in {t.strip().removeprefix("W/") for t in known.split(",")}:
        return Response(status_code=304, headers=headers)
    encoding = _encoding(request.headers.get("accept-encoding", ""))
    if encoding and len(body) >= _COMPRESS_MIN_BYTES:
        forms = _body_encoded.get(etag)
        if forms is None:
            if len(_body_encoded) >= 64:  # old closures snapshots
                _body_encoded.clear()
            forms = _body_encoded[etag] = {}
        if encoding not in forms:
            forms[encoding] = _compress(body, encoding, best=len(body) < 256 * 1024)
        body = forms[encoding]
        headers["Content-Encoding"] = encoding
    return Response(body, media_type=media_type, headers=headers)


class ApiGZip:
    """GZipMiddleware for /api and /auth only: static files are served precompressed, and
    tiles and images are not worth it."""

    def __init__(self, app) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024, compresslevel=4)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(("/api/", "/auth/")):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


# ── Versioned index page ──────────────────────────────────────────────────────
_FRONTEND = Path(config.REPO_ROOT) / "frontend"
_ASSET = re.compile(r"""(["'])/?((?:js|vendor)/[\w.\-]+\.(?:js|mjs|css)|styles\.css)\1""")
_index_html: bytes | None = None
_sw_js: bytes | None = None
_module_js: bytes | None = None


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


@functools.cache
def asset_version() -> str:
    """_asset_version of this process (a deploy restarts it): the page's ?v=."""
    return _asset_version()


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


def index_response(request: Request) -> Response:
    global _index_html
    if _index_html is None:
        v = asset_version()
        html = (_FRONTEND / "index.html").read_text()
        html = _ASSET.sub(lambda m: f"{m[1]}/{m[2]}?v={v}{m[1]}", html)
        _index_html = (
            html.replace("__BASE_URL__", config.base_url())
            .replace("__APP_VERSION__", app_version())
            .encode()
        )
    return body_response(request, _index_html, "text/html; charset=utf-8")


def module_response(request: Request) -> Response:
    """js/maplibre-global.js with its imports versioned like the page's assets, so the
    map library is cached for good instead of revalidated at every launch."""
    global _module_js
    if _module_js is None:
        v = asset_version()
        src = (_FRONTEND / "js" / "maplibre-global.js").read_text()
        _module_js = _ASSET.sub(lambda m: f"{m[1]}/{m[2]}?v={v}{m[1]}", src).encode()
    return body_response(request, _module_js, "text/javascript; charset=utf-8")


def service_worker_response(request: Request) -> Response:
    """sw.js with the shell hash as its cache name (so every change to a cached file
    installs a new worker) and the server's API path list."""
    global _sw_js
    if _sw_js is None:
        v = asset_version()
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
            .encode()
        )
    return body_response(request, _sw_js, "text/javascript; charset=utf-8")


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
_CSP_HEADER = "Content-Security-Policy" if CSP_ENFORCE else "Content-Security-Policy-Report-Only"


class RequestMiddleware:
    """ASGI middleware: rate limits, security and cache headers, and one log line per request
    (a plain ASGI wrapper: no per-request task or body streaming as with BaseHTTPMiddleware)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        rid, ip, t0 = uuid.uuid4().hex[:12], client_ip(request), time.perf_counter()
        path, method = scope["path"], scope["method"]
        shown = "/api/calendar/…" if path.startswith("/api/calendar/") else path  # a secret
        status: list[int] = []  # the response's, once started

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                status.append(message["status"])
                h = MutableHeaders(scope=message)
                h.update(SECURITY_HEADERS)
                if config.base_url().startswith("https://"):
                    h["Strict-Transport-Security"] = "max-age=31536000"
                h[_CSP_HEADER] = CSP
                h["X-Request-ID"] = rid
                if "cache-control" not in h:
                    h["Cache-Control"] = cache_control(
                        path, QueryParams(scope["query_string"]).get("v")
                    )
            await send(message)

        rule = _limit_for(path) if method != "OPTIONS" else None
        try:
            if rule and RATE_LIMIT and not _limiter.allow(client_key(ip), rule[0], rule[1]):
                refused = refusal(path, 429, "Too many requests.", {"Retry-After": str(_WINDOW_S)})
                await refused(scope, receive, send_with_headers)
            else:
                await self.app(scope, receive, send_with_headers)
        except Exception:
            log.exception("unhandled error", extra={"rid": rid, "method": method, "path": shown})
            if status:  # the response has begun: nothing else can be sent
                raise
            error = JSONResponse({"detail": "Internal error."}, status_code=500)
            await error(scope, receive, send_with_headers)
        extra = {
            "rid": rid,
            "method": method,
            "path": shown,
            "status": status[0] if status else None,
            "ms": round((time.perf_counter() - t0) * 1000),
        }
        if reason := getattr(request.state, "log_reason", None):  # why a request was refused
            extra["reason"] = reason
        log.info("request", extra=extra)
