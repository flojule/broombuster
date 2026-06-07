// ── Config ────────────────────────────────────────────────────────────────────
const DEV_MODE      = window.DEV_MODE === true || window.DEV_MODE === 'true';
// PMTILES_MODE: render zones from static vector tiles + client-side urgency,
// instead of per-request GeoJSON from /check. See js/urgency.js.
const PMTILES_MODE  = window.PMTILES_MODE === true || window.PMTILES_MODE === 'true';
const REGION_TZ     = window.REGION_TZ || {};

const DEFAULT_CENTER = { lat: 38, lon: -96, zoom: 4 };  // US overview — shown only if no car/IP data
const CAR_COLORS = ['#3b82f6','#10b981','#a855f7','#06b6d4','#ec4899','#84cc16','#6366f1','#22d3ee'];
function carColor(idx) { return CAR_COLORS[idx % CAR_COLORS.length]; }

// ── State ─────────────────────────────────────────────────────────────────────
let session        = null;
let cars           = [];
let activeCarId    = null;
let regions        = {};
let placingCar     = false;
let placingEditId  = null;
let pendingLat     = null, pendingLon = null;
let carSchedules   = {};
let _currentGeojson = null;   // last zone GeoJSON from /check
let _tempPin        = null;   // {lat,lon} while naming a new car
let _ctxScreenX     = 0, _ctxScreenY = 0;
let _selectedCarId  = null;
let _locSource = null, _locLat = null, _locLon = null, _locCity = null;
let _gpsLocPin = null;
let _gpsLocPinTimer = null;
let _hoverSuppressed = false;
let _renderedRegion  = null;
let _settingRegion   = false;  // true while setNearestRegion() is updating the select

// ── Map (MapLibre) ────────────────────────────────────────────────────────────
let map = null;
const _carMarkers  = new Map();  // carId → maplibregl.Marker
let _gpsMarker     = null;
let _tempPinMarker = null;
let _zonePopup     = null;  // MapLibre popup for clicked zone detail

const ZONES_SOURCE = 'sweeping-zones';
const ZONE_LAYERS  = ['zones-fill', 'zones-outline', 'zones-ward', 'zones-line'];
const HOVER_LAYERS = ['zones-fill', 'zones-line'];

// PMTILES mode: vector source + per-feature urgency via feature-state. Layer ids
// match the GeoJSON path so hover/click (HOVER_LAYERS) work unchanged. Colours
// mirror maps.py (_URGENCY_RGB / _zone_fill_color / _color_meta).
const TILES_SOURCE     = 'zones-tiles';
const TILES_SRC_LAYER  = 'zones';
const URGENCY_COLORS = {
  today:    { fill: 'rgba(220,60,60,0.55)',   border: 'rgba(220,60,60,0.90)',  line: 'tomato' },
  tomorrow: { fill: 'rgba(230,130,20,0.40)',  border: 'rgba(230,130,20,0.80)', line: 'orange' },
  clear:    { fill: 'rgba(80,110,180,0.18)',  border: 'rgba(80,110,180,0.40)', line: 'cornflowerblue' },
};
function _urgCase(prop) {
  // MapLibre expression: pick colour from feature-state 'urgency' (default clear).
  return [
    'case',
    ['==', ['feature-state', 'urgency'], 'today'],    URGENCY_COLORS.today[prop],
    ['==', ['feature-state', 'urgency'], 'tomorrow'], URGENCY_COLORS.tomorrow[prop],
    URGENCY_COLORS.clear[prop],
  ];
}
// Street line width scales with zoom so it stays thin on city-wide views.
const ZONE_LINE_WIDTH = ['interpolate', ['linear'], ['zoom'], 11, 0.8, 14, 1.5, 16, 2.5, 18, 4];

const LIGHT_STYLE = 'https://tiles.openfreemap.org/styles/positron';
const DARK_STYLE  = 'https://tiles.openfreemap.org/styles/dark';
// Night ~= 7pm–7am local time; picks the default basemap + UI chrome.
function _isNight() { const h = new Date().getHours(); return h >= 19 || h < 7; }
function _wantDark() {
  const stored = localStorage.getItem('bb_dark');
  return stored !== null ? stored === '1' : _isNight();
}
let _mapStyle = _wantDark() ? DARK_STYLE : LIGHT_STYLE;

// ── DOM ───────────────────────────────────────────────────────────────────────
const authScreen     = document.getElementById('auth-screen');
const appScreen      = document.getElementById('app-screen');
const emailEl        = document.getElementById('email');
const passwordEl     = document.getElementById('password');
const authError      = document.getElementById('auth-error');
const btnLogin       = document.getElementById('btn-login');
const btnSignup      = document.getElementById('btn-signup');
const btnLogout      = document.getElementById('btn-logout');
const btnLocate      = document.getElementById('btn-locate');
const regionSelect   = document.getElementById('region-select');
const statusText     = document.getElementById('status-text');
const mapDiv         = document.getElementById('map');
const placeBanner    = document.getElementById('place-banner');
const btnCancelPlace = document.getElementById('btn-cancel-place');
const ctxMenu        = document.getElementById('ctx-menu');
const ctxAddCar      = document.getElementById('ctx-add-car');
const carsPanel      = document.getElementById('cars-panel');
const namePanel      = document.getElementById('name-panel');
const namePanelInput = document.getElementById('name-panel-input');
const btnNpSave      = document.getElementById('btn-np-save');
const customHoverEl  = document.getElementById('custom-hover');

