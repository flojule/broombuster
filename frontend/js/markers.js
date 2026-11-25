// ── Map markers (MapLibre HTML markers) ───────────────────────────────────────
// Move `marker` to {lat, lon}, creating it from makeEl() when absent; null `at`
// removes it. Returns the marker (or null).
function placeMarker(marker, at, makeEl) {
  if (!at) { marker?.remove(); return null; }
  if (marker) return marker.setLngLat([at.lon, at.lat]);
  return new maplibregl.Marker({ element: makeEl(), anchor: 'center' })
    .setLngLat([at.lon, at.lat]).addTo(map);
}

// A pin's glyph: the item's icon over the same icon drawn as its outline (.pin-halo).
const _pinGlyph = kind => `<svg class="ic"><use class="pin-halo" href="#i-${kind}"/><use href="#i-${kind}"/></svg>`;

// A car or home pin: the item's icon in its color (styles.css .pin); a tap opens the item
// in the sheet. Its label is the item's name (set by the caller).
function _pinEl(kind, id) {
  const el = document.createElement('button');
  el.className = 'pin';
  el.innerHTML = _pinGlyph(kind);
  el.addEventListener('click', e => { e.stopPropagation(); openItem(id); });
  return el;
}

// Keep `markers` (id -> marker) in step with `items`: one pin each, in its color, named;
// offset: its x shift (px).
function _syncPins(markers, items, kind, color, name, offset = () => 0) {
  for (const [id, marker] of markers) {
    if (!items.some(i => i.id === id)) { marker.remove(); markers.delete(id); }
  }
  for (const item of items) {
    const marker = placeMarker(markers.get(item.id), item, () => _pinEl(kind, item.id));
    marker.setOffset([offset(item), 0]);
    marker.getElement().style.setProperty('--pin', color(item));
    marker.getElement().setAttribute('aria-label', name(item));
    markers.set(item.id, marker);
  }
  applySelection();
}

function updateCarMarkers() {
  // Cars at one spot (within ~1 m) sit side by side, 18 px apart, so each pin shows.
  const spot = c => `${c.lat.toFixed(5)},${c.lon.toFixed(5)}`, at = new Map();
  for (const c of cars) at.set(spot(c), [...(at.get(spot(c)) || []), c.id]);
  _syncPins(_carMarkers, cars, 'car', carColor, c => c.name, c => {
    const same = at.get(spot(c));
    return (same.indexOf(c.id) - (same.length - 1) / 2) * 18;
  });
  renderCarsPanel();
}

function updateHomeMarkers() {
  if (!map) return;
  _syncPins(_homeMarkers, homes, 'home', () => 'var(--home)', itemName);  // lighter in dark mode
}

// ── Selection: one car or home at a time ──────────────────────────────────────
// The selected item has its own page in the Saved tab (as in Find My): its name is
// the title, the other cards hide, and a back button (‹) leads back to the list. Applied
// in place (no re-render, so focus and scroll stay).
const savedTitle   = document.getElementById('saved-title');
const btnAdd       = document.getElementById('btn-add');
const btnItemBack  = document.getElementById('btn-item-back');
const btnItemMore  = document.getElementById('btn-item-more');

function selectedItem() {
  return cars.find(c => c.id === _selectedId) || homes.find(h => h.id === _selectedId) || null;
}
function itemName(item) {
  return cars.includes(item) ? item.name : homes.length > 1 ? `Home ${homes.indexOf(item) + 1}` : 'Home';
}

