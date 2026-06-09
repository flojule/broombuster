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
let home           = null;       // { lat, lon, address } — the saved residence
let homeSchedule   = null;       // /check-home response (home-subject domains[])
let _homeMarker    = null;       // maplibregl.Marker for the home pin
let _gpsMarker     = null;
let _tempPinMarker = null;
let _zonePopup     = null;  // MapLibre popup for clicked zone detail
let _namePopup     = null;  // MapLibre popup (arrow box) for naming a new car
let _nameInput     = null;  // <input> inside the active name popup

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
const btnSignin      = document.getElementById('btn-signin');
const authClose      = document.getElementById('btn-auth-close');
const btnLocate      = document.getElementById('btn-locate');
const regionSelect   = document.getElementById('region-select');
const statusText     = document.getElementById('status-text');
const mapDiv         = document.getElementById('map');
const placeBanner    = document.getElementById('place-banner');
const btnCancelPlace = document.getElementById('btn-cancel-place');
const ctxMenu        = document.getElementById('ctx-menu');
const ctxAddCar      = document.getElementById('ctx-add-car');
const ctxAddHome     = document.getElementById('ctx-add-home');
const carsPanel      = document.getElementById('cars-panel');
const homePanel      = document.getElementById('home-panel');
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

// Guest prefs live in sessionStorage — discarded when the tab/window closes.
const GUEST_PREFS_KEY = 'bb_guest_prefs';
function _loadGuestPrefs() {
  try { return JSON.parse(sessionStorage.getItem(GUEST_PREFS_KEY)) || {}; }
  catch (_) { return {}; }
}
function _saveGuestPrefs(p) {
  try { sessionStorage.setItem(GUEST_PREFS_KEY, JSON.stringify(p)); } catch (_) {}
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
    // Validate the stored token; on 401 try refresh, else drop to guest mode.
    fetch('/prefs', { headers: { Authorization: `Bearer ${stored}` } }).then(async r => {
      if (r.status === 401 && !await _tryRefresh()) _clearTokens();
      initApp();
    }).catch(() => initApp());
  } else {
    // Guest by default — no gate. Prefs live in sessionStorage until tab close.
    initApp();
  }
}

// Auto-refresh access token 1 min before expiry (every 14 min).
setInterval(async () => { if (session && !DEV_MODE) await _tryRefresh(); }, 14 * 60 * 1000);

function showAuth() {
  authError.textContent = '';
  authScreen.hidden = false;
  emailEl.focus();
}
function hideAuth() { authScreen.hidden = true; }

// Toggle the toolbar's Sign in / Sign out buttons to match auth state.
function _updateAuthUI() {
  if (DEV_MODE) { btnSignin.hidden = true; btnLogout.hidden = true; return; }
  btnSignin.hidden = !!session;
  btnLogout.hidden = !session;
}

async function initApp() {
  authScreen.hidden = true;
  appScreen.style.display  = 'flex';
  _updateAuthUI();
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

  // Render the home card + pin and refresh its trash schedule (if saved).
  renderHomePanel();
  updateHomeMarker();
  if (home) checkHome();

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

// After a successful login/signup: adopt the guest's setup into the account,
// then reload so the app comes back signed in with server-backed prefs.
async function _finishAuth() {
  await _migrateGuestToServer();
  sessionStorage.removeItem(GUEST_PREFS_KEY);
  location.reload();
}

// Merge the guest's cars/home (in-memory + sessionStorage) into the account,
// keeping anything the account already had. New cars dedupe by name.
async function _migrateGuestToServer() {
  const g = _loadGuestPrefs();
  const guestCars = [...(g.cars || [])];
  for (const c of cars) if (!guestCars.find(x => x.id === c.id)) guestCars.push(c);
  const guestHome = home
    || (g.home_lat != null && g.home_lon != null
        ? { lat: g.home_lat, lon: g.home_lon, address: g.home_address || '' } : null);

  let acct = {};
  try { const r = await apiFetch('/prefs'); if (r.ok) acct = await r.json(); } catch (_) {}

  const merged = [...(acct.cars || [])];
  const names = new Set(merged.map(c => c.name));
  for (const c of guestCars) if (!names.has(c.name)) { merged.push(c); names.add(c.name); }

  const finalHome = guestHome
    || (acct.home_lat != null ? { lat: acct.home_lat, lon: acct.home_lon, address: acct.home_address || '' } : null);

  if (merged.length === 0 && !finalHome) return;  // nothing to persist
  try {
    await apiFetch('/prefs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        cars: merged,
        home_lat: finalHome?.lat ?? null,
        home_lon: finalHome?.lon ?? null,
        home_address: finalHome?.address ?? null,
      }),
    });
  } catch (_) {}
}

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
    await _finishAuth();
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
    await _finishAuth();
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
btnSignin.addEventListener('click', showAuth);
authClose.addEventListener('click', hideAuth);
authScreen.addEventListener('click', e => { if (e.target === authScreen) hideAuth(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !authScreen.hidden) hideAuth(); });
// Logout returns to guest mode (server data stays on the account).
btnLogout.addEventListener('click', () => { _clearTokens(); location.reload(); });

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
  const styleChanged = _mapStyle !== style;
  _mapStyle = style;
  if (map && styleChanged) {
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
  }
}
(function _initDark() { _applyDark(_wantDark(), false); })();
_btnDark.addEventListener('click', () => _applyDark(!document.body.classList.contains('dark'), true));