// ── Auth ──────────────────────────────────────────────────────────────────────
function _saveTokens(access, refresh) {
  localStorage.setItem('bb_access',  access);
  localStorage.setItem('bb_refresh', refresh);
  session = { access_token: access };
}

function _clearTokens() {
  localStorage.removeItem('bb_access');
  localStorage.removeItem('bb_refresh');
  session = null;
}

async function _tryRefresh() {
  const rt = localStorage.getItem('bb_refresh');
  if (!rt) return false;
  try {
    const res = await fetch('/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) { _clearTokens(); return false; }
    const data = await res.json();
    _saveTokens(data.access_token, data.refresh_token);
    return true;
  } catch (_) { return false; }
}

if (DEV_MODE) {
  session = { access_token: 'dev-token' };
  initApp();
} else {
  const stored = localStorage.getItem('bb_access');
  if (stored) {
    session = { access_token: stored };
    // Validate by attempting a prefs fetch; if 401 try refresh.
    fetch('/prefs', { headers: { Authorization: `Bearer ${stored}` } }).then(async r => {
      if (r.status === 401) {
        const ok = await _tryRefresh();
        ok ? initApp() : showAuth();
      } else {
        initApp();
      }
    }).catch(() => showAuth());
  } else {
    showAuth();
  }
}

// Auto-refresh access token 1 min before expiry (every 14 min).
setInterval(async () => { if (session && !DEV_MODE) await _tryRefresh(); }, 14 * 60 * 1000);

function showAuth() {
  authScreen.style.display = 'flex';
  appScreen.style.display  = 'none';
}

async function initApp() {
  authScreen.style.display = 'none';
  appScreen.style.display  = 'flex';
  // Wait one animation frame so the browser can lay out #app-screen
  // before MapLibre reads the container dimensions.
  await new Promise(r => requestAnimationFrame(r));

  // Load cities and saved cars BEFORE creating the map so we can open
  // directly at the right location instead of defaulting to Bay Area.
  await Promise.all([loadCities(), loadPrefs()]);

  // Pick initial map center from saved cars, falling back to US overview.
  const initCenter = cars.length > 0
    ? { lat: cars[0].lat, lon: cars[0].lon, zoom: 15 }
    : DEFAULT_CENTER;
  initMap(initCenter);

  // Fetch IP location in parallel with the first car check.
  const ipLocPromise = getIPLocation();

  if (cars.length > 0) {
    activeCarId = cars[0].id;
    setNearestRegion(cars[0].lat, cars[0].lon);
    await checkCarWithRender(cars[0]);
    for (const car of cars.slice(1)) checkCarSilently(car);
  } else {
    const ipLoc = await ipLocPromise;
    if (ipLoc) {
      setNearestRegion(ipLoc.lat, ipLoc.lon);
      setLocationKnown('ip', null, null, ipLoc.city);
      map.jumpTo({ center: [ipLoc.lon, ipLoc.lat], zoom: 13 });
      setStatus('idle', 'Loading map…');
      loadAreaMap(ipLoc.lat, ipLoc.lon, 13);
    } else {
      setStatus('idle', 'Select a region or add a car to load the map.');
    }
  }
}

// ── Auth forms ────────────────────────────────────────────────────────────────
async function login() {
  authError.textContent = '';
  btnLogin.disabled = true;
  btnLogin.innerHTML = '<span class="spinner"></span>Signing in…';
  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: emailEl.value.trim(), password: passwordEl.value }),
    });
    const data = await res.json();
    if (!res.ok) { authError.textContent = data.detail || 'Sign in failed'; return; }
    _saveTokens(data.access_token, data.refresh_token);
    initApp();
  } catch (e) {
    authError.textContent = 'Network error — is the server running?';
  } finally {
    btnLogin.disabled = false;
    btnLogin.textContent = 'Sign in';
  }
}

async function signup() {
  authError.textContent = '';
  btnSignup.disabled = true;
  btnSignup.innerHTML = '<span class="spinner"></span>Creating account…';
  try {
    const res = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: emailEl.value.trim(), password: passwordEl.value }),
    });
    const data = await res.json();
    if (!res.ok) { authError.textContent = data.detail || 'Registration failed'; return; }
    _saveTokens(data.access_token, data.refresh_token);
    initApp();
  } catch (e) {
    authError.textContent = 'Network error — is the server running?';
  } finally {
    btnSignup.disabled = false;
    btnSignup.textContent = 'Create account';
  }
}

btnLogin.addEventListener('click', login);
btnSignup.addEventListener('click', signup);
[emailEl, passwordEl].forEach(el => el.addEventListener('keydown', e => { if (e.key === 'Enter') login(); }));
btnLogout.addEventListener('click', () => { _clearTokens(); showAuth(); });

// ── Toast notifications ────────────────────────────────────────────────────────
const _toastContainer = document.getElementById('toast-container');
function showToast(msg, isError = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = msg;
  _toastContainer.appendChild(el);
  setTimeout(() => el.remove(), 3100);
}

