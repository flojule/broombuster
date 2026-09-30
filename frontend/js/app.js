// ── Bootstrap (runs after every other script has defined its functions) ─────
if (DEV_MODE) {
  session = { access_token: 'dev-token' };
  initApp();
} else {
  const stored = localStorage.getItem('bb_access');
  if (stored) {
    session = { access_token: stored };
    // Validate the stored token; on 401 try refresh, else drop to guest mode.
    apiFetch('/prefs').then(async r => {
      if (r.status === 401 && !await _tryRefresh()) _clearTokens();
      initApp();
    }).catch(() => initApp());
  } else {
    // Guest by default — no gate. Prefs live in sessionStorage until tab close.
    initApp();
  }
}

// Auto-refresh access token 1 min before expiry (every 14 min).
setInterval(async () => { if (session && !DEV_MODE) await _tryRefresh(); }, 14 * 60 * 1000);

// Minute tick: map colours and car cards follow the clock (a window closes, a
// day starts). Cards re-render only when some urgency or the date changed.
let _liveUrgencySig = '';
setInterval(() => {
  scheduleUrgencyUpdate();
  const sig = new Date().toDateString() + '|' + Object.entries(carSchedules)
    .map(([id, s]) => id + ':' + panelUrgency(s)).join('|');
  if (sig === _liveUrgencySig) return;
  _liveUrgencySig = sig;
  renderCarsPanel();
  updateStatusFromSchedules();
}, 60 * 1000);

// ── Locate button (GPS) ───────────────────────────────────────────────────────
btnLocate.addEventListener('click', () => {
  const btn = btnLocate;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="border-top-color:#2563eb;border-color:rgba(0,0,0,.15)"></span>';
  getGPS(async (lat, lon) => {
    btn.disabled = false; btn.textContent = '📍';
    setLocationKnown('gps', lat, lon);
    setNearestRegion(lat, lon);
    if (_gpsLocPinTimer) clearTimeout(_gpsLocPinTimer);
    _gpsLocPin = { lat, lon };
    if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
    updateCarMarkers();
    loadAreaMap(lat, lon, 16);
    updateGpsPinPopup();
    _gpsLocPinTimer = setTimeout(() => {
      _gpsLocPin = null; _gpsLocPinTimer = null;
      document.getElementById('gps-pin-popup').style.display = 'none';
      updateCarMarkers();
    }, 15000);
    for (const car of cars) checkCarSilently(car);
  }, msg => { btn.disabled = false; btn.textContent = '📍'; showToast(`GPS: ${msg}`, true); });
});

// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