// ── Center banner ─────────────────────────────────────────────────────────────
// Always-on pill that reports the street under the viewport center (the
// mobile-friendly equivalent of hovering). Same headline format as the old snap
// chip, plus the schedule lines the hover tooltip shows; one line, tap to expand.
const _snapChip   = document.getElementById('snap-chip');
const _crosshair  = document.getElementById('center-crosshair');
_snapChip.addEventListener('click', () => _snapChip.classList.toggle('expanded'));

// Meters from a {lng,lat} point to a line/multiline geometry; null for polygons.
// Short-range equirectangular approximation (cos-scaled longitude).
function _segDistM(p, a, b) {
  const R = 6371000, rad = Math.PI / 180, k = Math.cos(p.lat * rad);
  const px = p.lng * rad * k * R, py = p.lat * rad * R;
  const ax = a[0] * rad * k * R, ay = a[1] * rad * R;
  const bx = b[0] * rad * k * R, by = b[1] * rad * R;
  const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
  let t = L2 ? ((px - ax) * dx + (py - ay) * dy) / L2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
function _geomDistM(center, geom) {
  if (!geom) return null;
  let lines = [];
  if (geom.type === 'LineString') lines = [geom.coordinates];
  else if (geom.type === 'MultiLineString') lines = geom.coordinates;
  else return null;
  let best = Infinity;
  for (const line of lines)
    for (let i = 0; i + 1 < line.length; i++)
      best = Math.min(best, _segDistM(center, line[i], line[i + 1]));
  return isFinite(best) ? best : null;
}

// Query the feature under the viewport center (small pixel box so thin street
// lines are forgiving on touch). Returns the topmost hit or null.
function _centerFeature() {
  if (!map) return null;
  const layers = HOVER_LAYERS.filter(l => !!map.getLayer(l));
  if (!layers.length) return null;
  const c = map.project(map.getCenter()), pad = 9;
  let feats;
  try {
    feats = map.queryRenderedFeatures(
      [[c.x - pad, c.y - pad], [c.x + pad, c.y + pad]], { layers });
  } catch (_) { return null; }
  return feats.length ? feats[0] : null;
}

function updateCenterBanner() {
  if (!map) { _snapChip.classList.remove('visible'); _crosshair.classList.remove('visible'); return; }
  // The center target is always on once the map is up — it marks the point the
  // banner reads (the nearest projected street), independent of any hit.
  _crosshair.classList.add('visible');
  const f = _centerFeature();
  if (!f) { _snapChip.classList.remove('visible'); return; }
  const props = f.properties || {};
  const isPoly = props.render_type === 'polygon';
  const street = props.street || '';
  const lines  = PMTILES_MODE ? tileSchedLines(props) : [];
  let head;
  if (isPoly) {
    head = `📍 Zone: ${esc(street)}`;
  } else {
    const d = _geomDistM(map.getCenter(), f.geometry);
    const dtxt = d == null ? '' : (d < 1 ? '<1 m' : `${Math.round(d)} m`);
    head = `📍 ${esc(street)}${dtxt ? ' — ' + dtxt : ''}`;
  }
  const sched = lines.join('  ·  ');
  _snapChip.innerHTML = `<span class="sc-head">${head}</span>`
    + (sched ? `<span class="sc-sched">${sched}</span>` : '');
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
    if (placingCar) { commitPlacement(e.lngLat.lat, e.lngLat.lng); return; }
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

  // Center banner: re-read the street under the viewport center when panning
  // stops, and once more after tiles finish streaming in (idle).
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

// Canonical schedule lines for a tile feature (day-first, "Every <Wd>" merge,
// Mon->Sun order, merged next-cluster dates) — identical to the card and the
// server hover. Shared by the hover tooltip and the center banner.
function tileSchedLines(props) {
  let sched = [];
  try { sched = JSON.parse(props.sched || '[]'); } catch (_) {}
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  const evens = BroomUrgency.formatScheduleSide(sched.filter(e => e.side === 'even'), now);
  const odds  = BroomUrgency.formatScheduleSide(sched.filter(e => e.side === 'odd'), now);
  if (evens.length && odds.length && evens.join('|') === odds.join('|')) return evens.slice();
  if (!evens.length && !odds.length) return [];
  return [...evens.map(e => 'Even: ' + e), ...odds.map(o => 'Odd: ' + o)];
}

function tileHoverHtml(props) {
  const lines = tileSchedLines(props);
  // No real schedule (only N/A / no-sweep) → no hover at all.
  return lines.length ? `<b>${esc(props.street || '')}</b><br>${lines.join('<br>')}` : '';
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
  const { lat, lon } = _gpsLocPin;
  hideGpsPinPopup();
  openNameBox(lat, lon);
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

// ── Card schedule detail window (toggle, no arrow) ─────────────────────────────
// Same content as a street/ward click, shown as a fixed panel instead of a
// map-anchored popup. Clicking the same card's header again closes it.
let _cardDetailCarId = null;
function openCardDetail(carId, html) {
  const panel = document.getElementById('card-detail');
  document.getElementById('card-detail-body').innerHTML = html;
  panel.style.display = 'block';
  _cardDetailCarId = carId;
}
function closeCardDetail() {
  document.getElementById('card-detail').style.display = 'none';
  _cardDetailCarId = null;
}
function toggleCardDetail(carId, html) {
  if (_cardDetailCarId === carId) closeCardDetail();
  else openCardDetail(carId, html);
}
document.getElementById('btn-card-detail-close').addEventListener('click', closeCardDetail);

// Dismiss whatever transient map window is open — the click-away analogue of Esc.
function dismissMapWindows() {
  if (_gpsLocPin) hideGpsPinPopup();
  closeZoneDetail();
  closeCardDetail();
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
  // Click outside the name box cancels it. Map-canvas clicks are excluded here
  // and handled by the map 'click' listener so the opening tap can't self-close.
  if (_namePopup
      && !e.target.closest('.maplibregl-popup')
      && !e.target.closest('#ctx-menu')
      && !e.target.closest('#gps-pin-popup')
      && !e.target.closest('#btn-add-car')
      && !e.target.closest('#map')) {
    closeNameBox();
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
  if (_namePopup)                               { closeNameBox();     dismissed = true; }
  if (_cardDetailCarId)                         { closeCardDetail();  dismissed = true; }
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
  if (session) {
    try {
      const res = await apiFetch('/prefs');
      if (res.ok) {
        const prefs = await res.json();
        cars = prefs.cars || [];
        if (prefs.home_lat != null && prefs.home_lon != null) {
          home = { lat: prefs.home_lat, lon: prefs.home_lon, address: prefs.home_address || '' };
        }
      }
    } catch (_) {}
    return;
  }
  // Guest — restore from sessionStorage (cleared when the tab closes).
  const g = _loadGuestPrefs();
  cars = g.cars || [];
  if (g.home_lat != null && g.home_lon != null) {
    home = { lat: g.home_lat, lon: g.home_lon, address: g.home_address || '' };
  }
}

async function savePrefs() {
  const payload = {
    cars,
    home_lat: home?.lat ?? null,
    home_lon: home?.lon ?? null,
    home_address: home?.address ?? null,
  };
  if (!session) { _saveGuestPrefs(payload); return; }  // guest — sessionStorage only
  try {
    await apiFetch('/prefs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
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
    openNameBox(lat, lon);
  }
}

btnCancelPlace.addEventListener('click', stopPlacing);

// "+ Add car" — enters tap-to-place mode (works on touch where right-click can't).
document.getElementById('btn-add-car').addEventListener('click', () => {
  closeNameBox();
  startPlacing(null);
});

// ── Context menu ──────────────────────────────────────────────────────────────
function showCtxMenu(x, y) {
  // Hide "Set home here" when a home is already saved — edit it via its card.
  ctxAddHome.style.display = home ? 'none' : '';
  const mw = 170, mh = home ? 44 : 80;
  ctxMenu.style.left = Math.min(x, window.innerWidth  - mw) + 'px';
  ctxMenu.style.top  = Math.min(y, window.innerHeight - mh) + 'px';
  ctxMenu.style.display = 'block';
}
function hideCtxMenu() { ctxMenu.style.display = 'none'; }

ctxAddCar.addEventListener('click', () => {
  hideCtxMenu();
  openNameBox(pendingLat, pendingLon);
});

ctxAddHome.addEventListener('click', () => {
  hideCtxMenu();
  setHomeFromCoords(pendingLat, pendingLon);
});

// ── Name box (arrow popup at the pending location) ─────────────────────────────
// Anchored MapLibre popup whose tip points at the spot the car will sit, matching
// the ward/street detail box style. Drops a temp pin under the tip.
function openNameBox(lat, lon) {
  if (!map) return;
  pendingLat = lat; pendingLon = lon;
  addTempPin(lat, lon);
  closeNameBox(true);  // clear any prior box without wiping the pending coords

  const row = document.createElement('div');
  row.className = 'np-row';
  const input = document.createElement('input');
  input.type = 'text'; input.id = 'name-panel-input';
  input.placeholder = 'Car name…'; input.maxLength = 32;
  input.value = defaultCarName();
  const save = document.createElement('button');
  save.id = 'btn-np-save'; save.title = 'Save'; save.setAttribute('aria-label', 'Save');
  save.textContent = '✓';
  const close = document.createElement('button');
  close.className = 'popup-close inline-close';
  close.title = 'Close (Esc)'; close.setAttribute('aria-label', 'Close');
  close.textContent = '✕';
  row.append(input, save, close);

  save.addEventListener('click', savePendingCar);
  close.addEventListener('click', () => closeNameBox());
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); savePendingCar(); }
    if (e.key === 'Escape') { e.preventDefault(); closeNameBox(); }
  });

  _nameInput = input;
  _namePopup = new maplibregl.Popup({
    closeButton: false, closeOnClick: false, anchor: 'bottom', offset: 22,
    className: 'name-popup',
  }).setLngLat([lon, lat]).setDOMContent(row).addTo(map);
  _namePopup.on('close', () => { _namePopup = null; _nameInput = null; });
  setTimeout(() => { input.focus(); input.select(); }, 30);
}

// keepPending=true tears down the popup/pin but leaves pendingLat/Lon set (used
// when re-opening or right before savePendingCar consumes them).
function closeNameBox(keepPending = false) {
  if (_namePopup) { _namePopup.remove(); _namePopup = null; }
  _nameInput = null;
  if (!keepPending) {
    removeTempPin();
    pendingLat = pendingLon = null;
  }
}

async function savePendingCar() {
  if (pendingLat === null) return;
  const name = (_nameInput?.value.trim()) || 'My car';
  const id = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36);
  cars.push({ id, name, lat: pendingLat, lon: pendingLon });
  removeTempPin();
  closeNameBox(true);
  if (_gpsLocPin) hideGpsPinPopup();
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
  let i = 1;
  while (used.has(`Car ${i}`)) i++;
  return `Car ${i}`;
}

