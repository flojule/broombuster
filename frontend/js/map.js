// ── Map init ──────────────────────────────────────────────────────────────────
function initMap(center) {
  const c = center || DEFAULT_CENTER;
  map = new maplibregl.Map({
    container: 'map',
    style: _mapStyle,
    center: [c.lon, c.lat],
    zoom: c.zoom,
    attributionControl: false,
  });
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  map.on('load', () => {
    map.resize();
    attachMapListeners();
    // Hydrate the persisted viewport cache once. Fire-and-forget — pans
    // before hydration completes will fall through to network and the
    // result populates both caches via fetchViewport's normal path.
    hydrateViewportCache();
  });
  map.on('error', (e) => console.error('[MapLibre]', e.error));
}

// ── Viewport fetch + cache (module scope so loadAreaMap can prefetch) ────────
//
// One bbox per request — the server clips its GDF to that bbox and returns
// a complete FeatureCollection for the visible area. The cache key is the
// rounded bbox; entries persist across page reloads via IndexedDB.
let _viewportFetchTimer    = null;
let _inflightController    = null;  // AbortController for the latest /check
const VIEWPORT_CACHE_MAX   = 64;
const VIEWPORT_TTL_MS      = 10 * 60 * 1000; // 10 minutes
const viewportCache        = new Map(); // bboxKey -> { geojson, ts }

// IndexedDB persistence: viewport responses survive page reloads. Keyed by
// the same bboxKey we use in memory; TTL still 10 minutes.
const VIEWPORT_DB_NAME    = 'broombuster';
const VIEWPORT_STORE      = 'viewport_cache';
const VIEWPORT_DB_VERSION = 1;
let _viewportDb = null;

function _openViewportDb() {
  return new Promise((resolve) => {
    if (!('indexedDB' in window)) return resolve(null);
    let req;
    try { req = indexedDB.open(VIEWPORT_DB_NAME, VIEWPORT_DB_VERSION); }
    catch (_) { return resolve(null); }
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(VIEWPORT_STORE)) {
        db.createObjectStore(VIEWPORT_STORE, { keyPath: 'k' });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror   = ()  => resolve(null);
  });
}

async function hydrateViewportCache() {
  if (_viewportDb) return;
  _viewportDb = await _openViewportDb();
  if (!_viewportDb) return;
  await new Promise((resolve) => {
    let tx;
    try { tx = _viewportDb.transaction(VIEWPORT_STORE, 'readonly'); }
    catch (_) { return resolve(); }
    const req = tx.objectStore(VIEWPORT_STORE).getAll();
    req.onsuccess = (e) => {
      const now = Date.now();
      for (const r of e.target.result || []) {
        if (r && r.k && r.geojson && (now - (r.ts || 0)) < VIEWPORT_TTL_MS) {
          viewportCache.set(r.k, { geojson: r.geojson, ts: r.ts });
        }
      }
      resolve();
    };
    req.onerror = () => resolve();
  });
}

function _persistViewportEntry(key, entry) {
  if (!_viewportDb) return;
  try {
    const tx = _viewportDb.transaction(VIEWPORT_STORE, 'readwrite');
    tx.objectStore(VIEWPORT_STORE).put({ k: key, geojson: entry.geojson, ts: entry.ts });
  } catch (_) {}
}

function _bboxKey(b, region) {
  // Round to ~110 m so adjacent fine-grained pans hit the same cache entry.
  return `${region}|${b.getSouth().toFixed(3)},${b.getWest().toFixed(3)},${b.getNorth().toFixed(3)},${b.getEast().toFixed(3)}`;
}

async function fetchViewport(bounds) {
  // PMTILES mode: the map data comes from tiles, not /check. Just ensure the
  // tile source is mounted and refresh urgency for the new viewport.
  if (PMTILES_MODE) { ensureTiles(); scheduleUrgencyUpdate(); return; }
  const region = regionSelect.value || _renderedRegion;
  if (!region || !map) return;
  const key = _bboxKey(bounds, region);
  const now = Date.now();
  const cached = viewportCache.get(key);
  if (cached && (now - cached.ts) < VIEWPORT_TTL_MS) {
    renderZones(cached.geojson);
    return;
  }

  // Cancel any earlier in-flight /check — only the latest viewport matters.
  if (_inflightController) _inflightController.abort();
  const controller = new AbortController();
  _inflightController = controller;

  try {
    const res = await apiFetch('/check', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        lat:    map.getCenter().lat,
        lon:    map.getCenter().lng,
        region: regionSelect.value || undefined,
        bbox:   [bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast()],
      }),
      signal: controller.signal,
    });
    if (!res.ok) return;
    const data = await res.json();
    const geojson = data.geojson || { type: 'FeatureCollection', features: [] };

    const entry = { geojson, ts: Date.now() };
    viewportCache.set(key, entry);
    if (viewportCache.size > VIEWPORT_CACHE_MAX) {
      // Evict oldest insertion (Map preserves insertion order)
      const k = viewportCache.keys().next().value;
      viewportCache.delete(k);
    }
    _persistViewportEntry(key, entry);

    // Guard against the rare race where abort() didn't propagate before
    // a competing fetch resolved: only render if we're still the latest.
    if (controller === _inflightController) renderZones(geojson);
  } catch (e) {
    if (e && e.name === 'AbortError') return;
  }
}

