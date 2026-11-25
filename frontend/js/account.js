// ── Account (passkey sign-in; the account's cars and homes sync to its members) ──
let accountEnabled = false;  // the server has sign-in configured
let account    = null;   // the signed-in person { name } while signed in
let family     = [];     // names of everyone in the account
let _syncTimer = null;
let _saving    = Promise.resolve();  // account saves and reads, one at a time
let _prefsRev  = null;   // the account's prefs version our lists build on (auth.me prefs_rev)
// The sync base: {cars, homes} as this page last read from or saved to the account (null:
// none). What differs from it is this device's unsaved edits. Kept across launches.
const SYNC_BASE_KEY = 'bb_sync_base';
let _base = null;
let calendarOn = false;  // the account has a calendar subscription link
let _calendarUrl = null; // a freshly made link, shown once: {url, webcal}
let _joinToken = null;   // an invite's token, from a join link, until a sign-in uses it
let _inviteLink = null;  // a freshly made invite, shown once: {url, days}

// Sign-in state. A signed-in user gets the account's lists with this device's unsaved
// edits re-applied on top (all of its lists, for an account without any yet).
async function loadAccount() {
  const join = new URLSearchParams(location.search).get('join');
  _joinToken = /^bbi_[\w-]+$/.test(join || '') ? join : null;
  if (join !== null) history.replaceState(null, '', '/');
  // The base of the lists this page starts on (another tab may save before /api/me answers).
  try { _base = JSON.parse(_read(SYNC_BASE_KEY)); } catch (_) { _base = null; }
  try {
    const res = await fetch('/api/me', { cache: 'no-store' });
    if (!res.ok) return;
    const me = await res.json();
    accountEnabled = !!me.enabled;
    account = me.user;
    family = me.family || [];
    calendarOn = !!me.calendar;
    if (account && _joinToken) {  // one account at a time
      showToast('Sign out first', true);
      _joinToken = null;
    }
    // The session ended on its own (30 days unused): say so; cars stay on this device only.
    if (!account && accountEnabled && _read('bb_signed_in')) showToast('Signed out', true, { sticky: true });
    _store('bb_signed_in', account ? '1' : null);
    if (!account) { _setBase(null); return; }
    if (!map) loadPrefs();  // before startup: this device's copy (answered late: the live lists)
    _takeAccountLists(me);
    if (_unsaved()) { syncPrefs(); await _flushPrefs(); }
    _checkPush();
  } catch (_) {}
}

function _setBase(json) {
  _base = json && JSON.parse(json);
  _store(SYNC_BASE_KEY, json);
}
// This device's lists differ from the account's as last seen.
function _unsaved() {
  return !_base || !(_sameList(cars, _base.cars) && _sameList(homes, _base.homes));
}

// PUT the lists, based on version _prefsRev; on success they are the new sync base.
async function _sendPrefs() {
  const sent = JSON.stringify({ cars, homes }), rev = _prefsRev;
  const res = await fetch('/api/me/prefs', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, keepalive: true,
    body: JSON.stringify({ cars, homes, rev: rev ?? undefined }),
  }).catch(() => null);
  if (res?.ok && _prefsRev === rev) {  // else a newer save or read moved on meanwhile
    _setBase(sent);
    try { _prefsRev = (await res.json()).rev ?? null; } catch (_) { _prefsRev = null; }
  }
  return res;
}

// Save the lists to the account. Another device saved first (409): re-read, re-apply
// our edits on top of its lists, and save once more.
async function _pushPrefs(retry = true) {
  const res = await _sendPrefs();
  if (res?.status === 409 && retry && await _pullPrefs()) return _pushPrefs(false);
  if (!res?.ok) showToast('Couldn’t save to your account', true);
}

// The page is being hidden (iOS may freeze or end it): send unsaved edits at once,
// outside the queue. A refusal is rebased at the next launch or return.
function sendPrefsNow() {
  if (!account) return;
  clearTimeout(_syncTimer);
  _syncTimer = null;
  if (_unsaved()) _sendPrefs();
}

// Called by savePrefs(): upload cars and homes shortly after the last change.
function syncPrefs() {
  if (!account) return;
  clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_flushPrefs, 800);
}

function _flushPrefs() {
  if (_syncTimer !== null) {
    clearTimeout(_syncTimer);
    _syncTimer = null;
    _saving = _saving.then(() => _pushPrefs()).catch(() => {});
  }
  return _saving;
}

