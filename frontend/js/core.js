// Every urgency surface reads this one table: card colour (CSS vars in
// styles.css), labels, and the map paint (MapLibre needs literal colours).
const URGENCY = {
  today:    { color: 'var(--urg-today)',    short: 'Today', icon: 'alert',
              fill: 'rgba(220,60,60,0.55)',  border: 'rgba(220,60,60,0.90)',  line: 'tomato' },
  tomorrow: { color: 'var(--urg-tomorrow)', short: 'Tomorrow', icon: 'alert',
              fill: 'rgba(230,130,20,0.40)', border: 'rgba(230,130,20,0.80)', line: 'orange' },
  // Clear is quiet (a muted green): only today and tomorrow should catch the eye.
  safe:     { color: 'var(--urg-safe)',     short: 'Clear', icon: 'check',
              fill: 'rgba(63,140,92,0.12)',  border: 'rgba(63,140,92,0.35)',  line: 'rgba(63,140,92,0.6)' },
  // Map only: a street or zone with no sweeping at all (not a car verdict).
  none:     { color: 'var(--muted)',       short: 'No sweeping',
              fill: 'rgba(120,130,150,0.10)', border: 'rgba(120,130,150,0.35)', line: '#94a3b8' },
};

const DEFAULT_CENTER = { lat: 38, lon: -96, zoom: 4 };  // US overview — shown only if no car/IP data
const CAR_COLORS = ['#3b82f6','#10b981','#a855f7','#06b6d4','#ec4899','#84cc16','#6366f1','#22d3ee'];
const HOME_COLOR = '#15803d';
// A car keeps the colour it was given (saved with it), so removing one never repaints the others.
function carColor(car) { return car.color || CAR_COLORS[0]; }
function nextCarColor() {
  const used = new Set(cars.map(c => c.color));
  return CAR_COLORS.find(c => !used.has(c)) || CAR_COLORS[cars.length % CAR_COLORS.length];
}

// ── Shared utilities ──────────────────────────────────────────────────────────
// This device's settings. Storage can be off (private browsing, blocked site data): then
// nothing is remembered, and nothing breaks. _store(key, null) forgets one.
const _read = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
const _stored = (key, allowed, fallback) => { const v = _read(key); return allowed.includes(v) ? v : fallback; };
const _store = (key, v) => {
  try { if (v === null) localStorage.removeItem(key); else localStorage.setItem(key, v); } catch (_) {}
};
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

function newId() {
  return crypto.randomUUID ? crypto.randomUUID()
                           : Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// Typed address -> {lat, lon}, searched in `region` (the server bounds it to that
// region's cities); null when not found; throws when the lookup is unavailable.
async function geocode(q, region) {
  const res = await fetch('/api/geocode?' + new URLSearchParams({ q, ...(region ? { region } : {}) }));
  if (!res.ok) throw new Error(res.statusText);
  const hit = await res.json();
  return hit.lat == null ? null : hit;
}

// What applies at a car (permit area, meter, …) or a home (fire, evacuation zone): id ->
// [{theme, label, rule, asof, city}] from /api/rules; refetched when the item moves, and
// after RULES_TTL_MS (closures come and go). Returns whether new rules arrived.
const placeRules = {};
const _rulesAt = {};  // id -> "lat,lon@fetch time slot" the rules are for
const RULES_TTL_MS = 30 * 60 * 1000;
async function loadRules(item, kind) {
  const at = `${item.lat},${item.lon}@${Math.floor(Date.now() / RULES_TTL_MS)}`;
  if (_rulesAt[item.id] === at) return false;
  _rulesAt[item.id] = at;
  const q = new URLSearchParams({ lat: item.lat, lon: item.lon, kind });
  const region = regionAt(item.lat, item.lon);
  if (region) q.set('region', region);
  try {
    const res = await fetch('/api/rules?' + q);
    if (!res.ok) { delete _rulesAt[item.id]; return false; }
    if (_rulesAt[item.id] !== at) return false;  // moved meanwhile
    placeRules[item.id] = (await res.json()).rules || [];
    return true;
  } catch (_) { delete _rulesAt[item.id]; return false; }
}

// An item page's section of rules: label, what it means, and the source's date.
function rulesBlockHTML(header, rules) {
  if (!rules?.length) return '';
  const rows = rules.map(r => {
    const { text, detail } = ruleText(r);
    return `<div class="ce-rule${r.warn ? ' warn' : ''}"><b>${esc(text)}</b>${detail ? ` <span>${esc(detail)}</span>` : ''}</div>`;
  }).join('');
  return `<div class="sec-label">${esc(header)}</div><div class="sec">${rows}</div>`;
}

// A rule in a few words (the car's page, the street card): its label, and the first clause
// of its text only when that adds a number (hours, a limit, a rate). Explanations ("permit
// holders are exempt…", "see the meter") are left out.
function ruleText(r) {
  const text = r.theme === 'meters' ? 'Metered' : r.label;
  let detail = String(r.rule || '').split(/;|\. /)[0].replace(/\.$/, '').replace(/\s*\([^)]*\)/g, '')
    .replace(/^Metered:\s*(most meters\s*)?/i, '').trim();
  // "No oversized vehicles" + "No oversized vehicles, M–Su 12am–6am": the hours only.
  if (detail.toLowerCase().startsWith(text.toLowerCase())) detail = detail.slice(text.length).replace(/^[\s,:–-]+/, '');
  return { text, detail: /\d/.test(detail) ? detail : '' };
}

