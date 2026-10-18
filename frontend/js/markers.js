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
// Apple Maps); a tap opens the item in the sheet. Its label is the item's name (set by the caller).
function _pinEl(kind, id) {
  const el = document.createElement('button');
  el.className = 'pin';
  el.innerHTML = icon(kind);
  el.addEventListener('click', e => { e.stopPropagation(); openItem(id); });
  return el;
}

function updateCarMarkers() {
  for (const [id, marker] of _carMarkers) {
    if (!cars.find(c => c.id === id)) { marker.remove(); _carMarkers.delete(id); }
  }
  for (const car of cars) {
    const marker = placeMarker(_carMarkers.get(car.id), car, () => _pinEl('car', car.id));
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
    const marker = placeMarker(_homeMarkers.get(h.id), h, () => _pinEl('home', h.id));
    marker.getElement().style.setProperty('--pin', HOME_COLOR);
    marker.getElement().setAttribute('aria-label', itemName(h));
    _homeMarkers.set(h.id, marker);
  }
  applySelection();
}

// Under the name box while a new car is being named: its pin to be, faded.
function updateTempPin() {
  _tempPinMarker = placeMarker(_tempPinMarker, _tempPin, () => {
    const el = document.createElement('div');
    el.className = 'pin temp';
    el.innerHTML = icon('car');
    el.style.setProperty('--pin', nextCarColor());
    return el;
  });
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
  if (_selectedId && !selectedItem()) _selectedId = null;  // it was removed
  for (const [id, m] of [..._carMarkers, ..._homeMarkers]) m.getElement().classList.toggle('selected', id === _selectedId);
  for (const el of sheet.querySelectorAll('.car-entry')) {
    const sel = el.dataset.id === _selectedId, head = el.querySelector('.ce-header');
    el.classList.toggle('selected', sel);
    // A list row opens its item; on the item's own page the header is just its verdict.
    if (sel) { head.removeAttribute('role'); head.removeAttribute('tabindex'); }
    else { head.setAttribute('role', 'button'); head.tabIndex = 0; }
  }
  const item = selectedItem();
  carsPanel.classList.toggle('item-open', !!item);
  if (savedTitle.contentEditable !== 'true') savedTitle.textContent = item ? itemName(item) : 'Saved';
  btnAdd.hidden = !!item;
  btnItemMore.hidden = btnItemBack.hidden = !item;
}
// Back to the list; focus moves to "+" (the ‹ it was on is gone).
btnItemBack.addEventListener('click', () => { clearSelection(); btnAdd.focus(); });

function selectItem(id) {
  _selectedId = id;
  applySelection();
}

function clearSelection() {
  if (_selectedId) selectItem(null);
}

// Select a car or home and centre the target on it, so the signs and calendar read its
// street. The padding rides along: one animation, which a sheet change can't cut short.
function focusItem(id) {
  selectItem(id);
  const item = selectedItem();
  if (item && map) map.easeTo({ center: [item.lon, item.lat], zoom: Math.max(map.getZoom(), 16), padding: mapPadding() });
}

// Show an item's page from anywhere (its pin, or once it is added or moved): the
// Saved tab, the sheet at least half open, the map centred on it.
function openItem(id) {
  showTab('saved');
  focusItem(id);
  carsPanel.scrollTop = 0;
}

// The item's title is its name: a car's can be renamed ("…" › Rename).
const _selectedCar = () => cars.find(c => c.id === _selectedId);
makeEditable(savedTitle, () => itemName(selectedItem()), v => {
  const car = _selectedCar();
  if (!car) return;  // removed while being renamed
  car.name = v;
  savePrefs();
  updateCarMarkers();
}, () => !!_selectedCar());

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
