// ── Toast notifications ────────────────────────────────────────────────────────
const _toastContainer = document.getElementById('toast-container');
// The container is a polite live region, so screen readers announce each toast.
function showToast(msg, isError = false, ms = 3100) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = msg;
  el.style.animationDelay = `0s, ${(ms - 400) / 1000}s`;  // toast-in, then toast-out
  _toastContainer.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// Safari on iPhone: once, after the first car or home is saved (the user has seen what
// the app is for), say how to install it full screen. Safari has no install prompt.
function offerInstall() {
  if (navigator.standalone !== false || !/iPhone|iPad/.test(navigator.userAgent)) return;
  if (_read('bb_install_hint')) return;
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
  menu.querySelector('button')?.focus();
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
  return true;
}
document.addEventListener('keydown', e => {
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
  // Browser bars (older Safari, Chrome) take the map's own colour, as html's background does.
  document.querySelector('meta[name="theme-color"]').content = on ? '#121212' : '#ecefea';
  const style = on ? DARK_STYLE : LIGHT_STYLE;
  const styleChanged = _mapStyle !== style;
  _mapStyle = style;
  if (map && styleChanged) {
    // Carry the street source and layers into the new basemap so they never blink
    // out (the ward outline takes the new theme's colour). Their urgency is
    // re-applied once it settles, in case the swap dropped feature-state.
    // The map layers that are on (js/layers.js, sources "lyr-…") come along too.
    map.setStyle(_mapStyle, { transformStyle: (prev, next) => {
      if (!prev?.sources?.[TILES_SOURCE]) return next;
      const overlay = id => id.startsWith(LAYER_PREFIX);
      const ours = prev.layers.filter(l => ZONE_LAYERS.includes(l.id) || overlay(l.source || ''))
        .map(l => l.id === 'zones-ward' ? { ...l, paint: { ...l.paint, 'line-color': _wardLineColor() } } : l);
      const srcs = Object.fromEntries(Object.entries(prev.sources).filter(([k]) => k === TILES_SOURCE || overlay(k)));
      return { ...next, sources: { ...next.sources, ...srcs }, layers: [...next.layers, ...ours] };
    } });
    map.once('idle', scheduleUrgencyUpdate);
  }
}
// Menu choice (auto / light / dark); auto also reacts to the system changing.
function setThemePref(pref) {
  _store('bb_theme', pref === 'auto' ? null : pref);
  _applyDark(_wantDark());
  document.querySelectorAll('#theme-seg button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.theme === pref)));
}
setThemePref(_themePref());
_sysDark.addEventListener('change', () => { if (_themePref() === 'auto') _applyDark(_wantDark()); });
document.getElementById('theme-seg').addEventListener('click', e => {
  const b = e.target.closest('button[data-theme]');
  if (b) setThemePref(b.dataset.theme);
});

// ── Center target ─────────────────────────────────────────────────────────────
// The signs and calendar report the street under the map's centre (the
// mobile-friendly equivalent of hovering); the crosshair marks that point.
const _crosshair  = document.getElementById('center-crosshair');

// Query the feature under the map's centre (small pixel box so thin street lines
// are forgiving on touch). Returns the topmost hit or null.
function _centerFeature() {
  if (!map) return null;
  const layers = HOVER_LAYERS.filter(l => !!map.getLayer(l));
  if (!layers.length) return null;
  const c = map.project(map.getCenter()), pad = 9;
  let feats;
  try {
    feats = map.queryRenderedFeatures(
      [[c.x - pad, c.y - pad], [c.x + pad, c.y + pad]], { layers });
  } catch (_) { return null; }
  return feats.length ? feats[0] : null;
}

// ── Zoom buttons (desktop) ────────────────────────────────────────────────────
document.getElementById('btn-zoom-in').addEventListener('click', () => map?.zoomIn());
document.getElementById('btn-zoom-out').addEventListener('click', () => map?.zoomOut());
