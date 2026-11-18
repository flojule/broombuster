// ── "Add here" menu: right-click, or a long press on touch (as in Google / Apple Maps) ──
// While placing, a mouse right-click moves the target there and sets the spot; a finger
// resting on the map (Android fires contextmenu for it) does nothing.
let _touchAt = -Infinity;  // time of the last touch on the map
const _fromTouch = e => (e.pointerType ? e.pointerType !== 'mouse' : !!e.sourceCapabilities?.firesTouchEvents)
  || e.timeStamp - _touchAt < 1000;
mapDiv.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (!map) return;
  const rect = mapDiv.getBoundingClientRect();
  const at = map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
  if (placing) {
    if (!_fromTouch(e)) { map.jumpTo({ center: at }); confirmPlacementAtCenter(); }
    return;
  }
  if (!_zoomToPlace(at)) openCtxMenu(at, e.clientX, e.clientY);
});
// Zoomed out, a spot is a block or more: the first press zooms in there instead (true).
function _zoomToPlace(at) {
  if (map.getZoom() >= PLACE_ZOOM) return false;
  map.easeTo({ center: at, zoom: Math.max(PLACE_ZOOM + 1, map.getZoom()) });
  showToast('Zoomed in: press and hold again to add here');
  return true;
}
// Held still for 500 ms with one finger; a pan, a second finger or lifting cancels it.
let _press = null;  // AbortController of the pending press
mapDiv.addEventListener('touchstart', e => {
  _touchAt = e.timeStamp;
  _press?.abort();
  if (e.touches.length !== 1 || !map || placing || _namePopup || e.target.closest('.pin')) return;
  const { clientX: x0, clientY: y0 } = e.touches[0];
  const ac = _press = new AbortController(), opts = { passive: true, signal: ac.signal };
  const timer = setTimeout(() => {
    ac.abort();
    const rect = mapDiv.getBoundingClientRect(), at = map.unproject([x0 - rect.left, y0 - rect.top]);
    if (!_zoomToPlace(at)) openCtxMenu(at, x0, y0);
    // Lifting the finger must not also tap the map (or close the menu it just opened).
    mapDiv.addEventListener('touchend', ev => ev.preventDefault(), { once: true, passive: false });
  }, 500);
  ac.signal.addEventListener('abort', () => clearTimeout(timer));
  mapDiv.addEventListener('touchmove', ev => {
    const p = ev.touches[0];
    if (Math.hypot(p.clientX - x0, p.clientY - y0) > 10) ac.abort();
  }, opts);
  mapDiv.addEventListener('touchend', () => ac.abort(), opts);
  mapDiv.addEventListener('touchcancel', () => ac.abort(), opts);
}, { passive: true });

// Holistic Escape (and Back, js/ui.js): dismiss whichever transient UI is open (one layer
// per press), then close the open item, then (Escape only) shrink the sheet.
function dismissLayer(lower = true) {
  if (confirmDialog.open) return;  // the dialog cancels itself
  let dismissed = false;
  if (closeMenu(true))                     dismissed = true;
  if (_namePopup)                          { closeNameBox(); dismissed = true; }
  if (placing)                             { stopPlacing();  dismissed = true; }
  if (dismissed) return;
  if (_selectedId) { clearSelection(); btnAdd.focus(); return; }
  if (lower && !isDesktop()) lowerSheet();
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  // Editable fields own Escape locally (cancel rename / close name input);
  // don't cascade into dismissing layers or deselecting the card.
  const ae = document.activeElement;
  if (ae && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  dismissLayer();
});


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

// ── Cities / regions ──────────────────────────────────────────────────────────
// The areas (/api/cities), kept on this device: the map opens on the last copy
// (loadStoredCities); a failed load keeps it and retries every 5 s until it answers.
// The streets mount once it answered or failed (_citiesSettled, js/map.js ensureTiles).
// Returns whether it answered.
const CITIES_KEY = 'bb_cities';
let _citiesRetry = 0;
let _citiesSettled = false;
function loadStoredCities() {
  try { regions = JSON.parse(_read(CITIES_KEY)) || {}; } catch (_) { regions = {}; }
}
async function loadCities() {
  let ok = false;
  try {
    const res = await fetch('/api/cities', { signal: AbortSignal.timeout(START_WAIT_MS) });
    if (!res.ok) throw new Error(res.statusText);
    regions = await res.json();
    _store(CITIES_KEY, JSON.stringify(regions));
    _citiesFailed = false;
    ok = true;
  } catch (_) {
    _citiesFailed = true;
    clearTimeout(_citiesRetry);
    _citiesRetry = setTimeout(loadCities, 5000);
  }
  const first = !_citiesSettled;
  _citiesSettled = true;
  updateNetChip();
  if (map && (ok || first)) { syncRegion(); renderTargetInfo(); }
  return ok;
}

// ── Saved cars + homes (this browser only) ───────────────────────────────────
const PREFS_KEY = 'bb_prefs';
function loadPrefs() {
  let p = {};
  try { p = JSON.parse(_read(PREFS_KEY)) || {}; } catch (_) {}
  cars = Array.isArray(p.cars) ? p.cars : [];
  homes = Array.isArray(p.homes) ? p.homes : [];
  // Cars saved before colors were stored keep the color their position gave them.
  cars.forEach((c, i) => { c.color ||= CAR_COLORS[i % CAR_COLORS.length]; });
}
// Version a car's position change so a stale device or phone update can't
// overwrite it (the server keeps the higher version; see auth._merge_cars). The
// version is the clock in seconds: a phone report (server: version + 1) made while
// this page was open must not outrank a move the user makes here afterward.
function touchCarLocation(car) {
  car.loc_rev = Math.max((car.loc_rev || 0) + 1, Math.floor(Date.now() / 1000));
}
function savePrefs() {
  _store(PREFS_KEY, JSON.stringify({ cars, homes }));
  syncPrefs();
}

// Coarse position from the IP address (ISP routing point, often a city or more
// off): used only to pick the region, never to place the map.
async function getIPLocation() {
  try {
    const r = await fetch('/api/locate');
    const d = await r.json();
    if (d.lat != null && d.lon != null) return { lat: d.lat, lon: d.lon, region: d.region };
  } catch (_) {}
  return null;
}

// Device position (Wi-Fi/GPS) when the user already allowed it: no prompt on load.
async function getPermittedGPS() {
  try {
    const p = await navigator.permissions.query({ name: 'geolocation' });
    if (p.state !== 'granted') return null;
  } catch (_) { return null; }
  return new Promise(resolve => getGPS((lat, lon) => resolve({ lat, lon }), () => resolve(null)));
}

// A button that reads the device position: busy while waiting, error as a toast.
function locateWith(btn, onFix) {
  const done = () => { btn.disabled = false; btn.classList.remove('busy'); };
  btn.disabled = true; btn.classList.add('busy');
  getGPS(async (lat, lon, acc) => { done(); await onFix(lat, lon, acc); },
         msg => { done(); showToast(msg, true); });
}

function getGPS(onSuccess, onError) {
  if (!navigator.geolocation) { onError?.('Location isn’t available here.'); return; }
  navigator.geolocation.getCurrentPosition(
    pos => onSuccess(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy),
    err => onError?.(err.code === err.PERMISSION_DENIED ? 'Location is off for BroomBuster.' : 'Couldn’t find your location.'),
    { enableHighAccuracy: true, timeout: 10000 }
  );
}
