// ── Map contextmenu (right-click to add car) ──────────────────────────────────
mapDiv.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (!map) return;
  const rect = mapDiv.getBoundingClientRect();
  const ll   = map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
  pendingLat = ll.lat; pendingLon = ll.lng;
  showCtxMenu(e.clientX, e.clientY);
});

document.addEventListener('click', e => {
  if (!e.target.closest('#ctx-menu')) hideCtxMenu();
  // Click outside the name box cancels it. Map-canvas clicks are excluded here
  // and handled by the map 'click' listener so the opening tap can't self-close.
  if (_namePopup
      && !e.target.closest('.maplibregl-popup')
      && !e.target.closest('#ctx-menu')
      && !e.target.closest('#gps-pin-popup')
      && !e.target.closest('#btn-add-car')
      && !e.target.closest('#map')) {
    closeNameBox();
  }
});

// Holistic Escape: dismiss whichever transient UI is open (one layer per
// press), and if nothing is open, deselect the selected car card.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  // Editable fields own Escape locally (cancel rename / close name input);
  // don't cascade into dismissing layers or deselecting the card.
  const ae = document.activeElement;
  if (ae && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  let dismissed = false;
  if (ctxMenu.style.display === 'block')       { hideCtxMenu();      dismissed = true; }
  if (_namePopup)                               { closeNameBox();     dismissed = true; }
  if (_cardDetailCarId)                         { closeCardDetail();  dismissed = true; }
  if (_gpsLocPin)                               { hideGpsPinPopup();  dismissed = true; }
  if (_zonePopup)                               { closeZoneDetail();  dismissed = true; }
  if (placingCar || placingHome)                { stopPlacing(); setSheetCollapsed(false); dismissed = true; }
  if (dismissed) return;
  if (_selectedCarId) clearCarSelection();
});

carsPanel.addEventListener('mouseenter', () => { _hoverSuppressed = true;  customHoverEl.style.display = 'none'; });
carsPanel.addEventListener('mouseleave', () => { _hoverSuppressed = false; });

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

// Key of the region whose center is nearest (lat, lon) — cities.region_for_point.
function nearestRegionKey(lat, lon) {
  let best = null, bestDist = Infinity;
  for (const [key, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestDist) { bestDist = d; best = key; }
  }
  return best;
}

function setLocationKnown(source, lat = null, lon = null, city = null) {
  _locSource = source || null; _locLat = lat; _locLon = lon; _locCity = city || null;
  btnLocate.classList.remove('ip', 'gps');
  if (source) btnLocate.classList.add(source);
  updateLocateInfo();
}

function updateLocateInfo() {
  const el = document.getElementById('locate-info');
  if (!_locSource) { el.textContent = ''; return; }
  if (_locSource === 'ip') {
    el.textContent = _locCity ? `IP: ${_locCity}` : 'IP';
  } else {
    const coords = (_locLat !== null && _locLon !== null)
      ? `${_locLat.toFixed(4)}, ${_locLon.toFixed(4)}` : '';
    const area = _locCity || regions[nearestRegionKey(_locLat, _locLon)]?.name || '';
    el.textContent = coords
      ? `GPS: ${coords}${area ? ' · ' + area : ''}`
      : `GPS${area ? ': ' + area : ''}`;
  }
}

// ── Cities / regions ──────────────────────────────────────────────────────────
async function loadCities() {
  try {
    const res = await fetch('/cities');
    if (!res.ok) return;
    regions = await res.json();
    regionSelect.innerHTML = '';
    for (const [key, val] of Object.entries(regions)) {
      const opt = document.createElement('option');
      opt.value = key; opt.textContent = val.name;
      regionSelect.appendChild(opt);
    }
  } catch (_) {}
}

regionSelect.addEventListener('change', () => {
  if (_settingRegion) return;  // programmatic update — don't cascade
  const rk    = regionSelect.value;
  const rc    = regions[rk]?.center;
  const newCenter = rc || DEFAULT_CENTER;

  _renderedRegion  = rk;
  ensureTiles();
  if (map) map.jumpTo({ center: [newCenter.lon, newCenter.lat], zoom: regions[rk]?.zoom || 11 });
  updateCarMarkers();

  const carInRegion = cars.find(c => nearestRegionKey(c.lat, c.lon) === rk);
  if (carInRegion) { activeCarId = carInRegion.id; checkCarWithRender(carInRegion); }
  else { setStatus('idle', 'Add a car to check street sweeping.'); }
});

function setNearestRegion(lat, lon) {
  const best = nearestRegionKey(lat, lon);
  if (best && regionSelect.value !== best) {
    _settingRegion = true;
    regionSelect.value = best;
    _settingRegion = false;
  }
  if (best) _renderedRegion = best;
}

// ── Saved cars + homes (this browser only) ───────────────────────────────────
const PREFS_KEY = 'bb_prefs';
function loadPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (_) {}
  cars = Array.isArray(p.cars) ? p.cars : [];
  homes = Array.isArray(p.homes) ? p.homes : [];
}
function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify({ cars, homes })); } catch (_) {}
}

async function getIPLocation() {
  try {
    const r = await fetch('https://ipapi.co/json/');
    const d = await r.json();
    if (d.latitude && d.longitude) return { lat: d.latitude, lon: d.longitude, city: d.city || '' };
  } catch (_) {}
  return null;
}

function loadAreaMap(lat, lon, zoom = 11) {
  _renderedRegion = regionSelect.value || _renderedRegion;
  if (!map || !_renderedRegion) return;
  map.jumpTo({ center: [lon, lat], zoom });
  setStatus('idle', 'Add a car to check street sweeping.');
  ensureTiles();
}

function getGPS(onSuccess, onError) {
  if (!navigator.geolocation) { onError?.('Geolocation not supported.'); return; }
  navigator.geolocation.getCurrentPosition(
    pos => onSuccess(pos.coords.latitude, pos.coords.longitude),
    err => onError?.(err.message),
    { enableHighAccuracy: true, timeout: 10000 }
  );
}
