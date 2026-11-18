// ── Toast notifications ────────────────────────────────────────────────────────
const _toastContainer = document.getElementById('toast-container');
// The container is a polite live region, so screen readers announce each toast. A note
// fades after `ms`; an error stays until closed (x) and can offer Retry. `opts`: ms, or
// {ms, retry, action: {label, run} (Undo, Reload: a button on a note), sticky}. The same
// message is not shown twice at once.
function showToast(msg, isError = false, opts = {}) {
  if (typeof opts === 'number') opts = { ms: opts };
  if ([..._toastContainer.children].some(t => t.dataset.msg === msg)) return;
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '') + (opts.retry || opts.action || opts.sticky ? ' has-btn' : '');
  el.dataset.msg = msg;
  el.append(Object.assign(document.createElement('span'), { textContent: msg }));
  const btn = (text, label, onClick) => {
    const b = Object.assign(document.createElement('button'), { type: 'button', className: 'toast-btn', textContent: text });
    if (label) b.setAttribute('aria-label', label);
    b.addEventListener('click', () => { el.remove(); onClick?.(); });
    el.append(b);
  };
  if (opts.retry) btn('Retry', '', opts.retry);
  if (opts.action) btn(opts.action.label, '', opts.action.run);
  // An error with Retry (or marked sticky) stays until closed; the rest fade.
  if (opts.retry || opts.sticky) btn('\u00d7', 'Dismiss');
  else {
    const ms = opts.ms || (isError ? 5000 : 3100);
    el.style.animationDelay = `0s, ${(ms - 400) / 1000}s`;  // toast-in, then toast-out
    setTimeout(() => el.remove(), ms);
  }
  _toastContainer.appendChild(el);
}

// ── Connection chip: "Offline", "Can't reach the server" or "Loading streets…" over the
// map, so a failure never reads as "nothing here". ────────────────────────────────
const netChip = document.getElementById('net-chip');
let _citiesFailed = false;  // /api/cities failed (data.js retries it)
let _streetsSlow = false;   // the street tiles have been loading for over a second (map.js)
// Streets are on their way: /api/cities is loading, or the tiles are slow.
function streetsLoading() { return (!Object.keys(regions).length && !_citiesFailed) || _streetsSlow; }
function updateNetChip() {
  const text = !navigator.onLine ? 'Offline' : _citiesFailed && !Object.keys(regions).length ? 'Can’t reach the server'
    : _streetsSlow ? 'Loading streets…' : '';
  if (netChip.textContent !== text) netChip.textContent = text;
  netChip.hidden = !text;
}
window.addEventListener('offline', updateNetChip);
window.addEventListener('online', () => {
  updateNetChip();
  if (_citiesFailed) loadCities();
  for (const c of cars) if (loadFailed.has(c.id)) checkCar(c);
  for (const h of homes) if (loadFailed.has(h.id)) checkHome(h.id);
  loadAlerts();
});

// Once, after the first car or home is saved (the user has seen what the app is for):
// Chrome and Android offer Install (their install prompt); Safari on iPhone, which has
// none, gets the steps.
let _installPrompt = null;
// Held for after the first save; once offered, the browser's own prompts are left alone.
window.addEventListener('beforeinstallprompt', e => { if (_read('bb_install_hint')) return; e.preventDefault(); _installPrompt = e; });
function offerInstall() {
  if (_read('bb_install_hint')) return;
  if (_installPrompt) {
    _store('bb_install_hint', '1');
    const p = _installPrompt;
    _installPrompt = null;
    showToast('Install BroomBuster on this device?', false, { ms: 10000, action: { label: 'Install', run: () => p.prompt() } });
    return;
  }
  if (navigator.standalone !== false || !/iPhone|iPad/.test(navigator.userAgent)) return;
  _store('bb_install_hint', '1');
  showToast('Tip: Share › Add to Home Screen opens BroomBuster full screen.', false, 8000);
}

