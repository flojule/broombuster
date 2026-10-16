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
    const marker = placeMarker(_carMarkers.get(car.id), car, () => {
      const el = document.createElement('div');
      el.className = 'car-marker';
      el.innerHTML = icon('car');
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        setSelectedCar(car.id);
        setSheetCollapsed(false);  // reveal the sheet so the card is visible
        const entry = carsPanel.querySelector(`.car-entry[data-id="${CSS.escape(car.id)}"]`);
        if (entry) entry.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
      return el;
    });
    // Colour follows the car's position in the list (removing a car shifts it).
    const el = marker.getElement();
    el.classList.toggle('selected', isSelected);
    el.style.color = color;
    el.style.width = el.style.height = (isSelected ? 40 : 32) + 'px';
    el.style.fontSize = (isSelected ? 22 : 17) + 'px';
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
// updateCarMarkers() re-renders the cards, which marks the selected one.
function clearCarSelection() {
  _selectedCarId = null;
  updateCarMarkers();
}

function isCarInView(car) {
  try { return map?.getBounds()?.contains([car.lon, car.lat]) ?? false; }
  catch (_) { return false; }
}

function setSelectedCar(carId) {
  _selectedCarId = carId;
  const car = cars.find(c => c.id === carId);
  if (car && !isCarInView(car)) map.jumpTo({ center: [car.lon, car.lat], zoom: 16 });
  updateCarMarkers();
}

// Select a car and centre the target on it, so the info card reads its street.
function focusCar(carId) {
  setSelectedCar(carId);
  const car = cars.find(c => c.id === carId);
  if (car && map) map.easeTo({ center: [car.lon, car.lat], zoom: Math.max(map.getZoom(), 16) });
}

// ── GPS "you are here" dot ───────────────────────────────────────────────────
// Shown for a few seconds after the locate button centres the map; no actions.
function clearGpsPin() {
  if (_gpsLocPinTimer) { clearTimeout(_gpsLocPinTimer); _gpsLocPinTimer = null; }
  _gpsLocPin = null;
  updateCarMarkers();
}