let _mapListenersAttached = false;
let _tapTimer = null;  // deferred single-tap (cancelled by a double-tap zoom)

// Resolve a settled single tap: open the zone detail popup for a hit feature,
// else dismiss any open transient window. Split out of the click listener so the
// double-tap defer can call it after the timer fires.
function handleMapTap(point, lngLat) {
  const features = map.queryRenderedFeatures(point, { layers: HOVER_LAYERS.filter(l => !!map.getLayer(l)) });
  if (PMTILES_MODE) {
    // Tiles carry no detail_html; fetch the full-year popup for clicked zones.
    const poly = features.find(f => f.properties && f.properties.render_type === 'polygon');
    if (poly) { fetchZoneDetail(poly.properties, lngLat); return; }
    dismissMapWindows();
    return;
  }
  if (!features.length) { dismissMapWindows(); return; }
  const detailed = features.find(f => f.properties && f.properties.detail_html);
  if (detailed) showZoneDetail(lngLat, detailed.properties.detail_html);
  else dismissMapWindows();
}

function attachMapListeners() {
  // Event listeners live on the map instance and survive style changes, so only
  // attach once. Calling setStyle() does NOT remove them.
  if (_mapListenersAttached) return;
  _mapListenersAttached = true;

  // Hover tooltip
  map.on('mousemove', (e) => {
    if (_hoverSuppressed) return;
    const features = map.queryRenderedFeatures(e.point, { layers: HOVER_LAYERS.filter(l => !!map.getLayer(l)) });
    if (!features.length) { customHoverEl.style.display = 'none'; return; }
    const html = PMTILES_MODE
      ? tileHoverHtml(features[0].properties || {})
      : features[0].properties?.hover_html;
    if (!html) { customHoverEl.style.display = 'none'; return; }
    customHoverEl.innerHTML = html;
    customHoverEl.style.display = 'block';
    // Clamp using the box's actual width (it now sizes to its widest line).
    const w = customHoverEl.offsetWidth;
    const x = Math.min(e.originalEvent.clientX + 14, window.innerWidth - w - 10);
    const y = Math.max(e.originalEvent.clientY - 10, 10);
    customHoverEl.style.left = Math.max(x, 6) + 'px';
    customHoverEl.style.top  = y + 'px';
  });

  map.getCanvas().addEventListener('mouseleave', () => { customHoverEl.style.display = 'none'; });
  map.on('movestart', () => { customHoverEl.style.display = 'none'; });

  // Tap → show zone detail (Chicago section schedule + PDF link) when a zone is
  // hit; a tap away from a zone dismisses any open window (zone popup, GPS pin
  // popup, car selection) — same as pressing Esc. The schedule/dismiss action is
  // deferred ~280 ms so a double-tap-to-zoom can cancel it (no window flash);
  // placement commits immediately, and a tap while the name box is open cancels it.
  map.on('click', (e) => {
    // Placement commits via the center target ("Place here") or right-click, not
    // a stray tap — a tap during place mode is ignored so panning can't misfire.
    if (placingCar || placingHome) return;
    if (_namePopup) { closeNameBox(); return; }
    if (_tapTimer) clearTimeout(_tapTimer);
    const point = e.point, lngLat = e.lngLat;
    _tapTimer = setTimeout(() => { _tapTimer = null; handleMapTap(point, lngLat); }, 280);
  });
  map.on('dblclick', () => { if (_tapTimer) { clearTimeout(_tapTimer); _tapTimer = null; } });

  // GPS popup position update on move
  map.on('move', updateGpsPinPopup);
  map.on('moveend', updateGpsPinPopup);

  // Debounced viewport fetch on pan/zoom. The actual fetch lives at module
  // scope so initial loads (loadAreaMap) can call it directly.
  map.on('moveend', () => {
    if (_viewportFetchTimer) clearTimeout(_viewportFetchTimer);
    _viewportFetchTimer = setTimeout(() => {
      if (!_renderedRegion) return;
      fetchViewport(map.getBounds());
    }, 200);
  });

  // Center banner: live-read the street under the viewport center while the map
  // moves (drag/zoom), and once more after tiles finish streaming in (idle).
  map.on('move', updateCenterBanner);
  map.on('moveend', updateCenterBanner);
  map.on('idle', updateCenterBanner);

  // PMTILES: recolour newly loaded tile features once the map settles.
  if (PMTILES_MODE) {
    map.on('idle', scheduleUrgencyUpdate);
    ensureTiles();
  }
}

