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

function nearestRegionNameByCoords(lat, lon) {
  let best = null, bestDist = Infinity;
  for (const [, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestDist) { bestDist = d; best = r.name; }
  }
  return best || '';
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
    const area = _locCity || nearestRegionNameByCoords(_locLat, _locLon);
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
    const data = await res.json();
    regions = {};
    regionSelect.innerHTML = '';
    for (const [key, val] of Object.entries(data.regions)) {
      regions[key] = { name: val.name, center: val.center, zoom: val.overview_zoom || 11 };
      const opt = document.createElement('option');
      opt.value = key; opt.textContent = val.name;
      regionSelect.appendChild(opt);
    }
  } catch (_) {}
}

regionSelect.addEventListener('change', () => {
  if (_settingRegion) return;  // programmatic update — don't cascade
  const rk    = regionSelect.value;
  const rName = regions[rk]?.name || rk;
  const rc    = regions[rk]?.center;
  const newCenter = rc || DEFAULT_CENTER;

  _currentGeojson  = null;
  _renderedRegion  = rk;
  renderZones(null);
  if (map) map.jumpTo({ center: [newCenter.lon, newCenter.lat], zoom: regions[rk]?.zoom || 11 });
  updateCarMarkers();

  const carInRegion = cars.find(c => {
    let best = null, bd = Infinity;
    for (const [k, r] of Object.entries(regions)) {
      const d = (r.center.lat - c.lat) ** 2 + (r.center.lon - c.lon) ** 2;
      if (d < bd) { bd = d; best = k; }
    }
    return best === rk;
  });
  if (carInRegion) { activeCarId = carInRegion.id; checkCarWithRender(carInRegion); }
  else { setStatus('idle', 'Add a car to check street sweeping.'); }
  // map.jumpTo above fires moveend → tile-based fetch handles the rest
});

function setNearestRegion(lat, lon) {
  let best = null, bestDist = Infinity;
  for (const [key, r] of Object.entries(regions)) {
    const d = (r.center.lat - lat) ** 2 + (r.center.lon - lon) ** 2;
    if (d < bestDist) { bestDist = d; best = key; }
  }
  if (best && regionSelect.value !== best) {
    _settingRegion = true;
    regionSelect.value = best;
    _settingRegion = false;
  }
  if (best) _renderedRegion = best;
}

// ── Cars ──────────────────────────────────────────────────────────────────────
function _newId() {
  return crypto.randomUUID ? crypto.randomUUID()
                           : Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// Read the homes array from a prefs object, backfilling a legacy single home
// (home_lat/lon/address) so accounts saved before multi-home keep working.
function _homesFromPrefs(p) {
  if (Array.isArray(p.homes) && p.homes.length) {
    return p.homes
      .filter(h => h && h.lat != null && h.lon != null)
      .map(h => ({ id: h.id || _newId(), lat: h.lat, lon: h.lon, address: h.address || '' }));
  }
  if (p.home_lat != null && p.home_lon != null) {
    return [{ id: _newId(), lat: p.home_lat, lon: p.home_lon, address: p.home_address || '' }];
  }
  return [];
}

// Prefs payload for homes: the array plus a legacy single-home mirror (homes[0])
// so an older cached client still reads a home.
function _homePrefsPayload(list) {
  return {
    homes: list,
    home_lat: list[0]?.lat ?? null,
    home_lon: list[0]?.lon ?? null,
    home_address: list[0]?.address ?? null,
  };
}

async function loadPrefs() {
  if (session) {
    try {
      const res = await apiFetch('/prefs');
      if (res.ok) {
        const prefs = await res.json();
        cars = prefs.cars || [];
        homes = _homesFromPrefs(prefs);
      }
    } catch (_) {}
    return;
  }
  // Guest — restore from sessionStorage (cleared when the tab closes).
  const g = _loadGuestPrefs();
  cars = g.cars || [];
  homes = _homesFromPrefs(g);
}

async function savePrefs() {
  const payload = { cars, ..._homePrefsPayload(homes) };
  if (!session) { _saveGuestPrefs(payload); return; }  // guest — sessionStorage only
  try {
    await apiFetch('/prefs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (_) {}
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
  // jumpTo fires moveend → debounced fetch, but we also fire immediately
  // so the user doesn't wait the 200 ms debounce for the first frame.
  fetchViewport(map.getBounds());
}

function getGPS(onSuccess, onError) {
  if (!navigator.geolocation) { onError?.('Geolocation not supported.'); return; }
  navigator.geolocation.getCurrentPosition(
    pos => onSuccess(pos.coords.latitude, pos.coords.longitude),
    err => onError?.(err.message),
    { enableHighAccuracy: true, timeout: 10000 }
  );
}