function applySelection() {
  if (_selectedId && !selectedItem()) { _selectedId = null; syncHistory(); }  // it was removed
  for (const [id, m] of [..._carMarkers, ..._homeMarkers]) m.getElement().classList.toggle('selected', id === _selectedId);
  for (const el of sheet.querySelectorAll('.car-entry')) {
    const sel = el.dataset.id === _selectedId, head = el.querySelector('.ce-header');
    el.classList.toggle('selected', sel);
    // A list row opens its item; on the item's own page the header is just its verdict.
    if (sel) { head.removeAttribute('role'); head.removeAttribute('tabindex'); }
    else { head.setAttribute('role', 'button'); head.tabIndex = 0; }
  }
  const item = selectedItem();
  // The page slides in, or the list back (styles.css), when the Saved tab shows.
  if (carsPanel.classList.contains('item-open') !== !!item && _tab === 'saved' && !sheet.classList.contains('retracted')) {
    const tabSaved = document.getElementById('tab-saved');
    tabSaved.classList.remove('push-in', 'pop-in');
    restartAnim(tabSaved, item ? 'push-in' : 'pop-in');
  }
  carsPanel.classList.toggle('item-open', !!item);
  if (savedTitle.contentEditable !== 'true') savedTitle.textContent = item ? itemName(item) : 'Saved';
  btnAdd.hidden = !!item || !cars.length && !homes.length;  // nothing saved: the empty state's buttons add
  btnItemMore.hidden = btnItemBack.hidden = !item;
  syncCrosshair();
}

// The center target hides while a car or home pin sits on it (the pin marks the spot);
// it shows again once the map moves off, and always while placing (styles.css). On the
// "you are here" dot it widens into a ring around it, which would hide it.
function syncCrosshair() {
  if (!map) return;
  const c = map.project(map.getCenter());
  const near = (m, r) => { const p = map.project(m.getLngLat()); return Math.hypot(p.x - c.x, p.y - c.y) < r; };
  _crosshair.classList.toggle('under-pin', [..._carMarkers.values(), ..._homeMarkers.values()].some(m => near(m, 20)));
  _crosshair.classList.toggle('on-here', !!(_here && _hereMarker && near(_hereMarker, 16)));
}
// Back to the list; focus moves to "+" (the ‹ it was on is gone).
btnItemBack.addEventListener('click', () => { clearSelection(); btnAdd.focus(); });

// Home-screen app only (a browser has its own edge swipe, which reaches Back): a swipe
// right from the sheet's left edge goes back to the list. The page follows the finger;
// let go past 30 % of its width or with a flick, it goes, else it springs back.
const _standalone = navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
const EDGE_PX = 24;  // px from the sheet's left edge where the swipe starts
let _edge = null;
carsPanel.addEventListener('touchstart', e => {
  _edge = null;
  const p = e.touches[0];
  if (!_standalone || !_selectedId || isDesktop() || e.touches.length !== 1
      || p.clientX - sheet.getBoundingClientRect().left > EDGE_PX) return;
  _edge = { x: p.clientX, y: p.clientY, dx: 0, v: 0, on: false };
}, { passive: true });
carsPanel.addEventListener('touchmove', e => {
  const s = _edge;
  if (!s) return;
  const p = e.touches[0], dx = Math.max(0, p.clientX - s.x), dy = p.clientY - s.y;
  if (!s.on) {
    if (Math.hypot(dx, dy) < 8) return;
    if (dx <= 1.5 * Math.abs(dy)) { _edge = null; return; }
    s.on = true;
    s.els = [carsPanel, savedTitle];
  }
  if (s.t !== undefined && e.timeStamp > s.t) s.v = (dx - s.dx) / (e.timeStamp - s.t);
  s.dx = dx; s.t = e.timeStamp;
  for (const el of s.els) { el.style.transition = 'none'; el.style.transform = `translateX(${dx}px)`; }
}, { passive: true });
const _edgeEnd = () => {
  const s = _edge;
  _edge = null;
  if (!s?.on) return;
  const go = s.dx > 0.3 * carsPanel.offsetWidth || s.v >= 0.4;
  for (const el of s.els) { el.style.transition = 'transform .2s var(--sheet-ease)'; el.style.transform = go ? `translateX(${carsPanel.offsetWidth}px)` : ''; }
  setTimeout(() => {
    for (const el of s.els) el.style.transition = el.style.transform = '';
    if (go) clearSelection();
  }, 200);
};
carsPanel.addEventListener('touchend', _edgeEnd, { passive: true });
carsPanel.addEventListener('touchcancel', _edgeEnd, { passive: true });