// ── Zone layer management ─────────────────────────────────────────────────────
function removeZoneLayers() {
  for (const id of ZONE_LAYERS) {
    if (map.getLayer(id)) map.removeLayer(id);
  }
  if (map.getSource(ZONES_SOURCE)) map.removeSource(ZONES_SOURCE);
}

function addZoneLayers(geojson) {
  if (!geojson) return;
  // If source already exists, update it in-place to avoid removing layers
  // which causes a visual flicker. Otherwise create source and layers.
  if (map.getSource && map.getSource(ZONES_SOURCE)) {
    try {
      map.getSource(ZONES_SOURCE).setData(geojson);
      return;
    } catch (e) {
      // Fall through to recreate source/layers if setData fails
      try { removeZoneLayers(); } catch (_) {}
    }
  }

  map.addSource(ZONES_SOURCE, { type: 'geojson', data: geojson });

  // Polygon fills (Chicago ward zones)
  map.addLayer({
    id: 'zones-fill', type: 'fill', source: ZONES_SOURCE,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'fill-color': ['get', 'fill_color'] },
  });

  // Polygon outlines
  map.addLayer({
    id: 'zones-outline', type: 'line', source: ZONES_SOURCE,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'line-color': ['get', 'border_color'], 'line-width': 1.5 },
  });

  // Street lines (Oakland / SF)
  map.addLayer({
    id: 'zones-line', type: 'line', source: ZONES_SOURCE,
    filter: ['==', ['get', 'render_type'], 'line'],
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
    },
    paint: {
      'line-color': ['get', 'line_color'],
      'line-width': ZONE_LINE_WIDTH,
    },
  });
}

// Run `cb` once the style can accept sources/layers. `style.load` is a one-shot
// event: if it already fired before we register, map.once() never calls back.
// isStyleLoaded() can also be briefly false AFTER style.load while the basemap
// loads sprites/tiles. Gating on a styledata listener that re-checks
// isStyleLoaded() covers both cases — the fix for Chicago zones not appearing
// until the user pans/zooms.
function whenStyleReady(cb) {
  if (!map) return;
  if (map.isStyleLoaded()) { cb(); return; }
  const onData = () => {
    if (map.isStyleLoaded()) { map.off('styledata', onData); cb(); }
  };
  map.on('styledata', onData);
}

function renderZones(geojson) {
  // In PMTILES mode the map renders from vector tiles, not per-request GeoJSON.
  if (PMTILES_MODE) { ensureTiles(); return; }
  _currentGeojson = geojson || null;
  if (!map) return;
  whenStyleReady(() => {
    if (geojson) addZoneLayers(geojson);
    else removeZoneLayers();
  });
}

// ── PMTILES vector-tile rendering ─────────────────────────────────────────────
let _tilesRegion      = null;   // region currently mounted as a tile source
let _pmtilesProtocol  = false;  // pmtiles:// protocol registered once
let _featStateCache   = new Map();  // feature id -> [urgency, y-m-d-minute it was computed]
let _featSchedCache   = new Map();  // feature id -> parsed sched entries
let _urgencyTimer     = null;

function _archiveUrl(region) {
  return 'pmtiles://' + window.location.origin + '/tiles/' + region + '.pmtiles';
}

function removeTileLayers() {
  for (const id of ZONE_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(TILES_SOURCE)) map.removeSource(TILES_SOURCE);
}

// Ward outline colour: light on the dark basemap, dark on the light basemap.
function _wardLineColor() {
  return document.body.classList.contains('dark')
    ? 'rgba(245,248,252,0.75)'
    : 'rgba(20,28,46,0.7)';
}

