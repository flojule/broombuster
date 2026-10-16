// ── Account (Google sign-in; cars and homes sync to the signed-in user) ───────
const menuAccount = document.getElementById('menu-account');
let accountEnabled = false;  // server has Google sign-in configured
let account    = null;   // { email } while signed in
let _syncTimer = null;
let phoneLinks = {};     // car id -> epoch seconds of the phone's last report (null: never)

// Sign-in state. A signed-in user's saved prefs replace the local copy; an
// account without prefs adopts the local one.
async function loadAccount() {
  if (new URLSearchParams(location.search).has('auth_error')) {
    showToast('Sign-in failed', true);
    history.replaceState(null, '', '/');
  }
  try {
    const res = await fetch('/api/me', { cache: 'no-store' });
    if (!res.ok) return;
    const me = await res.json();
    accountEnabled = !!me.enabled;
    account = me.user;
    phoneLinks = me.phone_links || {};
    if (!account) return;
    if (me.prefs) localStorage.setItem(PREFS_KEY, JSON.stringify(me.prefs));
    else if (localStorage.getItem(PREFS_KEY)) _putPrefs(localStorage.getItem(PREFS_KEY));
  } catch (_) {}
}

function _putPrefs(body) {
  return fetch('/api/me/prefs', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body, keepalive: true,
  }).then(res => { if (!res.ok) showToast('Could not save to your account', true); })
    .catch(() => showToast('Could not save to your account', true));
}

// Called by savePrefs(): upload cars and homes shortly after the last change.
function syncPrefs() {
  if (!account) return;
  clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_flushPrefs, 800);
}

function _flushPrefs() {
  if (_syncTimer === null) return Promise.resolve();
  clearTimeout(_syncTimer);
  _syncTimer = null;
  return _putPrefs(JSON.stringify({ cars, homes }));
}

// Back in the foreground: a phone may have moved a car meanwhile. Push our own
// edits first, then adopt each server position with a higher version (the same
// rule the server applies, auth._merge_cars).
async function refreshFromAccount() {
  if (!account) return;
  try {
    await _flushPrefs();
    const res = await fetch('/api/me', { cache: 'no-store' });
    if (!res.ok) return;
    const me = await res.json();
    phoneLinks = me.phone_links || phoneLinks;
    const moved = [];
    for (const s of me.prefs?.cars || []) {
      const car = cars.find(c => c.id === s.id);
      if (car && (s.loc_rev || 0) > (car.loc_rev || 0)) {
        Object.assign(car, { lat: s.lat, lon: s.lon, loc_rev: s.loc_rev });
        moved.push(car);
      }
    }
    if (moved.length) {
      try { localStorage.setItem(PREFS_KEY, JSON.stringify({ cars, homes })); } catch (_) {}
      for (const car of moved) checkCar(car);
    }
    updateCarMarkers();  // re-renders the cards (phone link status may have changed)
  } catch (_) {}
}

// ── Account section of the Me tab ─────────────────────────────────────────────
function _menuItem(text, onClick) {
  const el = document.createElement(onClick ? 'button' : 'div');
  el.className = onClick ? 'menu-item' : 'menu-note';
  if (onClick) { el.type = 'button'; el.addEventListener('click', onClick); }
  el.textContent = text;
  return el;
}

async function _leaveAccount(request) {
  await _flushPrefs();
  const res = await request().catch(() => null);
  if (!res?.ok) { showToast('Request failed', true); return; }
  account = null;
  localStorage.removeItem(PREFS_KEY);  // the account's cars and homes leave this device
  location.reload();
}

// Two taps, like Remove on a card: the first arms it, the second deletes.
function _deleteAccountItem() {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'menu-item ce-danger';
  el.textContent = 'Delete account';
  el.title = 'Deletes the account and its saved cars and homes';
  confirmTap(el, () => {
    _syncTimer = null;
    _leaveAccount(() => fetch('/api/me', { method: 'DELETE' }));
  });
  return el;
}

function renderAccountSection() {
  menuAccount.hidden = !accountEnabled;
  if (!accountEnabled) return;
  const label = Object.assign(document.createElement('div'), { className: 'menu-label', textContent: 'Account' });
  menuAccount.replaceChildren(label, ...(account
    ? [_menuItem(account.email),
       _menuItem('Sign out', () => _leaveAccount(() => fetch('/auth/logout', { method: 'POST' }))),
       _deleteAccountItem()]
    : [_menuItem('Sign in with Google', () => { location.href = '/auth/login'; })]));
}