// ── Cars panel ────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Worst-case urgency across all domains (today > tomorrow > safe). Drives the
// card tint/dot so a trash-today still flags a car whose sweeping is clear.
const _URG_RANK = { today: 2, tomorrow: 1, safe: 0 };
function panelUrgency(sched) {
  let best = (sched?.urgency && sched.urgency !== false) ? sched.urgency : 'safe';
  for (const d of (sched?.domains || [])) {
    if ((_URG_RANK[d.urgency] || 0) > (_URG_RANK[best] || 0)) best = d.urgency;
  }
  return best;
}

// One card block for a non-sweeping domain (trash, events, …): server-formatted
// schedule_lines under the domain label, with its own urgency line.
function domainBlockHTML(d) {
  const u = d.urgency || 'safe';
  const color = u === 'today' ? '#ef4444' : u === 'tomorrow' ? '#f97316' : '#2563eb';
  const label = u === 'today' ? '🚨 Today' : u === 'tomorrow' ? '⚠️ Tomorrow' : '✅ Clear';
  let lines = (d.schedule_lines || []).slice(0, 4);
  if (!lines.length) lines = ['No schedule'];
  const items = lines.map(l => `<div class="ce-sched-item">${esc(l)}</div>`).join('');
  return `<div class="ce-sched-urgency" style="color:${color}">${esc(label)}</div>`
       + `<div class="ce-sched-header">${esc(d.label)}:</div>`
       + items;
}

