// Every urgency surface reads this one table: card colour (CSS vars in
// styles.css), labels, and the map paint (MapLibre needs literal colours).
const URGENCY = {
  today:    { color: 'var(--urg-today)',    short: 'Today', icon: 'alert',
              fill: 'rgba(220,60,60,0.55)',  border: 'rgba(220,60,60,0.90)',  line: 'tomato' },
  tomorrow: { color: 'var(--urg-tomorrow)', short: 'Tomorrow', icon: 'alert',
              fill: 'rgba(230,130,20,0.40)', border: 'rgba(230,130,20,0.80)', line: 'orange' },
  safe:     { color: 'var(--urg-safe)',     short: 'Clear', icon: 'check',
              fill: 'rgba(80,110,180,0.18)', border: 'rgba(80,110,180,0.40)', line: 'cornflowerblue' },
  // Map only: a street or zone with no sweeping at all (not a car verdict).
  none:     { color: 'var(--muted)',       short: 'No sweeping',
              fill: 'rgba(120,130,150,0.10)', border: 'rgba(120,130,150,0.35)', line: '#94a3b8' },
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
  return postJSON('/api/check', { lat, lon, region: regionAt(lat, lon) || undefined });
}

// Two-step button: the first tap arms it ("Confirm?" for 3 s), the second runs `action`.
function confirmTap(btn, action) {
  let timer = null;
  const label = btn.innerHTML;
  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (btn.classList.toggle('armed')) {
      btn.innerHTML = `${icon('trash')} Confirm?`;
      timer = setTimeout(() => { btn.classList.remove('armed'); btn.innerHTML = label; }, 3000);
      return;
    }
    clearTimeout(timer);
    action();
  });
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
let _region          = null;   // region key under the map center (null: none nearby)
const REGION_RADIUS  = 1.5;    // degrees: a region applies this close to its center
const AREA_ZOOM      = 9;      // below this the map shows area labels instead of streets
const isDesktop = () => window.matchMedia('(min-width: 768px), (orientation: landscape) and (max-height: 500px)').matches;  // = styles.css desktop layout

// Region whose center is within REGION_RADIUS of (lat, lon), nearest first; else null.
function regionAt(lat, lon) {
  let best = null, bestD = REGION_RADIUS ** 2;
  for (const [key, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestD) { bestD = d; best = key; }
  }
  return best;
}

// The one clock for sweeping and trash days: {y, m, d, min} in the region's
// timezone (the browser's until /cities has loaded).
function regionNow(regionKey) { return BroomUrgency.nowForTimeZone(regions[regionKey]?.tz); }