// ── Menus ("+", an item's "…", Layers, and "Add here" at a point on the map) ──
// As iOS pull-down menus: one open at a time, modal (what they cover takes no taps or
// focus), closed by a pick, a tap elsewhere or Esc (which returns focus to the button).
// The arrow keys, Home and End move between the items.
let _openMenu = null;  // { menu, anchor (null: opened at a point) }
const _clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
// Under `anchor` (or above it when the sheet sits low), or at the point `at` {x, y};
// kept inside the screen. It grows out of the button or the point (styles.css .pop-menu).
function openMenu(menu, anchor, at = null) {
  closeMenu();
  _openMenu = { menu, anchor };
  menu.hidden = false;
  anchor?.setAttribute('aria-expanded', 'true');
  sheet.inert = true;
  // A long menu (Layers and its legend) scrolls rather than covering its button.
  menu.style.maxHeight = anchor && !at ? Math.max(200, window.innerHeight - anchor.getBoundingClientRect().bottom - 14) + 'px' : '';
  const h = menu.offsetHeight, w = menu.offsetWidth, W = window.innerWidth, H = window.innerHeight;
  let left, top, ox, oy;
  if (at) {
    left = _clamp(at.x, 8, W - w - 8); top = _clamp(at.y, 8, H - h - 8);
    ox = at.x; oy = at.y;
  } else {
    const r = anchor.getBoundingClientRect();
    left = _clamp(r.right - w, 8, W - w - 8);
    top = r.bottom + 6 + h < H ? r.bottom + 6 : Math.max(8, r.top - h - 6);
    ox = r.left + r.width / 2; oy = r.top + r.height / 2;
  }
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
  menu.style.transformOrigin = `${ox - left}px ${oy - top}px`;
  (menu.querySelector('input, button'))?.focus();
  syncHistory();
}
// Returns whether a menu was open.
function closeMenu(refocus = false) {
  if (!_openMenu) return false;
  const { menu, anchor } = _openMenu;
  _openMenu = null;
  menu.hidden = true;
  anchor?.setAttribute('aria-expanded', 'false');
  sheet.inert = false;
  if (refocus) anchor?.focus();
  syncHistory();
  return true;
}
document.addEventListener('keydown', e => {
  // Tab stays in the menu (it is modal), going round from its last control to its first.
  if (_openMenu && e.key === 'Tab') {
    const fs = [..._openMenu.menu.querySelectorAll('button, input')].filter(el => !el.disabled && el.offsetParent);
    if (!fs.length) return;
    const i = fs.indexOf(document.activeElement), j = e.shiftKey ? (i <= 0 ? fs.length - 1 : i - 1) : (i + 1) % fs.length;
    e.preventDefault();
    fs[j].focus();
    return;
  }
  if (!_openMenu || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
  const items = [..._openMenu.menu.querySelectorAll('button')];
  if (!items.length) return;
  e.preventDefault();
  const i = items.indexOf(document.activeElement), n = items.length;
  const next = { ArrowDown: (i + 1) % n, ArrowUp: (i - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
  items[next].focus();
});
// A button that toggles `menu` (the click must not reach the closing listener below).
function menuButton(btn, menu, beforeOpen) {
  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (_openMenu?.menu === menu) { closeMenu(); return; }
    beforeOpen?.();
    openMenu(menu, btn);
  });
}
document.addEventListener('click', e => { if (_openMenu && !_openMenu.menu.contains(e.target)) closeMenu(); });

// ── Back button: one history entry while anything closable is open (a menu, the name
// box, placing, an item's page); Back closes the top one (as Escape does, js/data.js), so it
// never leaves the app with something open. Closed by other means, the entry is taken back.
let _historyQueued = false, _ownBack = false;
const _layerOpen = () => !!(_openMenu || _namePopup || placing || _selectedId);
function syncHistory() {
  if (_historyQueued) return;
  _historyQueued = true;
  queueMicrotask(() => {  // several changes in one step (a menu replacing another): one decision
    _historyQueued = false;
    const ours = history.state?.bb === 1;
    if (_layerOpen() && !ours) history.pushState({ bb: 1 }, '');
    else if (!_layerOpen() && ours && !_ownBack) { _ownBack = true; history.back(); }
  });
}
window.addEventListener('popstate', () => {
  if (_ownBack) { _ownBack = false; syncHistory(); return; }
  // Only Forward lands on our entry (opening a layer after Back drops the forward one): its layer is closed.
  if (history.state?.bb === 1) { history.back(); return; }
  dismissLayer(false);
  syncHistory();  // more open: a new entry for them
});

// ── Radio groups (Appearance, a car's side): one Tab stop, the checked radio (else the
// first); the arrow keys check the next or previous one, as native radios do.
function radioTabs(group) {
  const rs = [...group.querySelectorAll('[role="radio"]')];
  const on = rs.find(r => r.getAttribute('aria-checked') === 'true') || rs[0];
  for (const r of rs) r.tabIndex = r === on ? 0 : -1;
}
document.addEventListener('keydown', e => {
  const r = e.target.closest?.('[role="radio"]'), g = r?.closest('[role="radiogroup"]');
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!g || !step) return;
  e.preventDefault();
  const rs = [...g.querySelectorAll('[role="radio"]')];
  const next = rs[(rs.indexOf(r) + step + rs.length) % rs.length];
  next.click();  // a group re-rendered by the click focuses its own new radio (js/cars.js)
  if (next.isConnected) next.focus();
});