// ── Dark mode ─────────────────────────────────────────────────────────────────
const _btnDark = document.getElementById('btn-dark-mode');
// Switch UI chrome AND basemap together. persist=true pins an explicit override
// (the toggle); otherwise the choice follows local time (night -> dark).
function _applyDark(on, persist) {
  document.body.classList.toggle('dark', on);
  _btnDark.textContent = on ? '☀️' : '🌙';
  _btnDark.title = on ? 'Switch to light' : 'Switch to dark';
  if (persist) localStorage.setItem('bb_dark', on ? '1' : '0');
  const style = on ? DARK_STYLE : LIGHT_STYLE;
  if (map && _mapStyle !== style) {
    _mapStyle = style;
    map.setStyle(_mapStyle);
    // Re-mount only after the NEW style settles. whenStyleReady() can't be used:
    // right after setStyle() isStyleLoaded() still reports the OLD style as
    // loaded, so the re-add fires early and the new style then wipes it (street/
    // zone layers vanishing on toggle). 'style.load' is unreliable on setStyle
    // here; 'idle' fires once the swapped style + basemap have settled.
    map.once('idle', () => {
      map.dragRotate.disable();
      map.touchZoomRotate.disableRotation();
      // setStyle() drops all sources/layers; force the tile source to remount.
      if (PMTILES_MODE) { _tilesRegion = null; ensureTiles(); }
      else if (_currentGeojson) addZoneLayers(_currentGeojson);
      updateCarMarkers();
    });
  } else {
    _mapStyle = style;
  }
}
(function _initDark() { _applyDark(_wantDark(), false); })();
_btnDark.addEventListener('click', () => _applyDark(!document.body.classList.contains('dark'), true));

// ── Snap chip ─────────────────────────────────────────────────────────────────
const _snapChip = document.getElementById('snap-chip');
function showSnap(snap) {
  if (!snap) { _snapChip.classList.remove('visible'); return; }
  const dist = snap.distance_m < 1 ? '<1 m' : `${snap.distance_m} m`;
  _snapChip.textContent = snap.is_polygon
    ? `📍 Zone: ${snap.street_name}`
    : `📍 ${snap.street_name} — ${dist}`;
  _snapChip.classList.add('visible');
}

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

  // Click → show zone detail (Chicago section schedule + PDF link) when a
  // zone is hit; a click anywhere away from a zone dismisses any open window
  // (zone popup, GPS pin popup, car selection) — same as pressing Esc.
  map.on('click', (e) => {
    if (placingCar) {
      commitPlacement(e.lngLat.lat, e.lngLat.lng);
      return;
    }
    const features = map.queryRenderedFeatures(e.point, { layers: HOVER_LAYERS.filter(l => !!map.getLayer(l)) });
    if (PMTILES_MODE) {
      // Tiles carry no detail_html; fetch the full-year popup for clicked zones.
      const poly = features.find(f => f.properties && f.properties.render_type === 'polygon');
      if (poly) { fetchZoneDetail(poly.properties, e.lngLat); return; }
      dismissMapWindows();
      return;
    }
    if (!features.length) { dismissMapWindows(); return; }
    const detailed = features.find(f => f.properties && f.properties.detail_html);
    if (detailed) showZoneDetail(e.lngLat, detailed.properties.detail_html);
    else dismissMapWindows();
  });

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
let _featStateCache   = new Map();  // feature id -> urgency (for current day)
let _featStateDay     = null;
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
      _featStateCache.clear(); _featStateDay = null;
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
// which drives the paint expressions. Cached per feature id for the current day.
function applyUrgencyStates() {
  if (!PMTILES_MODE || !map || !_tilesRegion || !map.getSource(TILES_SOURCE)) return;
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  const dayStamp = now.y + '-' + now.m + '-' + now.d;
  if (dayStamp !== _featStateDay) { _featStateCache.clear(); _featStateDay = dayStamp; }
  let feats;
  try { feats = map.querySourceFeatures(TILES_SOURCE, { sourceLayer: TILES_SRC_LAYER }); }
  catch (_) { return; }
  for (const f of feats) {
    if (f.id === undefined || f.id === null) continue;
    if (_featStateCache.has(f.id)) continue;
    const u = BroomUrgency.urgencyForSched(f.properties.sched, now);
    _featStateCache.set(f.id, u);
    map.setFeatureState(
      { source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id: f.id },
      { urgency: u },
    );
  }
}

function tileHoverHtml(props) {
  let sched = [];
  try { sched = JSON.parse(props.sched || '[]'); } catch (_) {}
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  // Canonical formatting (day-first, "Every <Wd>" merge, Mon->Sun order, merged
  // next-cluster dates) — identical to the card and the server hover.
  const evens = BroomUrgency.formatScheduleSide(sched.filter(e => e.side === 'even'), now);
  const odds  = BroomUrgency.formatScheduleSide(sched.filter(e => e.side === 'odd'), now);
  let sched_html;
  if (evens.length && odds.length && evens.join('|') === odds.join('|')) {
    sched_html = evens.join('<br>');             // both sides identical → no label
  } else if (!evens.length && !odds.length) {
    sched_html = '';
  } else {
    sched_html = [...evens.map(e => 'Even: ' + e), ...odds.map(o => 'Odd: ' + o)].join('<br>');
  }
  // No real schedule (only N/A / no-sweep) → no hover at all.
  return sched_html ? `<b>${esc(props.street || '')}</b><br>${sched_html}` : '';
}

async function fetchZoneDetail(props, lngLat) {
  let code = '';
  try { code = (JSON.parse(props.sched || '[]')[0] || {}).code || ''; } catch (_) {}
  if (!code) return;
  const region = regionSelect.value || _renderedRegion || '';
  const qs = new URLSearchParams({ code, street: props.street || '', city: props.city || '', region });
  try {
    const res = await apiFetch('/zone/detail?' + qs.toString());
    if (!res.ok) return;
    const data = await res.json();
    if (data.detail_html) showZoneDetail(lngLat, data.detail_html);
  } catch (_) {}
}

