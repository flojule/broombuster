// ── Account (Google sign-in; cars and homes sync to the signed-in user) ───────
let accountEnabled = false;  // server has Google sign-in configured
let account    = null;   // { email } while signed in
let _syncTimer = null;
let phoneLinks = {};     // car id -> epoch seconds of the phone's last report (null: never)
let calendarOn = false;  // the account has a calendar subscription link
let _calendarUrl = null; // a freshly made link, shown once: {url, webcal}

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
    calendarOn = !!me.calendar;
    if (!account) return;
    if (me.prefs) _store(PREFS_KEY, JSON.stringify(me.prefs));
    else if (_read(PREFS_KEY)) _putPrefs(_read(PREFS_KEY));
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
      _store(PREFS_KEY, JSON.stringify({ cars, homes }));
      for (const car of moved) checkCar(car);
    }
    updateCarMarkers();  // re-renders the cards (phone link status may have changed)
  } catch (_) {}
}

// ── Account sections of the Settings tab ──────────────────────────────────────
// A row: a button with onClick, else plain text.
function _menuItem(text, onClick) {
  const el = document.createElement(onClick ? 'button' : 'div');
  el.className = 'menu-item';
  if (onClick) { el.type = 'button'; el.addEventListener('click', onClick); }
  el.textContent = text;
  return el;
}
const _note = text => Object.assign(document.createElement('p'), { className: 'note', textContent: text });
// A section: its label (none: '') over a box of rows.
function _section(label, rows) {
  const box = Object.assign(document.createElement('div'), { className: 'sec rows' });
  box.append(...rows);
  return label ? [Object.assign(document.createElement('div'), { className: 'sec-label', textContent: label }), box] : [box];
}

async function _leaveAccount(request) {
  await _flushPrefs();
  const res = await request().catch(() => null);
  if (!res?.ok) { showToast('Request failed', true); return; }
  account = null;
  _store(PREFS_KEY, null);  // the account's cars and homes leave this device
  location.reload();
}

// Asks first, as removing a car does.
function _deleteAccountItem() {
  const el = _menuItem('Delete account', async () => {
    if (!await askConfirm('Delete your account?', 'Its saved cars and homes are deleted too. This can’t be undone.', 'Delete')) return;
    _syncTimer = null;
    _leaveAccount(() => fetch('/api/me', { method: 'DELETE' }));
  });
  el.classList.add('danger');
  return el;
}

// ── Calendar subscription: a secret link a calendar app polls (sweeping of the cars,
// pickups of the homes, holidays), with a reminder the evening before each. ──────
async function _makeCalendarLink() {
  await _flushPrefs();
  const res = await fetch('/api/me/calendar', { method: 'POST' }).catch(() => null);
  if (!res?.ok) { showToast('Could not make a calendar link', true); return; }
  _calendarUrl = await res.json();
  calendarOn = true;
  renderAccountSection();
}
async function _stopCalendar() {
  if (!await askConfirm('Turn off the calendar link?', 'Calendars subscribed to it stop updating.', 'Turn off')) return;
  const res = await fetch('/api/me/calendar', { method: 'DELETE' }).catch(() => null);
  if (!res?.ok) { showToast('Could not turn it off', true); return; }
  calendarOn = false; _calendarUrl = null;
  renderAccountSection();
}
function _calendarItems() {
  const items = [];
  if (_calendarUrl) {
    const a = Object.assign(document.createElement('a'), {
      className: 'menu-item', href: _calendarUrl.webcal, textContent: 'Subscribe in Calendar' });
    items.push(a, _menuItem('Copy link', async () => {
      try { await navigator.clipboard.writeText(_calendarUrl.url); showToast('Link copied'); }
      catch (_) { showToast(_calendarUrl.url, false, 10000); }
    }), _note('Shown once. Anyone with the link sees your cars’ days.'));
  } else {
    items.push(_menuItem(calendarOn ? 'New calendar link' : 'Add sweeping and trash days to your calendar',
                         _makeCalendarLink));
  }
  if (calendarOn) {
    const off = _menuItem('Turn off calendar link', _stopCalendar);
    off.classList.add('danger');
    items.push(off);
  }
  return items;
}

// Settings (as iOS orders it): who you are first, the calendar link after Appearance,
// deleting the account last and apart (a destructive action kept away from the rest).
// Without Google sign-in on the server, none of them.
function renderAccountSection() {
  const fill = (id, nodes) => document.getElementById(id).replaceChildren(...(accountEnabled ? nodes : []));
  fill('set-account', account
    ? _section('Account', [_menuItem(account.email),
        _menuItem('Sign out', () => _leaveAccount(() => fetch('/auth/logout', { method: 'POST' })))])
    : _section('Account', [_menuItem('Sign in with Google', () => { location.href = '/auth/login'; }),
        _note('Sync your cars and homes, and add their days to your calendar.')]));
  fill('set-calendar', account ? _section('Calendar', _calendarItems()) : []);
  fill('set-danger', account ? _section('', [_deleteAccountItem()]) : []);
}
