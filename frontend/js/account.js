// ── Account (Google sign-in; cars and homes sync to the signed-in user) ───────
const btnAccount  = document.getElementById('btn-account');
const accountMenu = document.getElementById('account-menu');
let account    = null;   // { email } while signed in
let _syncTimer = null;

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
    btnAccount.hidden = !me.enabled;
    account = me.user;
    btnAccount.classList.toggle('signed-in', !!account);
    btnAccount.title = account ? account.email : 'Sign in';
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

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') _flushPrefs();
});

// ── Account menu ──────────────────────────────────────────────────────────────
function hideAccountMenu() { accountMenu.style.display = 'none'; }

function _menuItem(text, onClick) {
  const el = document.createElement('div');
  el.className = 'ctx-item' + (onClick ? '' : ' ctx-note');
  el.textContent = text;
  if (onClick) el.addEventListener('click', onClick);
  return el;
}

async function _leaveAccount(request) {
  hideAccountMenu();
  await _flushPrefs();
  const res = await request();
  if (!res.ok) { showToast('Request failed', true); return; }
  account = null;
  localStorage.removeItem(PREFS_KEY);  // the account's cars and homes leave this device
  location.reload();
}

btnAccount.addEventListener('click', e => {
  e.stopPropagation();
  if (accountMenu.style.display === 'block') { hideAccountMenu(); return; }
  const items = account
    ? [_menuItem(account.email),
       _menuItem('Sign out', () => _leaveAccount(() => fetch('/auth/logout', { method: 'POST' }))),
       _menuItem('Delete account', () => {
         if (!confirm('Delete your account and its saved cars and homes?')) return;
         _syncTimer = null;
         _leaveAccount(() => fetch('/api/me', { method: 'DELETE' }));
       })]
    : [_menuItem('Sync cars and homes across devices'),
       _menuItem('Sign in with Google', () => { location.href = '/auth/login'; })];
  accountMenu.replaceChildren(...items);
  // Opens to the left of the button (it sits in the right column).
  const r = btnAccount.getBoundingClientRect();
  accountMenu.style.top = r.top + 'px';
  accountMenu.style.right = (window.innerWidth - r.left + 8) + 'px';
  accountMenu.style.display = 'block';
});
document.addEventListener('click', hideAccountMenu);