// A car or home as text, its keys sorted, to compare two copies.
const _itemKey = x => JSON.stringify(x, Object.keys(x).sort());
const _sameList = (a, b) => a.length === b.length && a.every((x, i) => _itemKey(x) === _itemKey(b[i]));

// Where a car or home is: these fields change together.
const _SPOT = ['lat', 'lon', 'loc_rev', 'side', 'address'];

// Three-way merge into the local item `m` (kept as the same object): each field this
// device changed since `b` keeps its value, the others take the server's (`s`). The
// spot goes as one, and a server spot with a higher version wins (auth._merge_cars).
function _mergeItem(m, b, s) {
  const changed = k => JSON.stringify(m[k]) !== JSON.stringify(b[k]);
  const spotHere = _SPOT.some(changed) && !((s.loc_rev || 0) > (m.loc_rev || 0));
  const out = {};
  for (const k of new Set([...Object.keys(m), ...Object.keys(s)])) {
    const from = _SPOT.includes(k) ? (spotHere ? m : s) : (changed(k) ? m : s);
    if (from[k] !== undefined) out[k] = from[k];
  }
  for (const k of Object.keys(m)) if (!(k in out)) delete m[k];
  return Object.assign(m, out);
}

// `local` with this device's edits since `base` re-applied on top of `server`: its
// additions and removals by id, its changed fields per item. An item removed on the
// server stays removed. Local items are updated in place.
function _rebase(local, base, server) {
  const was = new Map(base.map(x => [x.id, x]));
  const mine = new Map(local.map(x => [x.id, x]));
  const out = [];
  for (const s of server) {
    const m = mine.get(s.id);
    if (!m) { if (!was.has(s.id)) out.push(s); continue; }  // removed here, or added elsewhere
    out.push(_mergeItem(m, was.get(s.id) || {}, s));
  }
  const there = new Set(server.map(s => s.id));
  for (const m of local) if (!was.has(m.id) && !there.has(m.id)) out.push(m);  // added here
  return out;
}

// Take the account's lists (`me`: /api/me) with this device's unsaved edits re-applied,
// against the saved sync base. Without one, the account's lists win; an account
// without any takes this device's. Draws the result once the app is up.
function _takeAccountLists(me) {
  const server = me.prefs || { cars: [], homes: [] };
  const base = _base || (me.prefs ? JSON.parse(JSON.stringify({ cars, homes })) : { cars: [], homes: [] });
  const spot = x => `${x.lat},${x.lon}`;
  const carAt = new Map(cars.map(c => [c.id, spot(c)]));
  const homeAt = new Map(homes.map(h => [h.id, spot(h)]));
  cars = _rebase(cars, base.cars, server.cars);
  homes = _rebase(homes, base.homes, server.homes);
  _prefsRev = me.prefs_rev ?? null;
  _setBase(JSON.stringify(server));
  _store(PREFS_KEY, JSON.stringify({ cars, homes }));
  for (const id in carSchedules) if (!cars.some(c => c.id === id)) delete carSchedules[id];
  for (const id in homeSchedules) if (!homes.some(h => h.id === id)) delete homeSchedules[id];
  if (!map) return;  // startup draws and checks them
  updateCarMarkers();  // also re-renders the cards
  updateHomeMarkers();
  renderHomePanel();
  for (const c of cars) if (carAt.get(c.id) !== spot(c)) { delete carSchedules[c.id]; checkCar(c); }
  for (const h of homes) if (homeAt.get(h.id) !== spot(h)) { delete homeSchedules[h.id]; checkHome(h.id); }
}

// Read the account's lists and take them (_takeAccountLists); false if that failed.
async function _pullPrefs() {
  const res = await fetch('/api/me', { cache: 'no-store' }).catch(() => null);
  if (!res?.ok) return false;
  const me = await res.json().catch(() => null);
  if (!me) return false;
  if (!me.user) {  // the session ended meanwhile
    account = null;
    _store('bb_signed_in', null);
    _setBase(null);
    showToast('Signed out', true, { sticky: true });
    renderAccountSection();
    return false;
  }
  _takeAccountLists(me);
  return true;
}

