// ── Bootstrap (runs after every other script has defined its functions) ─────
// With no car on this device the map opens where the IP address places it: /api/locate is
// asked alongside /api/cities and waited for this long, rather than opening on the whole
// country and jumping.
const OPEN_WAIT_MS = 1500;
const ALERTS_EVERY_MS = 10 * 60 * 1000;  // the alerts' refresh (paused while hidden, as the clock tick)
async function initApp() {
  // The map opens at once on this device's copies of the cities and saved cars. Meanwhile
  // /api/cities and /api/me load: a signed-in user's cars and homes are merged in with
  // this device's unsaved edits once the account answers (loadAccount).
  loadStoredCities();
  const firstVisit = !Object.keys(regions).length;
  const citiesLoaded = loadCities();
  const me = loadAccount();
  me.then(renderAccountSection);  // signed in or out; a join link's Join button too
  renderAccountSection();
  loadPrefs();
  loadSchedCache();  // each car's last answer, until its check comes back (offline: kept)
  let ipAt = null;
  const ip = cars.length ? Promise.resolve(null) : getIPLocation().then(v => (ipAt = v));
  if (!cars.length) {
    await Promise.race([Promise.all([firstVisit ? citiesLoaded : null, ip]), new Promise(r => setTimeout(r, OPEN_WAIT_MS))]);
  }
  const ipRegion = () => ipAt && (ipAt.region in regions ? ipAt.region : nearestRegion(ipAt.lat, ipAt.lon));
  const rk = !cars.length && ipRegion();
  const start = cars.length ? { lat: cars[0].lat, lon: cars[0].lon, zoom: 15 }
    : rk ? { lat: regions[rk].center.lat, lon: regions[rk].center.lon, zoom: regions[rk].zoom || 11 } : DEFAULT_CENTER;
  // The region's clock from the start: the calendar (held hidden until now, index.html
  // .cal-wait) never shows the device's date and then flips to the region's.
  _region = regionAt(start.lat, start.lon) || _region;
  initMap(start);
  renderTargetInfo();
  document.getElementById('ic-cal').classList.remove('cal-wait');
  setInterval(() => { if (!document.hidden) loadAlerts(); }, ALERTS_EVERY_MS);
  if (!Object.keys(regions).length) await citiesLoaded;  // first visit: regions, clocks and holidays
  showSaved();
  if (!cars.length) {
    // No cars on this device: open on the account's (loadAccount draws and checks them).
    await Promise.race([me, new Promise(r => setTimeout(r, START_WAIT_MS))]);
    if (cars.length) { map.jumpTo({ center: [cars[0].lon, cars[0].lat], zoom: 15 }); loadAlerts(); return; }
  }
  if (cars.length) return;  // opens on the calendar; the Saved tab's badge flags a car to move
  // No cars: device position if already allowed, else the area nearest the IP (where the
  // map opened, if it knew it in time), else every area's label in view.
  const gps = await getPermittedGPS();
  if (gps) { map.jumpTo({ center: [gps.lon, gps.lat], zoom: 15 }); return; }
  await ip;
  const late = ipRegion();
  if (late) {
    if (late !== rk) map.jumpTo({ center: [regions[late].center.lon, regions[late].center.lat], zoom: regions[late].zoom || 11 });
    return;
  }
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
  if (document.hidden) return;  // caught up when the page shows again (visibilitychange)
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
  const away = _hiddenAt ? Date.now() - _hiddenAt : 0;
  if (away > ALERTS_EVERY_MS) loadAlerts();
  if (away > 3600e3) checkForUpdate();
});
// The server's release differs from this page's (a deploy since it loaded).
async function newerRelease() {
  const mine = document.querySelector('meta[name="app-version"]')?.content;
  try {
    const { version } = await (await fetch('/health', { cache: 'no-store' })).json();
    return !!(mine && version && version !== mine);
  } catch (_) { return false; }
}
async function checkForUpdate() {
  if (await newerRelease() && !isEditingIn(document.body) && !confirmDialog.open) location.reload();
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
    if (hadWorker && await newerRelease()) {
      showToast('A new version is ready', false, { sticky: true, action: { label: 'Reload', run: () => location.reload() } });
    }
  });
}
