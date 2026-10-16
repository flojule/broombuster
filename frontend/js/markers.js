// ── Map markers (MapLibre HTML markers) ───────────────────────────────────────
// Move `marker` to {lat, lon}, creating it from makeEl() when absent; null `at`
// removes it. Returns the marker (or null).
function placeMarker(marker, at, makeEl) {
  if (!at) { marker?.remove(); return null; }
  if (marker) return marker.setLngLat([at.lon, at.lat]);
  return new maplibregl.Marker({ element: makeEl(), anchor: 'center' })
    .setLngLat([at.lon, at.lat]).addTo(map);
}

// A car or home pin: the card's icon, white on a disc of the item's colour (as in
// Apple Maps); a tap selects the item and shows its card.
function _pinEl(kind, id, label) {
  const el = document.createElement('button');
  el.className = 'pin';
  el.innerHTML = icon(kind);
  el.setAttribute('aria-label', label);
  el.addEventListener('click', e => {
    e.stopPropagation();
    selectItem(id);
    setSheetCollapsed(false);  // reveal the sheet so the card is visible
    document.querySelector(`#sheet [data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
  return el;
}

function updateCarMarkers() {
  for (const [id, marker] of _carMarkers) {
    if (!cars.find(c => c.id === id)) { marker.remove(); _carMarkers.delete(id); }
  }
  for (const car of cars) {
    const marker = placeMarker(_carMarkers.get(car.id), car, () => _pinEl('car', car.id, car.name));
    marker.getElement().style.setProperty('--pin', carColor(car));
    marker.getElement().setAttribute('aria-label', car.name);
    _carMarkers.set(car.id, marker);
  }
  applySelection();
  renderCarsPanel();
}

function updateHomeMarkers() {
  if (!map) return;
  for (const [id, marker] of _homeMarkers) {
    if (!homes.find(h => h.id === id)) { marker.remove(); _homeMarkers.delete(id); }
  }
  for (const h of homes) {
    const marker = placeMarker(_homeMarkers.get(h.id), h, () => _pinEl('home', h.id, 'Home'));
    marker.getElement().style.setProperty('--pin', HOME_COLOR);
    _homeMarkers.set(h.id, marker);
  }
  applySelection();
}

// The grey pin under the name box while a new car is being named.
function updateTempPin() {
  _tempPinMarker = placeMarker(_tempPinMarker, _tempPin, () => {
    const el = document.createElement('div');
    el.className = 'temp-pin';
    return el;
  });
}

// ── Selection: one car or home at a time ──────────────────────────────────────
// Marks the pin and opens the card in place (no re-render, so focus and scroll stay).
function applySelection() {
  for (const [id, m] of [..._carMarkers, ..._homeMarkers]) m.getElement().classList.toggle('selected', id === _selectedId);
  for (const el of sheet.querySelectorAll('.car-entry')) {
    el.classList.toggle('selected', el.dataset.id === _selectedId);
    el.querySelector('.ce-header').setAttribute('aria-expanded', String(el.dataset.id === _selectedId));
  }
}

function selectItem(id) {
  _selectedId = id;
  applySelection();
}

function clearSelection() {
  if (_selectedId) selectItem(null);
}

// Select a car or home and centre the target on it, so the sign and calendar read its street.
function focusItem(id) {
  selectItem(id);
  const item = cars.find(c => c.id === id) || homes.find(h => h.id === id);
  if (item && map) map.easeTo({ center: [item.lon, item.lat], zoom: Math.max(map.getZoom(), 16) });
}

// Card click: open (and centre on) the item, or close it if it is already open.
function toggleItem(id) {
  if (_selectedId === id) clearSelection(); else focusItem(id);
}

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
  // Halo diameter in px: the accuracy radius over the map's metres per pixel (512 px tiles).
  const mPerPx = 40075016.686 * Math.cos(_here.lat * Math.PI / 180) / (512 * 2 ** map.getZoom());
  _hereMarker.getElement().style.setProperty('--halo', Math.min(600, 2 * _here.acc / mPerPx) + 'px');
}

function _setHere(lat, lon, acc) { _here = { lat, lon, acc: acc || 0 }; updateHereMarker(); }

// Follow the device position (no-op if already following). A failure stops quietly:
// the locate button reports errors itself.
function followHere() {
  if (_hereWatch !== null || !navigator.geolocation) return;
  _hereWatch = navigator.geolocation.watchPosition(
    p => _setHere(p.coords.latitude, p.coords.longitude, p.coords.accuracy),
    () => pauseHere(), { enableHighAccuracy: true, maximumAge: 10000 });
}

function pauseHere() {
  if (_hereWatch !== null) navigator.geolocation.clearWatch(_hereWatch);
  _hereWatch = null;
}

// Back in the foreground: follow again if the dot was on.
function resumeHere() { if (_here) followHere(); }
