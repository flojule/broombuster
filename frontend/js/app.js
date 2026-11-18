// ── Bootstrap (runs after every other script has defined its functions) ─────
async function initApp() {
  // The map opens at once on this device's copies of the cities and saved cars. Meanwhile
  // /api/cities and /api/me load: a signed-in user's cars and homes are merged in with
  // this device's unsaved edits once the account answers (loadAccount).
  loadStoredCities();
  const citiesLoaded = loadCities();
  const me = loadAccount();
  me.then(renderAccountSection);  // signed in or out; a join link's Join button too
  renderAccountSection();
  loadPrefs();
  loadSchedCache();  // each car's last answer, until its check comes back (offline: kept)
  initMap(cars.length ? { lat: cars[0].lat, lon: cars[0].lon, zoom: 15 } : DEFAULT_CENTER);
  setInterval(loadAlerts, 10 * 60 * 1000);
  if (!Object.keys(regions).length) await citiesLoaded;  // first visit: regions, clocks and holidays
  const checked = showSaved();
  if (!cars.length) {
    // No cars on this device: open on the account's (loadAccount draws and checks them).
    await Promise.race([me, new Promise(r => setTimeout(r, START_WAIT_MS))]);
    if (cars.length) { map.jumpTo({ center: [cars[0].lon, cars[0].lat], zoom: 15 }); loadAlerts(); return; }
  }
  if (cars.length) {
    await checked;
    // A car to move today or tomorrow: open on it, not on the calendar.
    const { today, tomorrow } = urgentCars();
    if ((today.length || tomorrow.length) && _tab === 'calendar') showTab('saved');
    return;
  }
  // No cars: device position if already allowed, else the area nearest the IP,
  // else every area's label in view.
  const gps = await getPermittedGPS();
  if (gps) { map.jumpTo({ center: [gps.lon, gps.lat], zoom: 15 }); return; }
  const ip = await getIPLocation();
  const rk = ip && (ip.region in regions ? ip.region : nearestRegion(ip.lat, ip.lon));
  if (rk) { map.jumpTo({ center: [regions[rk].center.lon, regions[rk].center.lat], zoom: regions[rk].zoom || 11 }); return; }
  const cs = Object.values(regions).map(r => [r.center.lon, r.center.lat]);
  if (cs.length) {
    const lo = [0, 1].map(i => Math.min(...cs.map(c => c[i]))), hi = [0, 1].map(i => Math.max(...cs.map(c => c[i])));
    map.fitBounds([lo, hi], { padding: 70, maxZoom: AREA_ZOOM - 1, animate: false });
  }
}
// The saved cars and homes on the map and in the sheet, each checked. Resolves once the
// cars are checked.
function showSaved() {
  renderHomePanel();
  updateHomeMarkers();
  checkAllHomes();
  loadAlerts();
  updateCarMarkers();
  return Promise.all(cars.map((car, i) => checkCar(car, i === 0)));
}
initApp();

// Clock tick: map colors, the calendar and the cards follow the clock (a window
// starts or closes, a day starts). Cards re-render only when some urgency, a car's
// deadline ("Move by 9AM" -> "Sweeping now") or a region-local date changed.
let _liveUrgencySig = '';
function tick() {
  scheduleUrgencyUpdate();
  renderTargetInfo();  // no-op unless today changed
  // Rules at cars and homes (a closure starts, a feed recovers): refetched every 30 min.
  for (const c of cars) if (carSchedules[c.id]) loadRules(c, 'car').then(n => n && renderCarsPanel());
  for (const h of homes) if (homeSchedules[h.id]) loadRules(h, 'home').then(n => n && renderHomePanel());
  const day = s => BroomUrgency.isoDay(schedNow(s));
  const sig = [...cars.map(c => `${c.id}:${sweepUrgency(c)}:${carWords(c)?.long}:${day(carSchedules[c.id])}`),
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
// Back after an hour or more, a home-screen app may run an old release: reload when the
// server's differs (cars, tab and map position come back).
let _hiddenAt = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { _hiddenAt = Date.now(); sendPrefsNow(); pauseHere(); return; }
  tick();
  refreshFromAccount();
  resumeHere();
  if (_hiddenAt && Date.now() - _hiddenAt > 3600e3) checkForUpdate();
});
async function checkForUpdate() {
  const mine = document.querySelector('meta[name="app-version"]')?.content;
  try {
    const { version } = await (await fetch('/health', { cache: 'no-store' })).json();
    if (mine && version && version !== mine && !isEditingIn(document.body) && !confirmDialog.open) location.reload();
  } catch (_) {}
}

// ── Locate button: center on the device and show the "you are here" dot ─────
btnLocate.addEventListener('click', () => locateWith(btnLocate, (lat, lon, acc) => {
  if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
  _setHere(lat, lon, acc);
  followHere();
}));

// ── Service Worker ────────────────────────────────────────────────────────────
// A new worker takes over an open page (sw.js claims it). The page itself loads network
// first, so it is usually current already: offer Reload only when the server's release
// differs from this page's (a tab left open across a deploy).
if ('serviceWorker' in navigator) {
  const hadWorker = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.register('/sw.js').catch(() => {});
  navigator.serviceWorker.addEventListener('controllerchange', async () => {
    if (!hadWorker) return;
    const mine = document.querySelector('meta[name="app-version"]')?.content;
    try {
      const { version } = await (await fetch('/health', { cache: 'no-store' })).json();
      if (mine && version && version !== mine) {
        showToast('A new version is ready', false, { sticky: true, action: { label: 'Reload', run: () => location.reload() } });
      }
    } catch (_) {}
  });
}
