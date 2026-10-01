// ── Map markers (MapLibre HTML markers) ───────────────────────────────────────
// Move `marker` to {lat, lon}, creating it from makeEl() when absent; null `at`
// removes it. Returns the marker (or null).
function placeMarker(marker, at, makeEl) {
  if (!at) { marker?.remove(); return null; }
  if (marker) return marker.setLngLat([at.lon, at.lat]);
  return new maplibregl.Marker({ element: makeEl(), anchor: 'center' })
    .setLngLat([at.lon, at.lat]).addTo(map);
}

function _dotEl(css) {
  const el = document.createElement('div');
  el.style.cssText = 'border-radius:50%;' + css;
  return el;
}

function updateCarMarkers() {
  // Remove markers for cars that no longer exist
  for (const [id, marker] of _carMarkers) {
    if (!cars.find(c => c.id === id)) { marker.remove(); _carMarkers.delete(id); }
  }

  cars.forEach((car, i) => {
    const color = carColor(i);
    const isSelected = car.id === _selectedCarId;
    const size = (isSelected ? 22 : 16) + 'px';
    const marker = placeMarker(_carMarkers.get(car.id), car, () => {
      const el = document.createElement('div');
      el.className = 'car-marker';
      el.style.cssText = `background:${color};--marker-color:${color};`;
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        setSelectedCar(car.id);
        setSheetCollapsed(false);  // reveal the sheet so the card is visible
        const entry = carsPanel.querySelector(`.car-entry[data-id="${car.id}"]`);
        if (entry) entry.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
      return el;
    });
    const el = marker.getElement();
    el.style.width = el.style.height = size;
    el.style.borderColor = isSelected ? color : 'white';
    _carMarkers.set(car.id, marker);
  });

  // Temp pin (new car placement) and the GPS "you are here" pin.
  _tempPinMarker = placeMarker(_tempPinMarker, _tempPin, () => _dotEl(
    'width:16px;height:16px;background:#64748b;border:3px solid #111;box-shadow:0 2px 6px rgba(0,0,0,.4);'));
  _gpsMarker = placeMarker(_gpsMarker, _gpsLocPin, () => _dotEl(
    'width:14px;height:14px;background:var(--blue);border:3px solid white;' +
    'box-shadow:0 0 0 6px rgba(37,99,235,.2),0 2px 8px rgba(0,0,0,.4);'));

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