// ── Car markers (MapLibre HTML markers) ──────────────────────────────────────
function updateCarMarkers() {
  // Remove markers for cars that no longer exist
  for (const [id, marker] of _carMarkers) {
    if (!cars.find(c => c.id === id)) { marker.remove(); _carMarkers.delete(id); }
  }

  cars.forEach((car, i) => {
    const color      = carColor(i);
    const isSelected = car.id === _selectedCarId;
    const size       = isSelected ? 22 : 16;

    if (_carMarkers.has(car.id)) {
      const marker = _carMarkers.get(car.id);
      marker.setLngLat([car.lon, car.lat]);
      const el = marker.getElement();
      el.style.width  = size + 'px';
      el.style.height = size + 'px';
      el.style.borderColor = isSelected ? color : 'white';
    } else {
      const el = document.createElement('div');
      el.className = 'car-marker' + (isSelected ? ' selected' : '');
      el.style.cssText = `width:${size}px;height:${size}px;background:${color};--marker-color:${color};`;
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        setSelectedCar(car.id);
        const entry = carsPanel.querySelector(`.car-entry[data-id="${car.id}"]`);
        if (entry) entry.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
      const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([car.lon, car.lat])
        .addTo(map);
      _carMarkers.set(car.id, marker);
    }
  });

  // Temp pin (new car placement)
  if (_tempPin) {
    if (!_tempPinMarker) {
      const el = document.createElement('div');
      el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#64748b;border:3px solid #111;box-shadow:0 2px 6px rgba(0,0,0,.4);';
      _tempPinMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([_tempPin.lon, _tempPin.lat]).addTo(map);
    } else {
      _tempPinMarker.setLngLat([_tempPin.lon, _tempPin.lat]);
    }
  } else if (_tempPinMarker) {
    _tempPinMarker.remove(); _tempPinMarker = null;
  }

  // GPS "you are here" pin
  if (_gpsLocPin) {
    if (!_gpsMarker) {
      const el = document.createElement('div');
      el.style.cssText = 'width:14px;height:14px;border-radius:50%;background:#2563eb;border:3px solid white;box-shadow:0 0 0 6px rgba(37,99,235,.2),0 2px 8px rgba(0,0,0,.4);';
      _gpsMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([_gpsLocPin.lon, _gpsLocPin.lat]).addTo(map);
    } else {
      _gpsMarker.setLngLat([_gpsLocPin.lon, _gpsLocPin.lat]);
    }
  } else if (_gpsMarker) {
    _gpsMarker.remove(); _gpsMarker = null;
  }

  renderCarsPanel();
}

// ── Car selection ─────────────────────────────────────────────────────────────
function clearCarSelection() {
  _selectedCarId = null;
  for (const e of carsPanel.querySelectorAll('.car-entry')) e.classList.remove('selected');
  updateCarMarkers();
}

function isCarInView(car) {
  try { return map?.getBounds()?.contains([car.lon, car.lat]) ?? false; }
  catch (_) { return false; }
}

function setSelectedCar(carId) {
  _selectedCarId = carId;
  for (const e of carsPanel.querySelectorAll('.car-entry')) {
    e.classList.toggle('selected', e.dataset.id === carId);
  }
  const car = cars.find(c => c.id === carId);
  if (car && !isCarInView(car)) map.jumpTo({ center: [car.lon, car.lat], zoom: 16 });
  updateCarMarkers();
}

// ── GPS pin popup ─────────────────────────────────────────────────────────────
function updateGpsPinPopup() {
  const popup = document.getElementById('gps-pin-popup');
  if (!_gpsLocPin || !map) { popup.style.display = 'none'; return; }
  const px   = map.project([_gpsLocPin.lon, _gpsLocPin.lat]);
  const rect = mapDiv.getBoundingClientRect();
  popup.style.left = Math.round(rect.left + px.x) + 'px';
  popup.style.top  = Math.round(rect.top  + px.y) + 'px';
  popup.style.display = 'flex';
}

function hideGpsPinPopup() {
  if (_gpsLocPinTimer) { clearTimeout(_gpsLocPinTimer); _gpsLocPinTimer = null; }
  _gpsLocPin = null;
  document.getElementById('gps-pin-popup').style.display = 'none';
  updateCarMarkers();
}

document.getElementById('btn-gps-add').addEventListener('click', () => {
  if (!_gpsLocPin) return;
  pendingLat = _gpsLocPin.lat; pendingLon = _gpsLocPin.lon;
  addTempPin(pendingLat, pendingLon);
  const popup = document.getElementById('gps-pin-popup');
  showNamePanel(popup.getBoundingClientRect());
});

document.getElementById('btn-gps-close').addEventListener('click', hideGpsPinPopup);

// ── Zone detail popup (click a section) ─────────────────────────────────────────
function showZoneDetail(lngLat, html) {
  if (_zonePopup) _zonePopup.remove();
  _zonePopup = new maplibregl.Popup({
    closeButton: true, closeOnClick: false, maxWidth: '300px', className: 'zone-detail-popup',
  }).setLngLat(lngLat).setHTML(html).addTo(map);
  _zonePopup.on('close', () => { _zonePopup = null; });
}

function closeZoneDetail() {
  if (_zonePopup) { _zonePopup.remove(); _zonePopup = null; }
}

// Dismiss whatever transient map window is open — the click-away analogue of Esc.
function dismissMapWindows() {
  if (_gpsLocPin) hideGpsPinPopup();
  closeZoneDetail();
  clearCarSelection();
}