function scheduleHTML(sched) {
  if (!sched) return '<span style="color:var(--muted)">Loading…</span>';

  const domains = sched.domains || null;
  // Legacy server (no domains[]) always carries sweeping in the top-level fields.
  const hasSweeping = !domains || domains.some(d => d.id === 'sweeping');
  let html = '';

  if (hasSweeping) {
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

    // Header opens the full-year detail window when the server supplied one.
    const hasDetail = !!sched.detail_html;
    const headerCls = 'ce-sched-header' + (hasDetail ? ' clickable' : '');
    const chevron   = hasDetail ? ' <span class="ce-sched-chevron">▸</span>' : '';
    html += `<div class="ce-sched-urgency" style="color:${urgColor}">${urgLabel}</div>`
          + `<div class="${headerCls}">Street sweeping schedule:${chevron}</div>`
          + itemsHTML;
  }

  for (const d of (domains || [])) {
    if (d.id === 'sweeping') continue;
    html += domainBlockHTML(d);
  }

  return html || '<span style="color:var(--muted)">No schedule</span>';
}

function renderCarsPanel() {
  const ae = document.activeElement;
  if (carsPanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  for (const el of [...carsPanel.querySelectorAll('.car-entry')]) el.remove();
  cars.forEach((car, i) => {
    const color   = carColor(i);
    const sched   = carSchedules[car.id];
    const urgency = panelUrgency(sched);
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
      if (e.target.closest('button') || e.target.closest('[contenteditable="true"]')
          || e.target.closest('.ce-sched-header.clickable')) return;
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

    // ── Schedule header → toggle full-year detail window ──
    if (sched?.detail_html) {
      const hdr = entry.querySelector('.ce-sched-header.clickable');
      hdr?.addEventListener('click', e => {
        e.stopPropagation();
        toggleCardDetail(car.id, sched.detail_html);
      });
    }

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
      if (_cardDetailCarId === car.id) closeCardDetail();
      await savePrefs();
      updateCarMarkers();
      renderCarsPanel();
      if (!cars.length) setStatus('idle', 'Add a car to check street sweeping.');
      else updateStatusFromSchedules();
    });

    carsPanel.appendChild(entry);
  });
}