// Region whose center is nearest (lat, lon), however far; null if there are none.
function nearestRegion(lat, lon) {
  let best = null, bestD = Infinity;
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
let _namePopup     = null;  // MapLibre popup (arrow box) for naming a new car
let _nameInput     = null;  // <input> inside the active name popup

const ZONE_LAYERS  = ['zones-fill', 'zones-outline', 'zones-ward', 'zones-line'];
const HOVER_LAYERS = ['zones-fill', 'zones-line'];

// Vector tile source; per-feature urgency arrives via feature-state.
const TILES_SOURCE     = 'zones-tiles';
const TILES_SRC_LAYER  = 'zones';
function _urgCase(prop) {
  // MapLibre expression: pick colour from feature-state 'urgency'. A feature whose
  // urgency is not computed yet stays transparent rather than flashing "Clear" blue.
  return [
    'match', ['coalesce', ['feature-state', 'urgency'], ''],
    'today',    URGENCY.today[prop],
    'tomorrow', URGENCY.tomorrow[prop],
    'safe',     URGENCY.safe[prop],
    'none',     URGENCY.none[prop],
    'rgba(0,0,0,0)',
  ];
}
// Street line width scales with zoom so it stays thin on city-wide views; it also
// grows with urgency (today x2, tomorrow x1.5; no sweeping x0.6) so colour is not the only cue.
const LINE_W = { 11: 0.8, 14: 1.5, 16: 2.5, 18: 4 };
const LINE_MULT = { today: 2, tomorrow: 1.5, safe: 1, none: 0.6 };
function _lineWidth() {
  const stops = Object.entries(LINE_W).flatMap(([z, w]) => [Number(z), [
    'case',
    ['==', ['feature-state', 'urgency'], 'today'],    w * LINE_MULT.today,
    ['==', ['feature-state', 'urgency'], 'tomorrow'], w * LINE_MULT.tomorrow,
    ['==', ['feature-state', 'urgency'], 'none'],     w * LINE_MULT.none,
    w,
  ]]);
  return ['interpolate', ['linear'], ['zoom'], ...stops];
}

const LIGHT_STYLE = 'https://tiles.openfreemap.org/styles/positron';
const DARK_STYLE  = 'https://tiles.openfreemap.org/styles/dark';
// Appearance: 'auto' follows the system; 'light' / 'dark' pin it (menu).
const _sysDark = window.matchMedia('(prefers-color-scheme: dark)');
function _themePref() {
  const v = localStorage.getItem('bb_theme');
  return v === 'light' || v === 'dark' ? v : 'auto';
}
function _wantDark() { const p = _themePref(); return p === 'auto' ? _sysDark.matches : p === 'dark'; }
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
const sheet          = document.getElementById('sheet');
const sheetHandle    = document.getElementById('sheet-handle');
const sheetSummary   = document.getElementById('sheet-summary');
const btnConfirmPlace = document.getElementById('btn-confirm-place');

// ── Screen height (iPhone home-screen app) ────────────────────────────────────
// There the app always fills the screen, but with a translucent status bar iOS can
// size the viewport (100%, and sometimes 100vh) a status bar short, leaving a strip at
// the bottom. The screen size is exact (iOS reports it in portrait).
if (navigator.standalone && /iPhone/.test(navigator.userAgent)) {
  const _landscape = window.matchMedia('(orientation: landscape)');
  const setAppH = () => document.documentElement.style.setProperty('--app-h',
    (_landscape.matches ? Math.min(screen.width, screen.height) : Math.max(screen.width, screen.height)) + 'px');
  setAppH();
  _landscape.addEventListener('change', setAppH);
}

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
// `remember` is false when the app closes the sheet itself (not the user's choice to keep).
function setSheetCollapsed(v, remember = true) {
  _sheetCollapsed = !!v;
  if (remember) localStorage.setItem('bb_sheet_v2', _sheetCollapsed ? 'closed' : 'open');
  // Phone: one big panel at a time (as in Google / Apple Maps), so the centre target
  // and the map buttons stay clear. Desktop has the room for both.
  if (!_sheetCollapsed && !isDesktop() && typeof _cal !== 'undefined' && _cal.expanded) collapseCal();
  applySheet();
}
// Handle text: the most urgent car verdict, else counts.
function updateSheetSummary() {
  const { today, tomorrow } = urgentCars();
  sheetSummary.innerHTML = today.length ? `<span class="urg-today">${icon('alert')}</span> Move ${esc(today.join(', '))} today`
    : tomorrow.length ? `<span class="urg-tomorrow">${icon('alert')}</span> Move ${esc(tomorrow.join(', '))} tomorrow`
    : cars.length ? `<span class="urg-safe">${icon('check')}</span> All clear`
    : homes.length ? `${icon('home')} ${homes.length}` : 'Cars & homes';
  applySheet();
}
sheetHandle.addEventListener('click', () => setSheetCollapsed(!_sheetCollapsed));
sheetHandle.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheetCollapsed(!_sheetCollapsed); }
});
applySheet();

// A drag that starts on a panel stays with it, even once the finger leaves it: the
// map listens for touch moves on the whole window.
for (const panel of [sheet, document.getElementById('info-card')]) {
  panel.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });
}

// Sheet height lifts the map controls above it on mobile.
// (In a frame, so one observer's update can't re-trigger the other within the same frame.)
new ResizeObserver(() => requestAnimationFrame(() => {
  document.documentElement.style.setProperty('--sheet-h', sheet.offsetHeight + 'px');
  if (typeof layoutSign === 'function') layoutSign();  // calendar.js may not have loaded yet
})).observe(sheet);