// ── Map contextmenu (right-click to add car) ──────────────────────────────────
mapDiv.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (!map) return;
  const rect = mapDiv.getBoundingClientRect();
  const ll   = map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
  pendingLat = ll.lat; pendingLon = ll.lng;
  showCtxMenu(e.clientX, e.clientY);
});

document.addEventListener('click', e => {
  if (!e.target.closest('#ctx-menu')) hideCtxMenu();
  if (namePanel.style.display !== 'none'
      && !e.target.closest('#name-panel')
      && !e.target.closest('#ctx-menu')
      && !e.target.closest('#gps-pin-popup')) {
    hideNamePanel();
  }
});

// Holistic Escape: dismiss whichever transient UI is open (one layer per
// press), and if nothing is open, deselect the selected car card.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  // Editable fields own Escape locally (cancel rename / close name input);
  // don't cascade into dismissing layers or deselecting the card.
  const ae = document.activeElement;
  if (ae && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  let dismissed = false;
  if (ctxMenu.style.display === 'block')       { hideCtxMenu();      dismissed = true; }
  if (namePanel.style.display !== 'none')       { hideNamePanel();    dismissed = true; }
  if (_gpsLocPin)                               { hideGpsPinPopup();  dismissed = true; }
  if (_zonePopup)                               { closeZoneDetail();  dismissed = true; }
  if (placingCar)                               { stopPlacing();      dismissed = true; }
  if (dismissed) return;
  if (_selectedCarId) clearCarSelection();
});

carsPanel.addEventListener('mouseenter', () => { _hoverSuppressed = true;  customHoverEl.style.display = 'none'; });
carsPanel.addEventListener('mouseleave', () => { _hoverSuppressed = false; });

// ── Helpers ───────────────────────────────────────────────────────────────────
function abbreviate(s) {
  if (!s) return s;
  return s
    .replace(/\bAvenue\b/gi, 'Ave').replace(/\bBoulevard\b/gi, 'Blvd')
    .replace(/\bStreet\b/gi, 'St').replace(/\bDrive\b/gi, 'Dr')
    .replace(/\bCourt\b/gi, 'Ct').replace(/\bPlace\b/gi, 'Pl')
    .replace(/\bLane\b/gi, 'Ln').replace(/\bRoad\b/gi, 'Rd')
    .replace(/\bNorth\b/gi, 'N').replace(/\bSouth\b/gi, 'S')
    .replace(/\bEast\b/gi, 'E').replace(/\bWest\b/gi, 'W');
}

function latLonToScreen(lat, lon) {
  if (!map) return null;
  const rect = mapDiv.getBoundingClientRect();
  const px   = map.project([lon, lat]);
  return { x: rect.left + px.x, y: rect.top + px.y };
}

function nearestRegionNameByCoords(lat, lon) {
  let best = null, bestDist = Infinity;
  for (const [, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestDist) { bestDist = d; best = r.name; }
  }
  return best || '';
}

function setLocationKnown(source, lat = null, lon = null, city = null) {
  _locSource = source || null; _locLat = lat; _locLon = lon; _locCity = city || null;
  btnLocate.classList.remove('ip', 'gps');
  if (source) btnLocate.classList.add(source);
  updateLocateInfo();
}

function updateLocateInfo() {
  const el = document.getElementById('locate-info');
  if (!_locSource) { el.textContent = ''; return; }
  if (_locSource === 'ip') {
    el.textContent = _locCity ? `IP: ${_locCity}` : 'IP';
  } else {
    const coords = (_locLat !== null && _locLon !== null)
      ? `${_locLat.toFixed(4)}, ${_locLon.toFixed(4)}` : '';
    const area = _locCity || nearestRegionNameByCoords(_locLat, _locLon);
    el.textContent = coords
      ? `GPS: ${coords}${area ? ' · ' + area : ''}`
      : `GPS${area ? ': ' + area : ''}`;
  }
}

// ── Cities / regions ──────────────────────────────────────────────────────────
async function loadCities() {
  try {
    const res = await fetch('/cities');
    if (!res.ok) return;
    const data = await res.json();
    regions = {};
    regionSelect.innerHTML = '';
    for (const [key, val] of Object.entries(data.regions)) {
      regions[key] = { name: val.name, center: val.center, zoom: val.overview_zoom || 11 };
      const opt = document.createElement('option');
      opt.value = key; opt.textContent = val.name;
      regionSelect.appendChild(opt);
    }
  } catch (_) {}
}

regionSelect.addEventListener('change', () => {
  if (_settingRegion) return;  // programmatic update — don't cascade
  const rk    = regionSelect.value;
  const rName = regions[rk]?.name || rk;
  const rc    = regions[rk]?.center;
  const newCenter = rc || DEFAULT_CENTER;

  _currentGeojson  = null;
  _renderedRegion  = rk;
  renderZones(null);
  if (map) map.jumpTo({ center: [newCenter.lon, newCenter.lat], zoom: regions[rk]?.zoom || 11 });
  updateCarMarkers();

  const carInRegion = cars.find(c => {
    let best = null, bd = Infinity;
    for (const [k, r] of Object.entries(regions)) {
      const d = (r.center.lat - c.lat) ** 2 + (r.center.lon - c.lon) ** 2;
      if (d < bd) { bd = d; best = k; }
    }
    return best === rk;
  });
  if (carInRegion) { activeCarId = carInRegion.id; checkCarWithRender(carInRegion); }
  else { setStatus('idle', 'Add a car to check street sweeping.'); }
  // map.jumpTo above fires moveend → tile-based fetch handles the rest
});