function selectItem(id) {
  _selectedId = id;
  applySelection();
  syncHistory();
}

function clearSelection() {
  if (_selectedId) selectItem(null);
}

// Select a car or home and center the target on it, so the signs and calendar read its
// street. The padding rides along: one animation, which a sheet change can't cut short.
function focusItem(id) {
  selectItem(id);
  const item = selectedItem();
  if (item && map) map.easeTo({ center: [item.lon, item.lat], zoom: Math.max(map.getZoom(), 16), padding: mapPadding() });
}

// Show an item's page from anywhere (its pin, or once it is added or moved): the
// Saved tab, the sheet at least half open, the map centered on it.
function openItem(id) {
  showTab('saved');
  if (_detent !== 'medium') setDetent('medium');
  focusItem(id);
  carsPanel.scrollTop = 0;
}

// The item's title is its name: a car's can be renamed ("…" › Rename).
const _selectedCar = () => cars.find(c => c.id === _selectedId);
makeEditable(savedTitle, () => itemName(selectedItem()), v => {
  const car = _selectedCar();
  if (!car) return;  // removed while being renamed
  car.name = v.slice(0, CAR_NAME_MAX).trim();
  savePrefs();
  updateCarMarkers();
}, () => !!_selectedCar(), 'Car name');

// ── "You are here": a dot with an accuracy halo ──────────────────────────────
// The locate button starts it; it then follows the device while the app is in the
// foreground (paused while hidden), as the location dot does in Apple / Google Maps.
let _here = null;        // {lat, lon, acc (m)}: the last fix
let _hereMarker = null;
let _hereWatch = null;   // watchPosition id while following

function updateHereMarker() {
  _hereMarker = placeMarker(_hereMarker, _here, () => {
    const el = document.createElement('div');
    el.className = 'here';
    el.innerHTML = '<i class="here-halo"></i><i class="here-dot"></i>';
    return el;
  });
  if (!_here || !map) return;
  // Halo diameter in px: the accuracy radius over the map's meters per pixel.
  _hereMarker.getElement().style.setProperty('--halo', Math.min(600, 2 * _here.acc / metersPerPx(_here.lat)) + 'px');
}

function _setHere(lat, lon, acc) { _here = { lat, lon, acc: acc || 0 }; updateHereMarker(); syncCrosshair(); }

// Follow the device position (no-op if already following). A failure stops quietly:
// the locate button reports errors itself. High accuracy (GPS, which drains the battery)
// only until a fix within GOOD_FIX_M, or for HIGH_ACCURACY_MS; then the coarse position.
const GOOD_FIX_M = 20;
const HIGH_ACCURACY_MS = 2 * 60 * 1000;
let _coarseTimer = 0;
function followHere() {
  if (_hereWatch !== null || !navigator.geolocation) return;
  _watchHere(true);
}
function _watchHere(high) {
  const coarse = () => { pauseHere(); _watchHere(false); };
  _hereWatch = navigator.geolocation.watchPosition(p => {
    _setHere(p.coords.latitude, p.coords.longitude, p.coords.accuracy);
    if (high && p.coords.accuracy <= GOOD_FIX_M) coarse();
  }, () => pauseHere(), { enableHighAccuracy: high, maximumAge: 10000 });
  if (high) _coarseTimer = setTimeout(coarse, HIGH_ACCURACY_MS);
}

function pauseHere() {
  clearTimeout(_coarseTimer);
  if (_hereWatch !== null) navigator.geolocation.clearWatch(_hereWatch);
  _hereWatch = null;
}

// Back in the foreground: follow again if the dot was on.
function resumeHere() { if (_here) followHere(); }