function addTilePaintLayers() {
  map.addLayer({
    id: 'zones-fill', type: 'fill', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'fill-color': _urgCase('fill') },
  });
  map.addLayer({
    id: 'zones-outline', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'line-color': _urgCase('border'), 'line-width': 1.5 },
  });
  // Dissolved ward outlines: a clear neutral line over the urgency fills, kept
  // distinct from (and heavier than) the per-section outlines above. Colour is
  // theme-aware (light line on the dark basemap, dark line on the light one) so
  // it stays visible in both; recomputed when the basemap switches.
  map.addLayer({
    id: 'zones-ward', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'ward_boundary'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': _wardLineColor(),
      'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.0, 13, 2.0, 16, 3.2, 18, 4.5],
    },
  });
  map.addLayer({
    id: 'zones-line', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'line'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': _urgCase('line'), 'line-width': ZONE_LINE_WIDTH },
  });
}

function ensureTiles() {
  if (!PMTILES_MODE || !map) return;
  const region = regionSelect.value || _renderedRegion;
  if (!region) return;
  whenStyleReady(() => {
    if (!_pmtilesProtocol) {
      try { maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile); } catch (_) {}
      _pmtilesProtocol = true;
    }
    if (_tilesRegion !== region) {
      removeTileLayers();
      _featStateCache.clear(); _featSchedCache.clear();
      map.addSource(TILES_SOURCE, { type: 'vector', url: _archiveUrl(region) });
      addTilePaintLayers();
      _tilesRegion = region;
    }
    scheduleUrgencyUpdate();
  });
}

function scheduleUrgencyUpdate() {
  if (!PMTILES_MODE) return;
  if (_urgencyTimer) clearTimeout(_urgencyTimer);
  _urgencyTimer = setTimeout(applyUrgencyStates, 150);
}

// Compute urgency for every in-view tile feature and push it to feature-state,
// which drives the paint expressions. A verdict is reused only within the
// region-local minute it was computed (a window may close at any minute), and
// feature-state is written only when the verdict changed.
function applyUrgencyStates() {
  if (!PMTILES_MODE || !map || !_tilesRegion || !map.getSource(TILES_SOURCE)) return;
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  const stamp = now.y + '-' + now.m + '-' + now.d + '-' + now.min;
  let feats;
  try { feats = map.querySourceFeatures(TILES_SOURCE, { sourceLayer: TILES_SRC_LAYER }); }
  catch (_) { return; }
  for (const f of feats) {
    if (f.id === undefined || f.id === null) continue;
    const prev = _featStateCache.get(f.id);
    if (prev && prev[1] === stamp) continue;
    let entries = _featSchedCache.get(f.id);
    if (entries === undefined) {
      try { entries = JSON.parse(f.properties.sched || '[]') || []; } catch (_) { entries = []; }
      _featSchedCache.set(f.id, entries);
    }
    const u = BroomUrgency.checkDaySweeping(entries, now);
    _featStateCache.set(f.id, [u, stamp]);
    if (prev && prev[0] === u) continue;
    map.setFeatureState(
      { source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id: f.id },
      { urgency: u },
    );
  }
}

// Canonical schedule lines for a tile feature (day-first, "Every <Wd>" merge,
// Mon->Sun order, merged next-cluster dates) — identical to the card and the
// server hover. Shared by the hover tooltip and the center banner.
function tileSchedLines(props) {
  let sched = [];
  try { sched = JSON.parse(props.sched || '[]'); } catch (_) {}
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  const labels = [props.side_even || 'Even', props.side_odd || 'Odd'];
  return BroomUrgency.formatBothSides(
    sched.filter(e => e.side === 'even'), sched.filter(e => e.side === 'odd'), now, null, labels);
}

function tileHoverHtml(props) {
  const lines = tileSchedLines(props);
  // No real schedule (only N/A / no-sweep) → no hover at all.
  return lines.length ? `<b>${esc(props.street || '')}</b><br>${lines.map(esc).join('<br>')}` : '';
}

async function fetchZoneDetail(props, lngLat) {
  let codes = [];
  try { codes = JSON.parse(props.sched || '[]').map(e => e.code).filter(Boolean); } catch (_) {}
  if (!codes.length) return;
  const region = regionSelect.value || _renderedRegion || '';
  const qs = new URLSearchParams({ street: props.street || '', city: props.city || '', region });
  for (const c of new Set(codes)) qs.append('code', c);
  try {
    const res = await apiFetch('/zone/detail?' + qs.toString());
    if (!res.ok) return;
    const data = await res.json();
    if (data.detail_html) showZoneDetail(lngLat, data.detail_html);
  } catch (_) {}
}
