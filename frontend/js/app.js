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

// Minute tick: map colours, the calendar and the cards follow the clock (a
// window closes, a day starts). Cards re-render only when some urgency or a
// region-local date changed.
let _liveUrgencySig = '';
setInterval(() => {
  scheduleUrgencyUpdate();
  renderInfoCard();  // no-op unless today changed
  const day = s => BroomUrgency.isoDay(schedNow(s));
  const sig = [...Object.entries(carSchedules).map(([id, s]) => `${id}:${sweepUrgency(s)}:${day(s)}`),
               ...Object.entries(homeSchedules).map(([id, s]) => `${id}:${day(s)}`)].join('|');
  if (sig === _liveUrgencySig) return;
  _liveUrgencySig = sig;
  renderCarsPanel();
  renderHomePanel();
}, 60 * 1000);

// ── Locate button (GPS) ───────────────────────────────────────────────────────
btnLocate.addEventListener('click', () => locateWith(btnLocate, async (lat, lon) => {
  if (_gpsLocPinTimer) clearTimeout(_gpsLocPinTimer);
  _gpsLocPin = { lat, lon };
  if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
  updateCarMarkers();
  updateGpsPinPopup();
  _gpsLocPinTimer = setTimeout(hideGpsPinPopup, 15000);
  for (const car of cars) checkCarSilently(car);
}));

// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