function setNearestRegion(lat, lon) {
  let best = null, bestDist = Infinity;
  for (const [key, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestDist) { bestDist = d; best = key; }
  }
  if (best && regionSelect.value !== best) {
    _settingRegion = true;
    regionSelect.value = best;
    _settingRegion = false;
  }
  if (best) _renderedRegion = best;
}

// ── Cars ──────────────────────────────────────────────────────────────────────
async function loadPrefs() {
  if (!session) return;
  try {
    const res = await apiFetch('/prefs');
    if (res.ok) { const prefs = await res.json(); cars = prefs.cars || []; }
  } catch (_) {}
}

async function savePrefs() {
  if (!session) return;
  try {
    await apiFetch('/prefs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cars }),
    });
  } catch (_) {}
}

async function getIPLocation() {
  try {
    const r = await fetch('https://ipapi.co/json/');
    const d = await r.json();
    if (d.latitude && d.longitude) return { lat: d.latitude, lon: d.longitude, city: d.city || '' };
  } catch (_) {}
  return null;
}

function loadAreaMap(lat, lon, zoom = 11) {
  _renderedRegion = regionSelect.value || _renderedRegion;
  if (!map || !_renderedRegion) return;
  map.jumpTo({ center: [lon, lat], zoom });
  setStatus('idle', 'Add a car to check street sweeping.');
  // jumpTo fires moveend → debounced fetch, but we also fire immediately
  // so the user doesn't wait the 200 ms debounce for the first frame.
  fetchViewport(map.getBounds());
}

function getGPS(onSuccess, onError) {
  if (!navigator.geolocation) { onError?.('Geolocation not supported.'); return; }
  navigator.geolocation.getCurrentPosition(
    pos => onSuccess(pos.coords.latitude, pos.coords.longitude),
    err => onError?.(err.message),
    { enableHighAccuracy: true, timeout: 10000 }
  );
}

// ── Per-car schedule checks ───────────────────────────────────────────────────
async function checkCarWithRender(car) {
  setStatus('idle', 'Checking…');
  const warmupTimer = setTimeout(() => setStatus('idle', '⏳ Server warming up, please wait…'), 5000);
  try {
    const res = await apiFetch('/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: car.lat, lon: car.lon, region: regionSelect.value || undefined }),
    });
    clearTimeout(warmupTimer);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      setStatus('idle', `Error: ${err.detail}`); return;
    }
    const data = await res.json();
    carSchedules[car.id] = data;
    renderZones(data.geojson);
    showSnap(data.snap || null);
    if (map) map.jumpTo({ center: [car.lon, car.lat], zoom: 16 });
    updateCarMarkers();
    updateStatusFromSchedules();
    renderCarsPanel();
  } catch (e) { clearTimeout(warmupTimer); setStatus('idle', `Network error: ${e.message}`); }
}

async function checkCarSilently(car) {
  try {
    const res = await apiFetch('/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: car.lat, lon: car.lon, region: regionSelect.value || undefined }),
    });
    if (!res.ok) return;
    carSchedules[car.id] = await res.json();
    updateStatusFromSchedules();
    updateCarMarkers();
  } catch (_) {}
}

function updateStatusFromSchedules() {
  const today = new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const todayNames    = [];
  const tomorrowNames = [];
  for (const [carId, s] of Object.entries(carSchedules)) {
    const car = cars.find(c => c.id === carId);
    if (!car) continue;
    if (s.urgency === 'today')    todayNames.push(esc(car.name));
    if (s.urgency === 'tomorrow') tomorrowNames.push(esc(car.name));
  }
  const dateSpan = `<span style="color:var(--muted);font-weight:400">${today}</span>&emsp;`;
  if (todayNames.length) {
    statusText.className = 'today';
    statusText.innerHTML = `${dateSpan}🚨 Move ${todayNames.join(', ')} today!`;
  } else if (tomorrowNames.length) {
    statusText.className = 'tomorrow';
    statusText.innerHTML = `${dateSpan}⚠️ Move ${tomorrowNames.join(', ')} tomorrow.`;
  } else {
    statusText.className = 'safe';
    statusText.innerHTML = `${dateSpan}✅ No sweeping today or tomorrow.`;
  }
}

// ── Car placement ─────────────────────────────────────────────────────────────
function startPlacing(editCarId = null) {
  placingCar    = true;
  placingEditId = editCarId;
  placeBanner.classList.add('active');
  if (map) map.getCanvas().style.cursor = 'crosshair';
}

function stopPlacing() {
  placingCar    = false;
  placingEditId = null;
  placeBanner.classList.remove('active');
  if (map) map.getCanvas().style.cursor = '';
}

async function commitPlacement(lat, lon) {
  const editId = placingEditId;
  stopPlacing();

  if (editId) {
    const car = cars.find(c => c.id === editId);
    if (car) {
      car.lat = lat; car.lon = lon;
      await savePrefs();
      updateCarMarkers();
      checkCarSilently(car);
    }
  } else {
    pendingLat = lat; pendingLon = lon;
    addTempPin(lat, lon);
    const sc      = latLonToScreen(lat, lon);
    const mapRect = mapDiv.getBoundingClientRect();
    const anchorX = sc ? sc.x : mapRect.left + mapRect.width  / 2;
    const anchorY = sc ? sc.y : mapRect.top  + mapRect.height / 2;
    showNamePanel({ left: anchorX, top: anchorY, width: ctxMenu.offsetWidth || 160 });
  }
}

btnCancelPlace.addEventListener('click', stopPlacing);

