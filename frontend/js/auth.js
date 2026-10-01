// ── Auth ──────────────────────────────────────────────────────────────────────
function _saveTokens(access, refresh) {
  localStorage.setItem('bb_access',  access);
  localStorage.setItem('bb_refresh', refresh);
  session = { access_token: access };
}

function _clearTokens() {
  localStorage.removeItem('bb_access');
  localStorage.removeItem('bb_refresh');
  session = null;
}

// Guest prefs live in sessionStorage — discarded when the tab/window closes.
const GUEST_PREFS_KEY = 'bb_guest_prefs';
function _loadGuestPrefs() {
  try { return JSON.parse(sessionStorage.getItem(GUEST_PREFS_KEY)) || {}; }
  catch (_) { return {}; }
}
function _saveGuestPrefs(p) {
  try { sessionStorage.setItem(GUEST_PREFS_KEY, JSON.stringify(p)); } catch (_) {}
}

async function _tryRefresh() {
  const rt = localStorage.getItem('bb_refresh');
  if (!rt) return false;
  try {
    const res = await postJSON('/auth/refresh', { refresh_token: rt });
    if (!res.ok) { _clearTokens(); return false; }
    const data = await res.json();
    _saveTokens(data.access_token, data.refresh_token);
    return true;
  } catch (_) { return false; }
}


function showAuth() {
  authError.textContent = '';
  authScreen.hidden = false;
  emailEl.focus();
}
function hideAuth() { authScreen.hidden = true; }

// Toggle the toolbar's Sign in / Sign out buttons to match auth state.
function _updateAuthUI() {
  if (DEV_MODE) { btnSignin.hidden = true; btnLogout.hidden = true; return; }
  btnSignin.hidden = !!session;
  btnLogout.hidden = !session;
}

async function initApp() {
  authScreen.hidden = true;
  appScreen.style.display  = 'flex';
  _updateAuthUI();
  // Wait one animation frame so the browser can lay out #app-screen
  // before MapLibre reads the container dimensions.
  await new Promise(r => requestAnimationFrame(r));

  // Load cities and saved cars BEFORE creating the map so we can open
  // directly at the right location instead of defaulting to Bay Area.
  await Promise.all([loadCities(), loadPrefs()]);

  // Pick initial map center from saved cars, falling back to US overview.
  const initCenter = cars.length > 0
    ? { lat: cars[0].lat, lon: cars[0].lon, zoom: 15 }
    : DEFAULT_CENTER;
  initMap(initCenter);

  // Render the home cards + pins and refresh their trash schedules (if saved).
  renderHomePanel();
  updateHomeMarkers();
  checkAllHomes();

  // Fetch IP location in parallel with the first car check.
  const ipLocPromise = getIPLocation();

  if (cars.length > 0) {
    activeCarId = cars[0].id;
    setNearestRegion(cars[0].lat, cars[0].lon);
    await checkCarWithRender(cars[0]);
    for (const car of cars.slice(1)) checkCarSilently(car);
  } else {
    const ipLoc = await ipLocPromise;
    if (ipLoc) {
      setNearestRegion(ipLoc.lat, ipLoc.lon);
      setLocationKnown('ip', null, null, ipLoc.city);
      map.jumpTo({ center: [ipLoc.lon, ipLoc.lat], zoom: 13 });
      setStatus('idle', 'Loading map…');
      loadAreaMap(ipLoc.lat, ipLoc.lon, 13);
    } else {
      setStatus('idle', 'Select a region or add a car to load the map.');
    }
  }
}

// ── Auth forms ────────────────────────────────────────────────────────────────

// After a successful login/signup: adopt the guest's setup into the account,
// then reload so the app comes back signed in with server-backed prefs.
async function _finishAuth() {
  await _migrateGuestToServer();
  sessionStorage.removeItem(GUEST_PREFS_KEY);
  location.reload();
}

// Merge the guest's cars/homes (in-memory + sessionStorage) into the account,
// keeping anything the account already had. Cars dedupe by name, homes by coord.
async function _migrateGuestToServer() {
  const g = _loadGuestPrefs();
  const guestCars = [...(g.cars || [])];
  for (const c of cars) if (!guestCars.find(x => x.id === c.id)) guestCars.push(c);
  const guestHomes = _homesFromPrefs(g);
  for (const h of homes) if (!guestHomes.find(x => x.id === h.id)) guestHomes.push(h);

  let acct = {};
  try { const r = await apiFetch('/prefs'); if (r.ok) acct = await r.json(); } catch (_) {}

  const merged = [...(acct.cars || [])];
  const names = new Set(merged.map(c => c.name));
  for (const c of guestCars) if (!names.has(c.name)) { merged.push(c); names.add(c.name); }

  const mergedHomes = _homesFromPrefs(acct);
  const coords = new Set(mergedHomes.map(h => `${h.lat},${h.lon}`));
  for (const h of guestHomes) {
    const k = `${h.lat},${h.lon}`;
    if (!coords.has(k)) { mergedHomes.push(h); coords.add(k); }
  }

  if (merged.length === 0 && mergedHomes.length === 0) return;  // nothing to persist
  try { await postJSON('/prefs', { cars: merged, homes: mergedHomes }); } catch (_) {}
}

// POST credentials to an auth endpoint; on success store tokens and finish sign-in.
async function _submitAuth(path, btn, busyLabel, failMsg) {
  const idleLabel = btn.textContent;
  authError.textContent = '';
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>${busyLabel}`;
  try {
    const res = await postJSON(path, { email: emailEl.value.trim(), password: passwordEl.value });
    const data = await res.json();
    if (!res.ok) { authError.textContent = data.detail || failMsg; return; }
    _saveTokens(data.access_token, data.refresh_token);
    await _finishAuth();
  } catch (e) {
    authError.textContent = 'Network error — is the server running?';
  } finally {
    btn.disabled = false;
    btn.textContent = idleLabel;
  }
}

const login  = () => _submitAuth('/auth/login', btnLogin, 'Signing in…', 'Sign in failed');
const signup = () => _submitAuth('/auth/register', btnSignup, 'Creating account…', 'Registration failed');

// Registration may be locked on public deploys (shared account only). The
// server advertises this via window.ALLOW_REGISTRATION; hide the signup button
// so guests only see "Sign in". Defaults to shown when the flag is absent.
const ALLOW_REGISTRATION = window.ALLOW_REGISTRATION !== false;
if (!ALLOW_REGISTRATION) btnSignup.hidden = true;

btnLogin.addEventListener('click', login);
btnSignup.addEventListener('click', signup);
[emailEl, passwordEl].forEach(el => el.addEventListener('keydown', e => { if (e.key === 'Enter') login(); }));
btnSignin.addEventListener('click', showAuth);
authClose.addEventListener('click', hideAuth);
authScreen.addEventListener('click', e => { if (e.target === authScreen) hideAuth(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !authScreen.hidden) hideAuth(); });
// Logout returns to guest mode (server data stays on the account).
btnLogout.addEventListener('click', () => { _clearTokens(); location.reload(); });
