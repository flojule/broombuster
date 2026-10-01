// ── Bootstrap (runs after every other script has defined its functions) ─────
async function initApp() {
  // Cities and saved cars first, so the map opens at the right place.
  await loadCities();
  loadPrefs();
  initMap(cars.length ? { lat: cars[0].lat, lon: cars[0].lon, zoom: 15 } : DEFAULT_CENTER);

  renderHomePanel();
  updateHomeMarkers();
  checkAllHomes();

  if (cars.length) {
    activeCarId = cars[0].id;
    setNearestRegion(cars[0].lat, cars[0].lon);
    await checkCarWithRender(cars[0]);
    for (const car of cars.slice(1)) checkCarSilently(car);
    return;
  }
  const ipLoc = await getIPLocation();
  if (ipLoc) {
    setNearestRegion(ipLoc.lat, ipLoc.lon);
    setLocationKnown('ip', null, null, ipLoc.city);
    loadAreaMap(ipLoc.lat, ipLoc.lon, 13);
  } else {
    setStatus('idle', 'Select a region or add a car to load the map.');
  }
}
initApp();

// Minute tick: map colours and car cards follow the clock (a window closes, a
// day starts). Cards re-render only when some urgency or the date changed.
let _liveUrgencySig = '';
setInterval(() => {
  scheduleUrgencyUpdate();
  const sig = new Date().toDateString() + '|' + Object.entries(carSchedules)
    .map(([id, s]) => id + ':' + sweepUrgency(s)).join('|');
  if (sig === _liveUrgencySig) return;
  _liveUrgencySig = sig;
  renderCarsPanel();
  renderHomePanel();
  updateStatusFromSchedules();
}, 60 * 1000);

// ── Locate button (GPS) ───────────────────────────────────────────────────────
btnLocate.addEventListener('click', () => {
  const btn = btnLocate;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="border-top-color:var(--blue);border-color:rgba(0,0,0,.15)"></span>';
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