// ── Home (residence) — trash/recycling day ─────────────────────────────────────
function homeScheduleHTML() {
  if (!home) {
    return '<div class="ce-sched-item" style="color:var(--muted)">'
         + 'Add your home address to see trash &amp; recycling day.</div>';
  }
  if (!homeSchedule) return '<span style="color:var(--muted)">Loading…</span>';
  const domains = homeSchedule.domains || [];
  if (!domains.length) {
    return '<div class="ce-sched-item" style="color:var(--muted)">'
         + 'No collection info for this address.</div>';
  }
  return domains.map(domainBlockHTML).join('');
}

function renderHomePanel() {
  if (!homePanel) return;
  // Don't wipe an open address editor (inline input or contenteditable) mid-type.
  const ae = document.activeElement;
  if (homePanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT')) return;
  homePanel.innerHTML = '';

  // No home yet → a single "Add home" button (mirrors "+ Add car"). The map
  // equivalent is the right-click "Set home here" context-menu item.
  if (!home) {
    const btn = document.createElement('button');
    btn.id = 'btn-add-home';
    btn.className = 'add-car-btn add-home-btn';
    btn.textContent = '🏠 Add home';
    btn.title = 'Add your home to see trash & recycling day';
    btn.addEventListener('click', openHomeAddressInput);
    homePanel.appendChild(btn);
    return;
  }

  const urgency  = panelUrgency(homeSchedule);
  const addrText = home.address ? abbreviate(home.address) : '';

  const entry = document.createElement('div');
  entry.className = 'car-entry home-entry';
  entry.dataset.urgency = urgency;
  entry.style.cssText = '--car-color:#16a34a;--urg-color:#16a34a';
  entry.innerHTML = `
    <div class="ce-header">
      <span class="ce-dot ce-dot-home">🏠</span>
      <span class="ce-name">Home</span>
      <button class="ce-remove" title="Remove home">✕</button>
    </div>
    <div class="ce-addr" contenteditable="false" title="Double-click to edit your address">${esc(addrText)}</div>
    <div class="ce-sched">${homeScheduleHTML()}</div>`;

  // ── Address editing → geocode + refresh trash ──
  const addrEl = entry.querySelector('.ce-addr');
  addrEl.addEventListener('dblclick', () => {
    addrEl.contentEditable = 'true';
    addrEl.focus();
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
    if (!q || q === addrText) { renderHomePanel(); return; }
    await setHomeFromAddress(q);
  });

  entry.querySelector('.ce-remove').addEventListener('click', removeHome);

  homePanel.appendChild(entry);
}

// "Add home" button → inline address input in the panel (mirrors the car name
// box; typing an address geocodes it and runs the trash lookup).
function openHomeAddressInput() {
  homePanel.innerHTML = '';
  const row = document.createElement('div');
  row.className = 'home-input-row';
  const input = document.createElement('input');
  input.type = 'text'; input.id = 'home-addr-input';
  input.placeholder = 'Home address…';
  const save = document.createElement('button');
  save.className = 'ce-btn'; save.textContent = '✓'; save.title = 'Save';
  const cancel = document.createElement('button');
  cancel.className = 'popup-close inline-close';
  cancel.textContent = '✕'; cancel.title = 'Cancel';
  row.append(input, save, cancel);

  const submit = async () => {
    const q = input.value.trim();
    if (!q) { renderHomePanel(); return; }
    await setHomeFromAddress(q);
  };
  save.addEventListener('click', submit);
  cancel.addEventListener('click', renderHomePanel);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); submit(); }
    if (e.key === 'Escape') { e.preventDefault(); renderHomePanel(); }
  });

  homePanel.appendChild(row);
  setTimeout(() => input.focus(), 30);
}

