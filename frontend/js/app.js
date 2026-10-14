// ── Bootstrap (runs after every other script has defined its functions) ─────
async function initApp() {
  // Cities and saved cars first, so the map opens at the right place.
  await loadCities();
  await loadAccount();  // a signed-in user's saved cars and homes replace the local copy
  loadPrefs();
  initMap(cars.length ? { lat: cars[0].lat, lon: cars[0].lon, zoom: 15 } : DEFAULT_CENTER);

  renderHomePanel();
  updateHomeMarkers();
  checkAllHomes();

  if (cars.length) {
    activeCarId = cars[0].id;
    await checkCarWithRender(cars[0]);
    for (const car of cars.slice(1)) checkCarSilently(car);
    return;
  }
  // No cars: device position if already allowed, else the area nearest the IP,
  // else the area labels.
  const gps = await getPermittedGPS();
  if (gps) { map.jumpTo({ center: [gps.lon, gps.lat], zoom: 15 }); return; }
  const ip = await getIPLocation();
  const rk = ip && (ip.region in regions ? ip.region : nearestRegion(ip.lat, ip.lon));
  if (rk) map.jumpTo({ center: [regions[rk].center.lon, regions[rk].center.lat], zoom: regions[rk].zoom || 11 });
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
  updateSheetSummary();
}, 60 * 1000);

// ── Locate button (GPS) ───────────────────────────────────────────────────────
btnLocate.addEventListener('click', () => {
  const btn = btnLocate;
  btn.disabled = true; btn.classList.add('busy');
  getGPS(async (lat, lon) => {
    btn.disabled = false; btn.classList.remove('busy');
    if (_gpsLocPinTimer) clearTimeout(_gpsLocPinTimer);
    _gpsLocPin = { lat, lon };
    if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
    updateCarMarkers();
    updateGpsPinPopup();
    _gpsLocPinTimer = setTimeout(() => {
      _gpsLocPin = null; _gpsLocPinTimer = null;
      document.getElementById('gps-pin-popup').style.display = 'none';
      updateCarMarkers();
    }, 15000);
    for (const car of cars) checkCarSilently(car);
  }, msg => { btn.disabled = false; btn.classList.remove('busy'); showToast(`GPS: ${msg}`, true); });
});

// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
