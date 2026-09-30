// ── Config ────────────────────────────────────────────────────────────────────
const DEV_MODE      = window.DEV_MODE === true;
// PMTILES_MODE: render zones from static vector tiles + client-side urgency,
// instead of per-request GeoJSON from /check. See js/urgency.js.
const PMTILES_MODE  = window.PMTILES_MODE === true;
const REGION_TZ     = window.REGION_TZ || {};

const DEFAULT_CENTER = { lat: 38, lon: -96, zoom: 4 };  // US overview — shown only if no car/IP data
const CAR_COLORS = ['#3b82f6','#10b981','#a855f7','#06b6d4','#ec4899','#84cc16','#6366f1','#22d3ee'];
function carColor(idx) { return CAR_COLORS[idx % CAR_COLORS.length]; }

// ── Shared utilities ──────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function apiFetch(path, opts = {}) {
  const token = session?.access_token;
  return fetch(path, {
    ...opts,
    headers: { ...(opts.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
}

// POST /check for a lat/lon in the selected region; returns the Response.
function postCheck(lat, lon) {
  return apiFetch('/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lon, region: regionSelect.value || undefined }),
  });
}

// Nominatim forward geocode: {lat, lon} or null when not found; throws on network error.
async function geocode(q) {
  const res  = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`);
  const hits = await res.json();
  return hits.length ? { lat: parseFloat(hits[0].lat), lon: parseFloat(hits[0].lon) } : null;
}

// Double-click to edit; Enter commits, Escape or empty/unchanged restores original().
function makeEditable(el, original, onCommit) {
  el.addEventListener('dblclick', () => {
    el.contentEditable = 'true'; el.focus();
    const r = document.createRange(); r.selectNodeContents(el);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  });
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
    if (e.key === 'Escape') { el.textContent = original(); el.blur(); }
  });
  el.addEventListener('blur', () => {
    el.contentEditable = 'false';
    const v = el.textContent.trim();
    if (!v || v === original()) { el.textContent = original(); return; }
    onCommit(v);
  });
}

function setStatus(cls, text) { statusText.className = cls; statusText.textContent = text; }

// ── State ─────────────────────────────────────────────────────────────────────
let session        = null;
let cars           = [];
let activeCarId    = null;
let regions        = {};
let placingCar     = false;
let placingHome    = false;
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
let homes          = [];         // [{ id, lat, lon, address }] — saved residences
let homeSchedules  = {};         // homeId → /check-home response (home-subject domains[])
const _homeMarkers = new Map();  // homeId → maplibregl.Marker for the home pin
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
const placeBannerText = document.getElementById('place-banner-text');
const btnCancelPlace = document.getElementById('btn-cancel-place');
const ctxMenu        = document.getElementById('ctx-menu');
const ctxAddCar      = document.getElementById('ctx-add-car');
const ctxAddHome     = document.getElementById('ctx-add-home');
const carsPanel      = document.getElementById('cars-panel');
const homePanel      = document.getElementById('home-panel');
const customHoverEl  = document.getElementById('custom-hover');
const sheet          = document.getElementById('sheet');
const sheetHandle    = document.getElementById('sheet-handle');
const sheetSummary   = document.getElementById('sheet-summary');
const btnConfirmPlace = document.getElementById('btn-confirm-place');

// ── Bottom sheet (cars & homes) ───────────────────────────────────────────────
// Collapses to a handle so the map + placement center-target stay clear. The
// expanded/collapsed state persists; `body.sheet-expanded` gates the center band.
let _sheetCollapsed = localStorage.getItem('bb_sheet') === '1';
function applySheet() {
  sheet.classList.toggle('collapsed', _sheetCollapsed);
  document.body.classList.toggle('sheet-expanded', !_sheetCollapsed);
}
function setSheetCollapsed(v) {
  _sheetCollapsed = !!v;
  localStorage.setItem('bb_sheet', _sheetCollapsed ? '1' : '0');
  applySheet();
}
function updateSheetSummary() {
  const parts = [];
  if (cars.length)  parts.push(`🚗 ${cars.length}`);
  if (homes.length) parts.push(`🏠 ${homes.length}`);
  sheetSummary.textContent = parts.length ? parts.join('  ·  ') : 'Add a car or home';
}
sheetHandle.addEventListener('click', () => setSheetCollapsed(!_sheetCollapsed));
sheetHandle.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheetCollapsed(!_sheetCollapsed); }
});
applySheet();
