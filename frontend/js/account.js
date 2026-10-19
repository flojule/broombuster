// ── Account (passkey sign-in; the account's cars and homes sync to its members) ──
let accountEnabled = false;  // the server has sign-in configured
let account    = null;   // the signed-in person { name } while signed in
let family     = [];     // names of everyone in the account
let _syncTimer = null;
let phoneLinks = {};     // car id -> the phone's last report, {used, moved, error} (auth.me)
let calendarOn = false;  // the account has a calendar subscription link
let _calendarUrl = null; // a freshly made link, shown once: {url, webcal}
let _joinToken = null;   // an invite's token, from a join link, until a sign-in uses it
let _inviteLink = null;  // a freshly made invite, shown once: {url, days}

// Sign-in state. A signed-in user's account prefs replace the local copy; an
// account without prefs adopts the local one.
async function loadAccount() {
  const join = new URLSearchParams(location.search).get('join');
  _joinToken = /^bbi_[\w-]+$/.test(join || '') ? join : null;
  if (join !== null) history.replaceState(null, '', '/');
  try {
    const res = await fetch('/api/me', { cache: 'no-store' });
    if (!res.ok) return;
    const me = await res.json();
    accountEnabled = !!me.enabled;
    account = me.user;
    family = me.family || [];
    phoneLinks = me.phone_links || {};
    calendarOn = !!me.calendar;
    if (account && _joinToken) {  // one account at a time
      showToast('Sign out first', true);
      _joinToken = null;
    }
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

// Asks first, as removing a car does. In a shared account this leaves it; the others keep it.
function _deleteAccountItem() {
  const shared = family.length > 1;
  const el = _menuItem(shared ? 'Leave account' : 'Delete account', async () => {
    const ok = shared
      ? await askConfirm('Leave this account?', 'You lose access to its cars and homes. The others keep them.', 'Leave')
      : await askConfirm('Delete your account?', 'Its saved cars and homes are deleted too. This can’t be undone.', 'Delete');
    if (!ok) return;
    _syncTimer = null;
    _leaveAccount(() => fetch('/api/me', { method: 'DELETE' }));
  });
  el.classList.add('danger');
  return el;
}

// ── Passkeys (WebAuthn): sign in, or make an account ──
const _passkeysOk = !!(window.PublicKeyCredential && navigator.credentials);
const _b64uToBuf = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')
  .padEnd(Math.ceil(s.length / 4) * 4, '=')), c => c.charCodeAt(0)).buffer;
const _bufToB64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

async function _errorText(res, fallback) {
  try {
    const detail = (await res.json()).detail;
    if (typeof detail === 'string') return detail;
  } catch (_) {}
  return fallback;
}

// One WebAuthn ceremony: the server's options, the browser's answer, the server's check.
// Throws with a message for the person; a prompt they cancel throws NotAllowedError.
async function _passkeyCeremony(create, optionsUrl, body, verifyUrl) {
  const res = await fetch(optionsUrl, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(await _errorText(res, 'Passkeys are not available'));
  const opts = await res.json();
  opts.challenge = _b64uToBuf(opts.challenge);
  for (const c of [...(opts.excludeCredentials || []), ...(opts.allowCredentials || [])]) {
    c.id = _b64uToBuf(c.id);
  }
  if (create) opts.user.id = _b64uToBuf(opts.user.id);
  const cred = create
    ? await navigator.credentials.create({ publicKey: opts })
    : await navigator.credentials.get({ publicKey: opts });
  const r = cred.response;
  const answer = create
    ? { attestationObject: _bufToB64u(r.attestationObject), clientDataJSON: _bufToB64u(r.clientDataJSON),
        transports: r.getTransports?.() || [] }
    : { authenticatorData: _bufToB64u(r.authenticatorData), clientDataJSON: _bufToB64u(r.clientDataJSON),
        signature: _bufToB64u(r.signature), userHandle: r.userHandle ? _bufToB64u(r.userHandle) : null };
  const done = await fetch(verifyUrl, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: cred.id, rawId: _bufToB64u(cred.rawId), type: cred.type,
      response: answer, clientExtensionResults: cred.getClientExtensionResults() }) });
  if (!done.ok) throw new Error(await _errorText(done, 'The passkey could not be verified'));
}

function _passkeyFailed(e) {
  if (e?.name !== 'NotAllowedError') showToast(e?.message || 'Passkey failed', true);
}
async function _passkeySignIn() {
  try {
    await _passkeyCeremony(false, '/auth/passkey/login/options', null, '/auth/passkey/login/verify');
    location.reload();
  } catch (e) { _passkeyFailed(e); }
}
async function _passkeyCreate(name) {
  if (!name.trim()) { showToast('Name?', true); return; }
  try {
    await _passkeyCeremony(true, '/auth/passkey/register/options',
      { name: name.trim(), invite: _joinToken }, '/auth/passkey/register/verify');
    location.reload();
  } catch (e) { _passkeyFailed(e); }
}
// ── Invites: a single-use link that adds someone to this account ──────────────
async function _makeInvite() {
  const res = await fetch('/api/me/invite', { method: 'POST' }).catch(() => null);
  if (!res?.ok) { showToast(res ? await _errorText(res, 'Could not make an invite') : 'Could not make an invite', true); return; }
  _inviteLink = await res.json();
  renderAccountSection();
}
function _inviteItems() {
  if (!_inviteLink) return [_menuItem('Invite someone', _makeInvite)];
  return [_menuItem('Copy invite link', async () => {
      try { await navigator.clipboard.writeText(_inviteLink.url); showToast('Link copied'); }
      catch (_) { showToast(_inviteLink.url, false, 10000); }
    }),
    _note(`Works once, for ${_inviteLink.days} days.`)];
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

// Signed out. A join link's token travels with the passkey sign-up that uses it.
function _signedOutSection() {
  if (!_passkeysOk) return [];
  const name = Object.assign(document.createElement('input'), {
    className: 'field', placeholder: 'Name', maxLength: 40, autocomplete: 'name' });
  const go = Object.assign(document.createElement('button'), {
    type: 'button', className: 'btn btn-wide', textContent: _joinToken ? 'Join' : 'Create account' });
  go.addEventListener('click', () => _passkeyCreate(name.value));
  const box = Object.assign(document.createElement('div'), { className: 'sec' });
  box.append(name, go);
  return [...(_section('Account', [_menuItem('Sign in', _passkeySignIn)])), box];
}

function _signedInSection() {
  const rows = [_menuItem(account.name)];
  if (family.length > 1) rows.push(_note(`With ${family.join(', ')}`));
  rows.push(..._inviteItems(),
    _menuItem('Sign out', () => _leaveAccount(() => fetch('/auth/logout', { method: 'POST' }))));
  return _section('Account', rows);
}

// Settings (as iOS orders it): who you are first, the calendar link after Appearance,
// deleting the account last and apart (a destructive action kept away from the rest).
// Without sign-in on the server, none of them.
function renderAccountSection() {
  const fill = (id, nodes) => document.getElementById(id).replaceChildren(...(accountEnabled ? nodes : []));
  fill('set-account', account ? _signedInSection() : _signedOutSection());
  fill('set-calendar', account ? _section('Calendar', _calendarItems()) : []);
  fill('set-danger', account ? _section('', [_deleteAccountItem()]) : []);
}
