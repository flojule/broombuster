// ── Car markers (MapLibre HTML markers) ──────────────────────────────────────
function updateCarMarkers() {
  // Remove markers for cars that no longer exist
  for (const [id, marker] of _carMarkers) {
    if (!cars.find(c => c.id === id)) { marker.remove(); _carMarkers.delete(id); }
  }

  cars.forEach((car, i) => {
    const color      = carColor(i);
    const isSelected = car.id === _selectedCarId;
    const size       = isSelected ? 22 : 16;

    if (_carMarkers.has(car.id)) {
      const marker = _carMarkers.get(car.id);
      marker.setLngLat([car.lon, car.lat]);
      const el = marker.getElement();
      el.style.width  = size + 'px';
      el.style.height = size + 'px';
      el.style.borderColor = isSelected ? color : 'white';
    } else {
      const el = document.createElement('div');
      el.className = 'car-marker' + (isSelected ? ' selected' : '');
      el.style.cssText = `width:${size}px;height:${size}px;background:${color};--marker-color:${color};`;
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        setSelectedCar(car.id);
        setSheetCollapsed(false);  // reveal the sheet so the card is visible
        const entry = carsPanel.querySelector(`.car-entry[data-id="${car.id}"]`);
        if (entry) entry.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
      const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([car.lon, car.lat])
        .addTo(map);
      _carMarkers.set(car.id, marker);
    }
  });

  // Temp pin (new car placement)
  if (_tempPin) {
    if (!_tempPinMarker) {
      const el = document.createElement('div');
      el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#64748b;border:3px solid #111;box-shadow:0 2px 6px rgba(0,0,0,.4);';
      _tempPinMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([_tempPin.lon, _tempPin.lat]).addTo(map);
    } else {
      _tempPinMarker.setLngLat([_tempPin.lon, _tempPin.lat]);
    }
  } else if (_tempPinMarker) {
    _tempPinMarker.remove(); _tempPinMarker = null;
  }

  // GPS "you are here" pin
  if (_gpsLocPin) {
    if (!_gpsMarker) {
      const el = document.createElement('div');
      el.style.cssText = 'width:14px;height:14px;border-radius:50%;background:#2563eb;border:3px solid white;box-shadow:0 0 0 6px rgba(37,99,235,.2),0 2px 8px rgba(0,0,0,.4);';
      _gpsMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([_gpsLocPin.lon, _gpsLocPin.lat]).addTo(map);
    } else {
      _gpsMarker.setLngLat([_gpsLocPin.lon, _gpsLocPin.lat]);
    }
  } else if (_gpsMarker) {
    _gpsMarker.remove(); _gpsMarker = null;
  }

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
