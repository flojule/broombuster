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
    for (const [i, car] of cars.entries()) checkCar(car, i === 0);
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

// Clock tick: map colours, the calendar and the cards follow the clock (a window
// closes, a day starts). Cards re-render only when some urgency or a region-local
// date changed.
let _liveUrgencySig = '';
function tick() {
  scheduleUrgencyUpdate();
  renderInfoCard();  // no-op unless today changed
  const day = s => BroomUrgency.isoDay(schedNow(s));
  const sig = [...Object.entries(carSchedules).map(([id, s]) => `${id}:${sweepUrgency(s)}:${day(s)}`),
               ...Object.entries(homeSchedules).map(([id, s]) => `${id}:${day(s)}`)].join('|');
  if (sig === _liveUrgencySig) return;
  _liveUrgencySig = sig;
  renderCarsPanel();
  renderHomePanel();
}
setInterval(tick, 60 * 1000);

// Hidden: save pending edits and stop following the position. Back in the foreground
// (iOS freezes timers meanwhile): catch the clock up at once, take any car a phone
// moved, and follow the position again.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { _flushPrefs(); pauseHere(); return; }
  tick();
  refreshFromAccount();
  resumeHere();
});

// ── Locate button: centre on the device and show the "you are here" dot ─────
btnLocate.addEventListener('click', () => locateWith(btnLocate, (lat, lon, acc) => {
  if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
  _setHere(lat, lon, acc);
  followHere();
}));

// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