// ── Confirmation (as an iOS alert): a modal <dialog>; resolves true on the action ──
const confirmDialog = document.getElementById('confirm-dialog');
function askConfirm(title, message, action) {
  confirmDialog.querySelector('#confirm-title').textContent = title;
  confirmDialog.querySelector('#confirm-msg').textContent = message;
  confirmDialog.querySelector('[value="ok"]').textContent = action;
  confirmDialog.returnValue = '';
  return new Promise(resolve => {
    // Resolved after the dialog has put focus back, so the caller can move it.
    confirmDialog.addEventListener('close', () => setTimeout(() => resolve(confirmDialog.returnValue === 'ok')), { once: true });
    confirmDialog.showModal();
    confirmDialog.querySelector('[value="cancel"]').focus();  // the safe choice is the default
  });
}

// ── Dark mode ─────────────────────────────────────────────────────────────────
// Switch UI chrome AND basemap together.
function _applyDark(on) {
  document.body.classList.toggle('dark', on);
  // Browser bars (older Safari, Chrome) take the map's own color: html's background (styles.css --page).
  document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).backgroundColor;
  const style = on ? DARK_STYLE : LIGHT_STYLE;
  const styleChanged = _mapStyle !== style;
  _mapStyle = style;
  if (map && styleChanged) {
    // Carry the street source and layers into the new basemap so they never blink
    // out (layers with a theme paint take the new theme's, themePaint). Their urgency
    // and the target's marks are re-applied once it settles, in case the swap dropped
    // feature-state.
    // The map layers that are on (js/layers.js, sources "lyr-…") come along too.
    // Lines and areas go under the new basemap's labels, point markers over them.
    map.setStyle(_mapStyle, { transformStyle: (prev, next) => {
      const base = tuneBasemap(next).layers;
      if (!prev?.sources?.[TILES_SOURCE]) return { ...next, layers: base };
      const overlay = id => id.startsWith(LAYER_PREFIX);
      const ours = prev.layers.filter(l => ZONE_LAYERS.includes(l.id) || overlay(l.source || ''))
        .map(l => l.metadata?.bbTheme ? { ...l, paint: { ...l.paint, ...themePaint(l.metadata.bbTheme) } } : l);
      const under = ours.filter(l => l.type !== 'symbol' && l.type !== 'circle'), over = ours.filter(l => !under.includes(l));
      const at = base.findIndex(l => l.id === firstLabelLayer(base));
      const layers = at < 0 ? [...base, ...under, ...over] : [...base.slice(0, at), ...under, ...base.slice(at), ...over];
      const srcs = Object.fromEntries(Object.entries(prev.sources).filter(([k]) => k === TILES_SOURCE || overlay(k)));
      return { ...next, sources: { ...next.sources, ...srcs }, layers };
    } });
    map.once('idle', () => { scheduleUrgencyUpdate(); remarkTarget(); });
  }
}
// Menu choice (auto / light / dark); auto also reacts to the system changing.
function setThemePref(pref) {
  _store('bb_theme', pref === 'auto' ? null : pref);
  _applyDark(_wantDark());
  document.querySelectorAll('#theme-seg button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.theme === pref)));
  radioTabs(document.getElementById('theme-seg'));
}
setThemePref(_themePref());
_sysDark.addEventListener('change', () => { if (_themePref() === 'auto') _applyDark(_wantDark()); });
document.getElementById('theme-seg').addEventListener('click', e => {
  const b = e.target.closest('button[data-theme]');
  if (b) setThemePref(b.dataset.theme);
});

// ── Center target ─────────────────────────────────────────────────────────────
// The signs and calendar report the street under the map's center (the
// mobile-friendly equivalent of hovering); the crosshair marks that point.
const _crosshair  = document.getElementById('center-crosshair');

// Query the feature under the map's center (small pixel box so thin street lines
// are forgiving on touch): the street nearest the center (the box can hold a cross
// street), else the zone there, else null.
function _centerFeature() {
  if (!map || map.getZoom() < STREET_READ_ZOOM) return null;  // zoomed out a line is a block of streets
  const layers = HOVER_LAYERS.filter(l => !!map.getLayer(l));
  if (!layers.length) return null;
  const c = map.project(map.getCenter()), pad = 9;
  let feats;
  try {
    feats = map.queryRenderedFeatures(
      [[c.x - pad, c.y - pad], [c.x + pad, c.y + pad]], { layers });
  } catch (_) { return null; }
  let best = null, bestD = Infinity;
  for (const f of feats) {
    const d = nearestSegment(f, c)?.d ?? Infinity;
    if (d < bestD) { bestD = d; best = f; }
  }
  return best || feats[0] || null;
}

// Whether a street or zone of the data is drawn within `m` meters of the target (at any
// zoom); null while unknown (no street tiles here, or still loading).
function streetNear(m) {
  if (!map) return null;
  const layers = HOVER_LAYERS.filter(l => map.getLayer(l));
  const c = map.getCenter(), p = map.project(c), r = Math.min(m / metersPerPx(c.lat), 400);
  try {
    if (!layers.length || !map.isSourceLoaded(TILES_SOURCE)) return null;
    return map.queryRenderedFeatures([[p.x - r, p.y - r], [p.x + r, p.y + r]], { layers }).length > 0;
  } catch (_) { return null; }
}

// The segment of a street (line feature) nearest the screen point c: {d: distance (px),
// dir: [dx, dy] its drawing direction on screen, at: the nearest point on it}; null for a zone.
function nearestSegment(f, c) {
  const g = f.geometry;
  const parts = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : [];
  let best = null;
  for (const part of parts) {
    const pts = part.map(p => map.project(p));
    for (let i = 0; i + 1 < pts.length; i++) {
      const a = pts[i], b = pts[i + 1], dx = b.x - a.x, dy = b.y - a.y;
      const t = Math.max(0, Math.min(1, ((c.x - a.x) * dx + (c.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
      const d = Math.hypot(a.x + t * dx - c.x, a.y + t * dy - c.y);
      if (!best || d < best.d) best = { d, dir: [dx, dy], at: { x: a.x + t * dx, y: a.y + t * dy } };
    }
  }
  return best;
}

// ── Find an address: the map goes there, with a pin on the address and the target on
// the curb of the nearest street with data (the card and calendar read that street).
// Searched in the region on the map, then everywhere (a cold open shows no region).
const btnSearch = document.getElementById('btn-search'), searchBox = document.getElementById('search-box');
const SNAP_M = 60;  // the address's street: within this distance of the geocoded point
let _searchPin = null;
menuButton(btnSearch, searchBox);
btnSearch.addEventListener('click', () => { if (!searchBox.hidden) { searchMsg.textContent = ''; searchBox.q.focus(); } });
searchBox.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); closeMenu(true); } });
// A failed search says why under the field, which stays open to fix the address.
const searchMsg = searchBox.querySelector('.search-msg');
searchBox.q.addEventListener('input', () => { searchMsg.textContent = ''; });
searchBox.addEventListener('submit', async e => {
  e.preventDefault();
  const q = searchBox.q.value.trim();
  if (!map) return;
  if (!q) { searchMsg.textContent = 'Type an address.'; return; }
  const c = map.getCenter(), region = map.getZoom() >= AREA_ZOOM ? _region || regionAt(c.lat, c.lng) : null;
  let hit;
  searchMsg.textContent = '';
  try { hit = await geocode(q, region) || (region ? await geocode(q, null) : null); }
  catch (_) { searchMsg.textContent = 'Couldn’t look up the address. Try again.'; return; }
  if (!hit) { searchMsg.textContent = 'Address not found. Add the city?'; searchBox.q.select(); return; }
  closeMenu(true);
  goToAddress(hit, q);
});
function goToAddress(hit, label) {
  _searchPin = placeMarker(_searchPin, hit, () => {
    const el = document.createElement('div');
    el.className = 'pin search-pin';
    el.innerHTML = '<svg class="ic"><use class="pin-halo" href="#i-pin"/><use href="#i-pin"/></svg>';
    return el;
  });
  _searchPin.getElement().setAttribute('aria-label', label);
  _searchPin.getElement().title = label;
  map.jumpTo({ center: [hit.lon, hit.lat], zoom: 17 });
  let moved = false;  // the user panned before the streets loaded: leave the map where they put it
  const onMove = e => { if (e.originalEvent) moved = true; };
  map.on('movestart', onMove);
  // The street the address names ("1500 Valencia St": VALENCIA), when it is among those near.
  const words = label.toUpperCase().replace(/[^A-Z0-9 ]/g, ' ');
  const named = f => {
    const n = String(f.properties.street || '').toUpperCase().replace(/[^A-Z0-9 ]/g, ' ').split(/\s+/)
      .filter(w => w.length > 2 && !/^(ST|AVE|BLVD|STREET|AVENUE|ROAD|DRIVE|WAY|LANE|PLACE|COURT)$/.test(w));
    return n.length > 0 && n.every(w => new RegExp(`\\b${w}\\b`).test(words));
  };
  // Once the streets there have loaded: the target onto the nearest curb line of that street,
  // else of any street.
  map.once('idle', () => {
    map.off('movestart', onMove);
    if (moved) return;
    const p = map.project([hit.lon, hit.lat]), r = SNAP_M / metersPerPx(hit.lat);
    const layers = ['zones-line', 'zones-side-even', 'zones-side-odd'].filter(l => map.getLayer(l));
    let best = null;
    try {
      for (const f of map.queryRenderedFeatures([[p.x - r, p.y - r], [p.x + r, p.y + r]], { layers })) {
        const n = nearestSegment(f, p);
        if (!n) continue;
        n.named = named(f);
        if (!best || n.named > best.named || (n.named === best.named && n.d < best.d)) best = n;
      }
    } catch (_) {}
    if (best && best.d <= r) map.easeTo({ center: map.unproject([best.at.x, best.at.y]), duration: 300 });
  });
}

// ── Zoom buttons (desktop) ────────────────────────────────────────────────────
document.getElementById('btn-zoom-in').addEventListener('click', () => map?.zoomIn());
document.getElementById('btn-zoom-out').addEventListener('click', () => map?.zoomOut());
