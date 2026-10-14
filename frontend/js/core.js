// Every urgency surface reads this one table: card colour (CSS vars in
// styles.css), labels, and the map paint (MapLibre needs literal colours).
const URGENCY = {
  today:    { color: 'var(--urg-today)',    short: '🚨 Today',
              fill: 'rgba(220,60,60,0.55)',  border: 'rgba(220,60,60,0.90)',  line: 'tomato' },
  tomorrow: { color: 'var(--urg-tomorrow)', short: '⚠️ Tomorrow',
              fill: 'rgba(230,130,20,0.40)', border: 'rgba(230,130,20,0.80)', line: 'orange' },
  safe:     { color: 'var(--urg-safe)',     short: '✅ Clear',
              fill: 'rgba(80,110,180,0.18)', border: 'rgba(80,110,180,0.40)', line: 'cornflowerblue' },
};

const DEFAULT_CENTER = { lat: 38, lon: -96, zoom: 4 };  // US overview — shown only if no car/IP data
const CAR_COLORS = ['#3b82f6','#10b981','#a855f7','#06b6d4','#ec4899','#84cc16','#6366f1','#22d3ee'];
function carColor(idx) { return CAR_COLORS[idx % CAR_COLORS.length]; }

// ── Shared utilities ──────────────────────────────────────────────────────────
// Inline SVG icon from the sprite in index.html (stroke follows text colour).
function icon(name) { return `<svg class="ic"><use href="#i-${name}"/></svg>`; }

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// POST a JSON body; returns the Response.
function postJSON(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// POST /check for a lat/lon (the server picks the region if none is nearby); returns the Response.
function postCheck(lat, lon) {
  return postJSON('/check', { lat, lon, region: regionAt(lat, lon) || undefined });
}

function newId() {
  return crypto.randomUUID ? crypto.randomUUID()
                           : Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// Nominatim forward geocode: {lat, lon} or null when not found; throws on network error.
async function geocode(q) {
  const res  = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`);
  const hits = await res.json();
  return hits.length ? { lat: parseFloat(hits[0].lat), lon: parseFloat(hits[0].lon) } : null;
}

// Double-click (or el.startEdit()) to edit; Enter commits, Escape or empty/unchanged restores original().
function makeEditable(el, original, onCommit) {
  el.startEdit = () => {
    el.contentEditable = 'true'; el.focus();
    const r = document.createRange(); r.selectNodeContents(el);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  };
  el.addEventListener('dblclick', el.startEdit);
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

// ── State ─────────────────────────────────────────────────────────────────────
let cars           = [];
let activeCarId    = null;
let regions        = {};         // regionKey → {name, center, zoom, tz} from /cities
let placingCar     = false;
let placingHome    = false;
let placingEditId  = null;
let pendingLat     = null, pendingLon = null;
let carSchedules   = {};
let _tempPin        = null;   // {lat,lon} while naming a new car
let _selectedCarId  = null;
let _gpsLocPin = null;
let _gpsLocPinTimer = null;
let _hoverSuppressed = false;
let _region          = null;   // region key under the map center (null: none nearby)
const REGION_RADIUS  = 1.5;    // degrees: a region applies this close to its center
const AREA_ZOOM      = 9;      // below this the map shows area labels instead of streets
const isDesktop = () => window.matchMedia('(min-width: 768px)').matches;

// Region whose center is within REGION_RADIUS of (lat, lon), nearest first; else null.
function regionAt(lat, lon) {
  let best = null, bestD = REGION_RADIUS ** 2;
  for (const [key, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestD) { bestD = d; best = key; }
  }
  return best;
}

// ── Map (MapLibre) ────────────────────────────────────────────────────────────
let map = null;
const _carMarkers  = new Map();  // carId → maplibregl.Marker
let homes          = [];         // [{ id, lat, lon, address }] — saved residences
let homeSchedules  = {};         // homeId → /check-home response
const _homeMarkers = new Map();  // homeId → maplibregl.Marker for the home pin
let _gpsMarker     = null;
let _tempPinMarker = null;
let _zonePopup     = null;  // MapLibre popup for clicked zone detail
let _namePopup     = null;  // MapLibre popup (arrow box) for naming a new car
let _nameInput     = null;  // <input> inside the active name popup

const ZONE_LAYERS  = ['zones-fill', 'zones-outline', 'zones-ward', 'zones-line'];
const HOVER_LAYERS = ['zones-fill', 'zones-line'];

// Vector tile source; per-feature urgency arrives via feature-state.
const TILES_SOURCE     = 'zones-tiles';
const TILES_SRC_LAYER  = 'zones';
function _urgCase(prop) {
  // MapLibre expression: pick colour from feature-state 'urgency' (default safe).
  return [
    'case',
    ['==', ['feature-state', 'urgency'], 'today'],    URGENCY.today[prop],
    ['==', ['feature-state', 'urgency'], 'tomorrow'], URGENCY.tomorrow[prop],
    URGENCY.safe[prop],
  ];
}
// Street line width scales with zoom so it stays thin on city-wide views; it also
// grows with urgency (today x2, tomorrow x1.5) so colour is not the only cue.
const LINE_W = { 11: 0.8, 14: 1.5, 16: 2.5, 18: 4 };
const LINE_MULT = { today: 2, tomorrow: 1.5, safe: 1 };
function _lineWidth() {
  const stops = Object.entries(LINE_W).flatMap(([z, w]) => [Number(z), [
    'case',
    ['==', ['feature-state', 'urgency'], 'today'],    w * LINE_MULT.today,
    ['==', ['feature-state', 'urgency'], 'tomorrow'], w * LINE_MULT.tomorrow,
    w,
  ]]);
  return ['interpolate', ['linear'], ['zoom'], ...stops];
}

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
const btnLocate      = document.getElementById('btn-locate');
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
// Collapses to a handle (default) so the map + placement center-target stay clear. The
// expanded/collapsed state persists.
let _sheetCollapsed = localStorage.getItem('bb_sheet_v2') !== 'open';
// Empty (no car or home, not placing): only the two add buttons, no minimize.
function applySheet() {
  const empty = !cars.length && !homes.length && !placingCar && !placingHome;
  sheet.classList.toggle('empty', empty);
  sheet.classList.toggle('collapsed', _sheetCollapsed && !empty);
}
function setSheetCollapsed(v) {
  _sheetCollapsed = !!v;
  localStorage.setItem('bb_sheet_v2', _sheetCollapsed ? 'closed' : 'open');
  applySheet();
}
// Handle text: the most urgent car verdict, else counts.
function updateSheetSummary() {
  const { today, tomorrow } = urgentCars();
  sheetSummary.textContent = today.length ? `🚨 Move ${today.join(', ')} today`
    : tomorrow.length ? `⚠️ Move ${tomorrow.join(', ')} tomorrow`
    : cars.length ? '✅ All clear'
    : homes.length ? `🏠 ${homes.length}` : 'Cars & homes';
  applySheet();
}
sheetHandle.addEventListener('click', () => setSheetCollapsed(!_sheetCollapsed));
sheetHandle.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheetCollapsed(!_sheetCollapsed); }
});
applySheet();

// Sheet height lifts the map controls above it on mobile.
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--sheet-h', sheet.offsetHeight + 'px');
}).observe(sheet);