// ── Context menu ──────────────────────────────────────────────────────────────
function showCtxMenu(x, y) {
  _ctxScreenX = x; _ctxScreenY = y;
  const mw = 170, mh = 44;
  ctxMenu.style.left = Math.min(x, window.innerWidth  - mw) + 'px';
  ctxMenu.style.top  = Math.min(y, window.innerHeight - mh) + 'px';
  ctxMenu.style.display = 'block';
}
function hideCtxMenu() { ctxMenu.style.display = 'none'; }

ctxAddCar.addEventListener('click', () => {
  const rect = ctxMenu.getBoundingClientRect();
  hideCtxMenu();
  addTempPin(pendingLat, pendingLon);
  showNamePanel(rect);
});

// ── Inline name panel ─────────────────────────────────────────────────────────
function showNamePanel(rect) {
  namePanelInput.value    = defaultCarName();
  namePanel.style.left    = rect.left  + 'px';
  namePanel.style.top     = rect.top   + 'px';
  // Widen so the car name has room regardless of how narrow the anchor is.
  namePanel.style.width   = Math.max(rect.width, 240) + 'px';
  namePanel.style.display = 'block';
  setTimeout(() => { namePanelInput.focus(); namePanelInput.select(); }, 30);
}

function hideNamePanel() {
  namePanel.style.display = 'none';
  removeTempPin();
  pendingLat = pendingLon = null;
}

btnNpSave.addEventListener('click', savePendingCar);
document.getElementById('btn-np-close').addEventListener('click', hideNamePanel);
namePanelInput.addEventListener('keydown', e => {
  if (e.key === 'Enter')  savePendingCar();
  if (e.key === 'Escape') hideNamePanel();
});

async function savePendingCar() {
  if (pendingLat === null) return;
  const name = namePanelInput.value.trim() || 'My car';
  const id = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36);
  cars.push({ id, name, lat: pendingLat, lon: pendingLon });
  removeTempPin();
  namePanel.style.display = 'none';
  if (_gpsLocPin) {
    _gpsLocPin = null;
    if (_gpsLocPinTimer) { clearTimeout(_gpsLocPinTimer); _gpsLocPinTimer = null; }
    document.getElementById('gps-pin-popup').style.display = 'none';
  }
  await savePrefs();
  activeCarId = id;
  setNearestRegion(pendingLat, pendingLon);
  updateCarMarkers();
  checkCarSilently(cars[cars.length - 1]);
  pendingLat = pendingLon = null;
}

function addTempPin(lat, lon) { _tempPin = { lat, lon }; updateCarMarkers(); }
function removeTempPin()      { _tempPin = null;         updateCarMarkers(); }

function defaultCarName() {
  const used = new Set(cars.map(c => c.name));
  if (!used.has('Car')) return 'Car';
  let i = 2;
  while (used.has(`Car ${i}`)) i++;
  return `Car ${i}`;
}

// ── Cars panel ────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function scheduleHTML(sched) {
  if (!sched) return '<span style="color:var(--muted)">Loading…</span>';
  const urgency  = sched.urgency || 'safe';
  const urgColor = urgency === 'today'    ? '#ef4444'
                 : urgency === 'tomorrow' ? '#f97316' : '#2563eb';
  const urgLabel = urgency === 'today'    ? '🚨 Move car today!'
                 : urgency === 'tomorrow' ? '⚠️ Move car tomorrow'
                 : '✅ All clear';

  const now   = BroomUrgency.nowForTimeZone(REGION_TZ[regionSelect.value] || 'UTC');
  const side  = sched.car_side || 'even';
  let lines   = BroomUrgency.formatScheduleSide(
    side === 'even' ? sched.schedule_even : sched.schedule_odd, now);
  if (!lines.length) lines = ['No sweeping scheduled'];
  lines = lines.slice(0, 4);
  const itemsHTML = lines.map(l => `<div class="ce-sched-item">${esc(l)}</div>`).join('');

  return `<div class="ce-sched-urgency" style="color:${urgColor}">${urgLabel}</div>`
       + `<div class="ce-sched-header">Street sweeping schedule:</div>`
       + itemsHTML;
}

