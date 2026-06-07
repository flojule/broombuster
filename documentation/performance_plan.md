# BroomBuster — Performance Notes

A snapshot of what makes the map feel fast today and what's still on the
table for the next round.

---

## Shipped

### 1. MapLibre + GeoJSON over the wire
The map engine is MapLibre GL JS; `/check` returns a GeoJSON
`FeatureCollection` rather than a Plotly figure. Vector tiles render
sharply at any zoom and the response payload no longer carries densified
hover coordinates.

### 2. Single-bbox `/check` per pan, with a viewport-keyed cache
On `moveend`, the frontend posts one request with
`bbox: [minLat, minLon, maxLat, maxLon]`. The bbox span (rounded to ~110 m)
is the cache key, so adjacent fine pans hit the same entry in
[`viewportCache`](../frontend/index.html). LRU max 64 entries, 10 minute TTL.

### 3. AbortController on rapid pans
Each new fetch cancels the previous in-flight one. Out-of-order resolves
can no longer overwrite the latest viewport.

### 4. IndexedDB persistence
`viewportCache` hydrates from IndexedDB on map load and writes back on
each new response, so revisits within the 10 minute TTL render instantly
without hitting the network.

### 5. Initial-viewport prefetch
[`loadAreaMap()`](../frontend/index.html) calls `fetchViewport()` directly
after `jumpTo`, so the first frame doesn't wait for the moveend debounce.

### 6. Spatial-index clip on the server
`/check` uses
[`gdf.sindex.query(clip_geom, predicate="intersects")`](../src/broombuster/api/app.py)
instead of a full `.intersects()` scan. Indices are sorted to preserve
DataFrame row order (the GeoJSON segment dedup depends on it).

### 7. Zoom-adaptive `simplify()` in the GeoJSON builder
[`build_map_geojson()`](../src/broombuster/maps.py) accepts a
`simplify_tolerance` derived from `bbox_span_deg / 2000` (≈ sub-pixel for a
1000 px viewport). Cuts wide-zoom Chicago payloads several × in size with
no visible difference. `full_region` mode skips simplification because
`/check` consumers (and tests) treat full-region features as a superset of
narrower bbox responses.

### 8. Atomic city hot-swap
[`_hot_swap_city`](../src/broombuster/api/app.py) builds new EPSG:4326 +
EPSG:3857 frames before taking `_swap_lock`, then assigns under the lock.
`_get_region_gdfs` reads under the same lock so a request straddling a
freshness refresh cannot see one CRS post-swap and the other pre-swap.

### 9. Single-pass GeoJSON build
[`build_map_geojson()`](../src/broombuster/maps.py) iterates the clipped
GDF once, branching on geometry type. The previous
`_row_color` pre-pass and separate polygon/line passes are gone.

### 10. GZip middleware
[`GZipMiddleware`](../src/broombuster/api/app.py) compresses responses over
1 KB; the verbose `/check` GeoJSON gzips ~5-8x (interim SF win before tiles).

### 11. Style-ready render gate
[`whenStyleReady()`](../frontend/js/app.js) replaces a one-shot
`map.once('style.load')` that could miss, leaving Chicago zones unpainted
until a pan. Now gates on `styledata` + `isStyleLoaded()`.

### 12. PMTiles vector tiles (item D) — DEFAULT
Renders zones from static per-region
[`frontend/tiles/*.pmtiles`](../frontend/tiles) built by
[`build_pmtiles.py`](../scripts/build_pmtiles.py); `/check` drops `geojson`.
Urgency colour is computed client-side in
[`urgency.js`](../frontend/js/urgency.js) (JS port of `analysis`, parity-tested)
and pushed via `feature-state`. Tiles are date-independent; `_hot_swap_city`
triggers a rebuild on data refresh. On by default; `PMTILES_MODE=0` restores the
legacy GeoJSON path (the test suite forces it off via `tests/conftest.py`).
Deploy note: the build step must run `scripts/build_pmtiles.py` so the archives
exist, and the host needs `tippecanoe` for the auto-refresh rebuild.

---

## On deck

Ordered by impact-per-effort. Feature-level remaining work (new
domains, frontend modularisation, city manifests) lives in
[`feature_plan.md`](feature_plan.md) — this file is map-speed only.

### A — Server-side bbox/clip cache (LRU)

The combined GDF and the analysis name-index are cached, but the
per-bbox clip + GeoJSON build is recomputed on every request, even when
the same bbox is asked for repeatedly (popular neighbourhoods, the
user's home area, repeat tab opens).

Wrap the clip + `build_map_geojson` step in an LRU keyed on
`(region, rounded_bbox, day_of_year, simplify_tolerance)`. ~64 entries
is enough for the prototype; eviction is FIFO. Day-of-year prevents
stale urgency colours after midnight. `_hot_swap_city` invalidates the
cache for that region.

Files: [`src/broombuster/api/app.py`](../src/broombuster/api/app.py).
No client changes.

### B — Service-Worker `/check` cache

[`sw.js`](../frontend/sw.js) explicitly skips `/check` today. A
stale-while-revalidate cache lets the SW return the previous response
immediately while the network fetch updates the cache for next time.
Requires moving bbox into the URL (`POST /check?bbox=…&region=…&day=…`)
so the SW can match by request URL. The day-stamp invalidates daily.

Files: [`frontend/sw.js`](../frontend/sw.js),
[`frontend/index.html`](../frontend/index.html),
[`src/broombuster/api/app.py`](../src/broombuster/api/app.py)
(accept bbox via query as well as body).

### C — `moveend` debounce 200 → 120 ms

With AbortController in place, debouncing only protects against the
animation-frame burst from MapLibre's inertia scroll. 200 ms is
conservative; 120 ms still covers the burst and feels noticeably
snappier on slow pans.

Files: [`frontend/index.html`](../frontend/index.html) (`moveend`
handler in `attachMapListeners`).

### D — PMTiles vector tiles — SHIPPED (see Shipped #12)

Behind `PMTILES_MODE`. Urgency uses `feature-state` set from a JS port of
`analysis` rather than a pure style expression, because sweep-code expansion
and the time-of-day window cannot be expressed in MapLibre expressions.

### E — Persistent compute / FGB mmap across restarts

Self-hosted Docker doesn't have Render's spin-down, but every
container restart still re-loads each city's `.fgb` from disk into a
`geopandas` GeoDataFrame (~3-8 s per city). Streaming reads with
`pyogrio` plus a cached merged-region bundle keyed by source FGB
mtime would shave that to under 1 s per restart.

Files:
[`src/broombuster/data_loader.py`](../src/broombuster/data_loader.py),
[`src/broombuster/api/app.py`](../src/broombuster/api/app.py).

### F — Drop server-rendered `hover_html` (depends on D)

Once PMTiles ships, schedule fields are already in tile properties; a
JS-side hover renderer composes the same string and saves ~30% of the
response on line cities.

Files:
[`src/broombuster/maps.py`](../src/broombuster/maps.py),
[`frontend/index.html`](../frontend/index.html).

---

| # | Item | Effort | Wins |
|---|---|---|---|
| A | Server-side bbox cache | half day | 200–400 ms repeat-pan, no client work |
| B | SW `/check` cache | day | Instant cold loads on repeat visits |
| C | Debounce 120 ms | 5 min | Subjective snappiness |
| D | PMTiles | 3–5 days | Removes payload as a constraint at scale |
| E | FGB mmap | 2 days | Sub-1 s container restart |
| F | Drop `hover_html` | half day | 30% wire reduction (after D) |
