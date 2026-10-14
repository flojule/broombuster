// ── Map contextmenu (right-click to add car) ──────────────────────────────────
mapDiv.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (!map) return;
  const rect = mapDiv.getBoundingClientRect();
  const ll   = map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
  pendingLat = ll.lat; pendingLon = ll.lng;
  showCtxMenu(e.clientX, e.clientY);
});

// The name box closes only via its ✕, Esc, or saving: a stray/retargeted tap
// (soft keyboard, touch) must not discard it.
document.addEventListener('click', e => {
  if (!e.target.closest('#ctx-menu')) hideCtxMenu();
});

// Holistic Escape: dismiss whichever transient UI is open (one layer per
// press), then close the month view, then deselect the selected car card.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  // Editable fields own Escape locally (cancel rename / close name input);
  // don't cascade into dismissing layers or deselecting the card.
  const ae = document.activeElement;
  if (ae && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  let dismissed = false;
  if (!_legendPop.hidden)                       { setLegendOpen(false); dismissed = true; }
  if (ctxMenu.style.display === 'block')       { hideCtxMenu();      dismissed = true; }
  if (_namePopup)                               { closeNameBox();     dismissed = true; }
  if (_gpsLocPin)                               { hideGpsPinPopup();  dismissed = true; }
  if (_zonePopup)                               { closeZoneDetail();  dismissed = true; }
  if (placingCar || placingHome)                { stopPlacing(); setSheetCollapsed(false); dismissed = true; }
  if (dismissed) return;
  if (_cal.expanded) { collapseCal(); return; }
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

// ── Cities / regions ──────────────────────────────────────────────────────────
async function loadCities() {
  try {
    const res = await fetch('/cities');
    if (res.ok) regions = await res.json();
  } catch (_) {}
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
  syncPrefs();
}

// Coarse position from the IP address (ISP routing point, often a city or more
// off): used only to pick the region, never to place the map.
async function getIPLocation() {
  try {
    const r = await fetch('/locate');
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

function getGPS(onSuccess, onError) {
  if (!navigator.geolocation) { onError?.('Geolocation not supported.'); return; }
  navigator.geolocation.getCurrentPosition(
    pos => onSuccess(pos.coords.latitude, pos.coords.longitude),
    err => onError?.(err.message),
    { enableHighAccuracy: true, timeout: 10000 }
  );
}