// Back in the foreground: another device may have changed the lists.
// Push our own edits first, then take the account's; edits still unsaved (a failed
// or refused save) are saved again.
async function refreshFromAccount() {
  if (!account) return;
  await _flushPrefs();
  const pulled = await (_saving = _saving.then(_pullPrefs).catch(() => false));
  if (pulled && _unsaved()) syncPrefs();
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
  if (_pushOn) await _pushOff();  // this device's reminders stop with its session
  const res = await request().catch(() => null);
  if (!res?.ok) { showToast('Couldn’t reach the server', true); return; }
  account = null;
  _store('bb_signed_in', null);
  _store(PREFS_KEY, null);  // the account's cars and homes leave this device
  _setBase(null);
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
  if (!res.ok) throw new Error(await _errorText(res, 'Passkeys aren’t available'));
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
  if (!done.ok) throw new Error(await _errorText(done, 'Couldn’t verify the passkey'));
}

// Canceled: nothing to say. The browser's own errors (DOMException) are not for people.
function _passkeyFailed(e) {
  if (e?.name === 'NotAllowedError') return;
  showToast(e instanceof DOMException || !e?.message ? 'Couldn’t use the passkey' : e.message, true);
}
async function _passkeySignIn() {
  try {
    await _passkeyCeremony(false, '/auth/passkey/login/options', null, '/auth/passkey/login/verify');
    location.reload();
  } catch (e) { _passkeyFailed(e); }
}
async function _passkeyCreate(name) {
  if (!name.trim()) { showToast('Enter a name', true); return; }
  try {
    await _passkeyCeremony(true, '/auth/passkey/register/options',
      { name: name.trim(), invite: _joinToken }, '/auth/passkey/register/verify');
    location.reload();
  } catch (e) { _passkeyFailed(e); }
}
// ── Invites: a single-use link that adds someone to this account ──────────────
async function _makeInvite() {
  const res = await fetch('/api/me/invite', { method: 'POST' }).catch(() => null);
  if (!res?.ok) { showToast(res ? await _errorText(res, 'Couldn’t make an invite') : 'Couldn’t make an invite', true); return; }
  _inviteLink = await res.json();
  renderAccountSection();
}
// Copies link.url; where the clipboard is refused, the link shows in a field instead
// (link.field, focused once drawn).
function _copyItems(label, link) {
  if (link.shown) {
    const field = link.field = Object.assign(document.createElement('input'), {
      className: 'field link-field', readOnly: true, value: link.url });
    field.setAttribute('aria-label', 'Link');
    field.addEventListener('focus', () => field.select());
    return [field];
  }
  return [_menuItem(label, async () => {
    try { await navigator.clipboard.writeText(link.url); showToast('Link copied'); }
    catch (_) {
      link.shown = true;
      renderAccountSection();
      link.field?.focus();
    }
  })];
}
function _inviteItems() {
  if (!_inviteLink) return [_menuItem('Invite someone', _makeInvite)];
  return [..._copyItems('Copy invite link', _inviteLink), _note(`Works once, for ${_inviteLink.days} days.`)];
}

// ── Calendar subscription: a secret link a calendar app polls (sweeping of the cars,
// pickups of the homes, holidays), with a reminder the evening before each. ──────
async function _makeCalendarLink() {
  await _flushPrefs();
  const res = await fetch('/api/me/calendar', { method: 'POST' }).catch(() => null);
  if (!res?.ok) { showToast('Couldn’t make a calendar link', true); return; }
  _calendarUrl = await res.json();
  calendarOn = true;
  renderAccountSection();
}
async function _stopCalendar() {
  if (!await askConfirm('Turn off the calendar link?', 'Calendars subscribed to it stop updating.', 'Turn off')) return;
  const res = await fetch('/api/me/calendar', { method: 'DELETE' }).catch(() => null);
  if (!res?.ok) { showToast('Couldn’t turn it off', true); return; }
  calendarOn = false; _calendarUrl = null;
  renderAccountSection();
}
function _calendarItems() {
  const items = [];
  if (_calendarUrl) {
    const a = Object.assign(document.createElement('a'), {
      className: 'menu-item', href: _calendarUrl.webcal, textContent: 'Subscribe in Calendar' });
    items.push(a, ..._copyItems('Copy link', _calendarUrl),
      _note('Shown once'));
  } else {
    items.push(_menuItem(calendarOn ? 'New calendar link' : 'Add to calendar',
                         _makeCalendarLink));
  }
  if (calendarOn) {
    const off = _menuItem('Turn off calendar link', _stopCalendar);
    off.classList.add('danger');
    items.push(off);
  }
  return items;
}