// Geocode a typed address → home pin + ReCollect lookup (keeps the typed text
// as home.address since ReCollect matches the user's address string best).
async function setHomeFromAddress(query) {
  try {
    const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(query)}`);
    const hits = await res.json();
    if (!hits.length) { showToast('Address not found', true); renderHomePanel(); return; }
    home = { lat: parseFloat(hits[0].lat), lon: parseFloat(hits[0].lon), address: query };
  } catch (_) { showToast('Could not look up address', true); renderHomePanel(); return; }
  await savePrefs();
  updateHomeMarker();
  renderHomePanel();
  await checkHome();
}

// Right-click "Set home here" → drop the home pin at the clicked point. Zone-based
// trash resolves straight from the coordinate; address-based (ReCollect) cities
// need the user to type the address into the card afterward, since reverse
// geocoding lives in the backend, not the frontend.
async function setHomeFromCoords(lat, lon) {
  home = { lat, lon, address: '' };
  await savePrefs();
  updateHomeMarker();
  renderHomePanel();
  await checkHome();
}

async function removeHome() {
  home = null; homeSchedule = null;
  if (_homeMarker) { _homeMarker.remove(); _homeMarker = null; }
  await savePrefs();
  renderHomePanel();
}

function updateHomeMarker() {
  if (!map) return;
  if (!home) { if (_homeMarker) { _homeMarker.remove(); _homeMarker = null; } return; }
  if (_homeMarker) { _homeMarker.setLngLat([home.lon, home.lat]); return; }
  const el = document.createElement('div');
  el.className = 'home-marker';
  el.textContent = '🏠';
  el.title = 'Home';
  _homeMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
    .setLngLat([home.lon, home.lat]).addTo(map);
}

async function checkHome() {
  if (!home) return;
  try {
    // Region is derived server-side from the home coordinate — a home can sit
    // in a different region than the map's currently selected one.
    const res = await apiFetch('/check-home', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: home.lat, lon: home.lon, address: home.address }),
    });
    if (res.ok) { homeSchedule = await res.json(); renderHomePanel(); }
  } catch (_) {}
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
btnLocate.addEventListener('click', () => {
  const btn = btnLocate;
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