// Schedules for cars and homes: the latest request per id wins (a slow answer for an
// old position never overwrites a newer one), and a failure is remembered so the card
// offers Retry instead of "Loading…" forever. Returns the data, or null if it failed
// or was superseded; `onError` gets the message of a current failure.
const _loadSeq = new Map();
const loadFailed = new Set();  // car / home ids whose last load failed
async function loadSchedule(id, store, request, onError) {
  const n = (_loadSeq.get(id) || 0) + 1;
  _loadSeq.set(id, n);
  let data = null, err = null;
  try {
    const res = await request();
    if (res.ok) data = await res.json();
    else err = (await res.json().catch(() => ({}))).detail || res.statusText;
  } catch (_) { err = 'Couldn’t reach the server'; }
  if (_loadSeq.get(id) !== n) return null;
  if (err) { loadFailed.add(id); onError?.(err); return null; }
  loadFailed.delete(id);
  store[id] = data;
  return data;
}

// el.startEdit() edits it in place (from a menu), while canEdit(); Enter commits, Escape
// or empty/unchanged restores original().
function makeEditable(el, original, onCommit, canEdit = () => true) {
  el.startEdit = () => {
    if (!canEdit()) return;
    el.contentEditable = 'true'; el.focus();
    const r = document.createRange(); r.selectNodeContents(el);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  };
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
    if (e.key === 'Escape') { e.stopPropagation(); el.textContent = original(); el.blur(); }  // cancels the edit only
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
let placing        = null;   // while placing: { kind: 'car' | 'home', editId (null: a new one) }
let pendingLat     = null, pendingLon = null;
let carSchedules   = {};
let _tempPin        = null;   // {lat,lon} while naming a new car
let _selectedId     = null;   // the one selected car or home (its card is open)
let _region          = null;   // region key under the map center (null: none nearby)
const REGION_RADIUS  = 1.5;    // degrees: a region applies this close to its center
const AREA_ZOOM      = 9;      // below this the map shows area labels instead of streets
const isDesktop = () => window.matchMedia('(min-width: 768px), (orientation: landscape) and (max-height: 500px)').matches;  // = styles.css desktop layout

// Region whose center is nearest (lat, lon) within `radius` degrees (default: any distance); else null.
function nearestRegion(lat, lon, radius = Infinity) {
  let best = null, bestD = radius ** 2;
  for (const [key, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestD) { bestD = d; best = key; }
  }
  return best;
}
// Region that applies at (lat, lon): its center is within REGION_RADIUS; else null.
function regionAt(lat, lon) { return nearestRegion(lat, lon, REGION_RADIUS); }

// The one clock for sweeping and trash days: {y, m, d, min} in the region's
// timezone (the browser's until /cities has loaded), now or at the Date `when`.
function regionNow(regionKey, when) { return BroomUrgency.nowForTimeZone(regions[regionKey]?.tz, when); }

// A city's no-sweep holidays ({dayKey: name}; null: none known), from /cities.
function sweepSkip(regionKey, city) {
  return BroomUrgency.holidaySkip(regions[regionKey]?.holidays?.[city], 'sweeping');
}
// "Oakland" for a city key ('' when unknown).
function cityName(city) {
  for (const r of Object.values(regions)) if (r.cities?.[city]) return r.cities[city];
  return '';
}
// The same for a /check response (the car's own region and city).
function schedSkip(sched) { return sched ? sweepSkip(sched.region || _region, sched.city) : null; }

// ── Map (MapLibre) ────────────────────────────────────────────────────────────
let map = null;
const _carMarkers  = new Map();  // carId → maplibregl.Marker
let homes          = [];         // [{ id, lat, lon, address }] — saved residences
let homeSchedules  = {};         // homeId → /check-home response
const _homeMarkers = new Map();  // homeId → maplibregl.Marker for the home pin
let _tempPinMarker = null;
let _namePopup     = null;  // MapLibre popup (arrow box) for naming a new car
let _nameInput     = null;  // <input> inside the active name popup

const ZONE_LAYERS  = ['zones-fill', 'zones-active', 'zones-outline', 'zones-ward', 'zones-line', 'zones-side-even', 'zones-side-odd'];
const HOVER_LAYERS = ['zones-fill', 'zones-line'];

// Vector tile source; per-feature urgency arrives via feature-state.
const TILES_SOURCE     = 'zones-tiles';
const TILES_SRC_LAYER  = 'zones';
function _urgCase(prop, key = 'urgency') {
  // MapLibre expression: pick colour from feature-state `key` ('urgency', or one side's
  // 'urgency_even' / 'urgency_odd'). A feature whose urgency is not computed yet stays
  // transparent rather than flashing "Clear" blue.
  return [
    'match', ['coalesce', ['feature-state', key], ''],
    'today',    URGENCY.today[prop],
    'tomorrow', URGENCY.tomorrow[prop],
    'safe',     URGENCY.safe[prop],
    'none',     URGENCY.none[prop],
    'rgba(0,0,0,0)',
  ];
}
// Street line width scales with zoom so it stays thin on city-wide views; it also
// grows with urgency (today x2, tomorrow x1.5; no sweeping a hairline) so colour is not the only cue.
// One side's line (key 'urgency_even' / 'urgency_odd') is `scale` of that.
const LINE_W = { 11: 0.8, 14: 1.5, 16: 2.5, 18: 4 };
const LINE_MULT = { today: 2, tomorrow: 1.5, safe: 1, none: 0.35 };
function _lineWidth(key = 'urgency', scale = 1) {
  const stops = Object.entries(LINE_W).flatMap(([z, w]) => [Number(z), [
    'case',
    ['==', ['feature-state', key], 'today'],    w * scale * LINE_MULT.today,
    ['==', ['feature-state', key], 'tomorrow'], w * scale * LINE_MULT.tomorrow,
    ['==', ['feature-state', key], 'none'],     w * scale * LINE_MULT.none,
    w * scale,
  ]]);
  return ['interpolate', ['linear'], ['zoom'], ...stops];
}

const LIGHT_STYLE = 'https://tiles.openfreemap.org/styles/positron';
const DARK_STYLE  = 'https://tiles.openfreemap.org/styles/dark';
// Appearance: 'auto' follows the system; 'light' / 'dark' pin it (menu).
const _sysDark = window.matchMedia('(prefers-color-scheme: dark)');
const _themePref = () => _stored('bb_theme', ['light', 'dark'], 'auto');
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
const itemList       = document.getElementById('item-list');  // cars, then homes
const sheet          = document.getElementById('sheet');
const savedSummary   = document.getElementById('saved-summary');
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