// ── Notifications (Web Push, api/push.py): the calendar link's reminders (the evening
// before and an hour before each sweep of the account's cars) on this device. iPhone:
// the home-screen app only (Safari has no PushManager), asked from a tap. ─────────────
const _pushOk = 'PushManager' in window && 'serviceWorker' in navigator && 'Notification' in window;
let _pushOn = false;  // this device is subscribed
const _pushSub = async () => (await navigator.serviceWorker.ready).pushManager.getSubscription();
async function _checkPush() {
  if (!_pushOk || !account) return;
  const sub = await _pushSub().catch(() => null);
  _pushOn = !!sub && Notification.permission === 'granted';
  renderAccountSection();
}
async function _pushTurnOn() {
  // First, in the tap: iOS shows the prompt only from a user gesture.
  if (await Notification.requestPermission() !== 'granted') { renderAccountSection(); return; }
  try {
    const keyRes = await fetch('/api/push/key');
    if (!keyRes.ok) throw new Error('key');
    const reg = await navigator.serviceWorker.ready;
    await (await reg.pushManager.getSubscription())?.unsubscribe();  // one made with another key fails
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: _b64uToBuf((await keyRes.json()).key) });
    await _flushPrefs();  // the reminders are of the account's saved cars
    const res = await fetch('/api/push', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(sub) });
    if (!res.ok) { await sub.unsubscribe(); throw new Error('subscribe'); }
    _pushOn = true;
  } catch (_) { showToast('Couldn’t turn on notifications', true); }
  renderAccountSection();
}
async function _pushOff() {
  const sub = await _pushSub().catch(() => null);
  if (sub) {
    await fetch('/api/push', { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => null);
    await sub.unsubscribe().catch(() => {});
  }
  _pushOn = false;
}
function _notifyItems() {
  if (Notification.permission === 'denied') return [_note('Blocked in Settings')];
  if (!_pushOn) return [_menuItem('Turn on notifications', _pushTurnOn)];
  return [_menuItem('Turn off notifications', async () => { await _pushOff(); renderAccountSection(); })];
}

// Signed out. A join link's token travels with the passkey sign-up that uses it.
function _signedOutSection() {
  if (!_passkeysOk) return [];
  const name = Object.assign(document.createElement('input'), {
    className: 'field', placeholder: 'Name', maxLength: 40, autocomplete: 'name' });
  name.setAttribute('aria-label', 'Name');
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
    _menuItem('Sign out', () => _leaveAccount(() => fetch('/auth/logout', { method: 'POST' }))),
    _menuItem('Sign out everywhere', async () => {
      if (!await askConfirm('Sign out everywhere?', 'Every device signed in as you signs out, this one too.', 'Sign out')) return;
      _leaveAccount(() => fetch('/auth/logout-everywhere', { method: 'POST' }));
    }));
  return _section('Account', rows);
}

// Settings (as iOS orders it): who you are first, the calendar link and notifications after Appearance,
// deleting the account last and apart (a destructive action kept away from the rest).
// Without sign-in on the server, none of them.
function renderAccountSection() {
  const fill = (id, nodes) => document.getElementById(id).replaceChildren(...(accountEnabled ? nodes : []));
  fill('set-account', account ? _signedInSection() : _signedOutSection());
  fill('set-calendar', account ? _section('Calendar', _calendarItems()) : []);
  fill('set-notify', account && _pushOk ? _section('Notifications', _notifyItems()) : []);
  // Signed out (or no sign-in): what this device keeps can be cleared here.
  document.getElementById('set-danger').replaceChildren(...(account ? (accountEnabled ? _section('', [_deleteAccountItem()]) : [])
    : (cars.length || homes.length) && !_read('bb_signed_in') ? _section('', [_clearSavedItem()]) : []));
}
// Removes every car and home saved on this device (asked first).
function _clearSavedItem() {
  const el = _menuItem('Remove all saved', async () => {
    if (!await askConfirm('Remove all saved cars and homes?', 'They are removed from this device.', 'Remove')) return;
    clearSelection();
    _setBase(null);  // a later sign-in takes the account's lists, never "all removed here"
    cars = []; homes = []; carSchedules = {}; homeSchedules = {};
    _store(SCHED_KEY, null);
    savePrefs();
    updateCarMarkers(); updateHomeMarkers(); renderHomePanel();
    renderAccountSection();
    showToast('Saved cars and homes removed');
  });
  el.classList.add('danger');
  return el;
}