function renderCarsPanel() {
  const ae = document.activeElement;
  if (carsPanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  for (const el of [...carsPanel.querySelectorAll('.car-entry')]) el.remove();
  cars.forEach((car, i) => {
    const color   = carColor(i);
    const sched   = carSchedules[car.id];
    const urgency = sched?.urgency || 'safe';
    const urgColor = urgency === 'today'    ? '#ef4444'
                   : urgency === 'tomorrow' ? '#f97316' : '#2563eb';
    const addrText = abbreviate(sched?.address || '');

    const entry = document.createElement('div');
    entry.className = 'car-entry' + (car.id === _selectedCarId ? ' selected' : '');
    entry.dataset.id = car.id;
    // Urgency tint is theme-aware via [data-urgency] CSS (dark mode needs
    // different backgrounds), so set the attribute instead of a hardcoded hex.
    entry.dataset.urgency = urgency;
    entry.style.cssText = `--car-color:${color};--urg-color:${urgColor}`;
    entry.addEventListener('click', e => {
      if (e.target.closest('button') || e.target.closest('[contenteditable="true"]')) return;
      setSelectedCar(car.id);
    });

    entry.innerHTML = `
      <div class="ce-header">
        <span class="ce-dot"></span>
        <span class="ce-name" contenteditable="false" data-id="${esc(car.id)}" title="Double-click to edit">${esc(car.name)}</span>
        <button class="ce-remove" data-id="${esc(car.id)}" title="Remove">✕</button>
      </div>
      <div class="ce-addr" contenteditable="false" data-id="${esc(car.id)}" title="Double-click to edit">${esc(addrText)}</div>
      <div class="ce-sched">${scheduleHTML(sched)}</div>
      <div class="ce-actions">
        <button class="ce-btn ce-btn-gps" data-id="${esc(car.id)}">📍 GPS</button>
        <button class="ce-btn ce-btn-place" data-id="${esc(car.id)}">📌 Set location</button>
      </div>`;

    // ── Name editing ──
    const nameEl = entry.querySelector('.ce-name');
    nameEl.addEventListener('dblclick', () => {
      nameEl.contentEditable = 'true'; nameEl.focus();
      const r = document.createRange(); r.selectNodeContents(nameEl);
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    });
    nameEl.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); nameEl.blur(); }
      if (e.key === 'Escape') { nameEl.textContent = car.name; nameEl.blur(); }
    });
    nameEl.addEventListener('blur', async () => {
      nameEl.contentEditable = 'false';
      const v = nameEl.textContent.trim();
      if (v && v !== car.name) { car.name = v; await savePrefs(); }
      else nameEl.textContent = car.name;
    });

    // ── Address editing ──
    const addrEl = entry.querySelector('.ce-addr');
    addrEl.addEventListener('dblclick', () => {
      addrEl.contentEditable = 'true'; addrEl.focus();
      const r = document.createRange(); r.selectNodeContents(addrEl);
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    });
    addrEl.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addrEl.blur(); }
      if (e.key === 'Escape') { addrEl.textContent = addrText; addrEl.blur(); }
    });
    addrEl.addEventListener('blur', async () => {
      addrEl.contentEditable = 'false';
      const q = addrEl.textContent.trim();
      if (!q || q === addrText) { addrEl.textContent = addrText; return; }
      addrEl.textContent = '…';
      try {
        const res  = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&addressdetails=1&q=${encodeURIComponent(q)}`);
        const hits = await res.json();
        if (!hits.length) {
          addrEl.style.color = 'var(--red)';
          addrEl.textContent = '⚠️ Address not found';
          setTimeout(() => { addrEl.style.color = ''; addrEl.textContent = addrText; }, 2500);
          return;
        }
        car.lat = parseFloat(hits[0].lat); car.lon = parseFloat(hits[0].lon);
        await savePrefs();
        setNearestRegion(car.lat, car.lon);
        updateCarMarkers();
        checkCarSilently(car);
      } catch (_) { addrEl.textContent = addrText; }
    });

    // ── GPS button ──
    entry.querySelector('.ce-btn-gps').addEventListener('click', () => {
      const btn = entry.querySelector('.ce-btn-gps');
      btn.disabled = true; btn.textContent = '…';
      getGPS(async (lat, lon) => {
        car.lat = lat; car.lon = lon;
        await savePrefs();
        btn.disabled = false; btn.textContent = '📍 GPS';
        setNearestRegion(lat, lon); setLocationKnown('gps', lat, lon);
        if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
        await checkCarWithRender(car);
      }, msg => { btn.disabled = false; btn.textContent = '📍 GPS'; showToast(`GPS: ${msg}`, true); });
    });

    // ── Set location button ──
    entry.querySelector('.ce-btn-place').addEventListener('click', () => startPlacing(car.id));

    // ── Remove button ──
    entry.querySelector('.ce-remove').addEventListener('click', async () => {
      const marker = _carMarkers.get(car.id);
      if (marker) { marker.remove(); _carMarkers.delete(car.id); }
      delete carSchedules[car.id];
      cars = cars.filter(c => c.id !== car.id);
      if (activeCarId === car.id) activeCarId = cars[0]?.id ?? null;
      if (_selectedCarId === car.id) _selectedCarId = null;
      await savePrefs();
      updateCarMarkers();
      renderCarsPanel();
      if (!cars.length) setStatus('idle', 'Add a car to check street sweeping.');
      else updateStatusFromSchedules();
    });

    carsPanel.appendChild(entry);
  });
}

// ── API fetch ─────────────────────────────────────────────────────────────────
function apiFetch(path, opts = {}) {
  const token = session?.access_token;
  return fetch(path, {
    ...opts,
    headers: { ...(opts.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
}

// ── Locate button (GPS) ───────────────────────────────────────────────────────
document.getElementById('btn-locate').addEventListener('click', () => {
  const btn = document.getElementById('btn-locate');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="border-top-color:#2563eb;border-color:rgba(0,0,0,.15)"></span>';
  getGPS(async (lat, lon) => {
    btn.disabled = false; btn.textContent = '📍';
    setLocationKnown('gps', lat, lon);
    setNearestRegion(lat, lon);
    if (_gpsLocPinTimer) clearTimeout(_gpsLocPinTimer);
    _gpsLocPin = { lat, lon };
    if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
    updateCarMarkers();
    updateGpsPinPopup();
    loadAreaMap(lat, lon, 16);
    updateGpsPinPopup();
    _gpsLocPinTimer = setTimeout(() => {
      _gpsLocPin = null; _gpsLocPinTimer = null;
      document.getElementById('gps-pin-popup').style.display = 'none';
      updateCarMarkers();
    }, 15000);
    for (const car of cars) checkCarSilently(car);
  }, msg => { btn.disabled = false; btn.textContent = '📍'; showToast(`GPS: ${msg}`, true); });
});

function setStatus(cls, text) { statusText.className = cls; statusText.textContent = text; }

// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
