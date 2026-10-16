// ── Toast notifications ────────────────────────────────────────────────────────
const _toastContainer = document.getElementById('toast-container');
function showToast(msg, isError = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = msg;
  _toastContainer.appendChild(el);
  setTimeout(() => el.remove(), 3100);
}

// ── Dark mode ─────────────────────────────────────────────────────────────────
// Switch UI chrome AND basemap together.
function _applyDark(on) {
  document.body.classList.toggle('dark', on);
  document.querySelector('meta[name="theme-color"]').content = on ? '#1e293b' : '#ffffff';
  const style = on ? DARK_STYLE : LIGHT_STYLE;
  const styleChanged = _mapStyle !== style;
  _mapStyle = style;
  if (map && styleChanged) {
    // Carry the street source and layers into the new basemap so they never blink
    // out (the ward outline takes the new theme's colour). Their urgency is
    // re-applied once it settles, in case the swap dropped feature-state.
    map.setStyle(_mapStyle, { transformStyle: (prev, next) => {
      if (!prev?.sources?.[TILES_SOURCE]) return next;
      const ours = prev.layers.filter(l => ZONE_LAYERS.includes(l.id)).map(l => l.id === 'zones-ward'
        ? { ...l, paint: { ...l.paint, 'line-color': _wardLineColor() } } : l);
      return { ...next, sources: { ...next.sources, [TILES_SOURCE]: prev.sources[TILES_SOURCE] },
               layers: [...next.layers, ...ours] };
    } });
    map.once('idle', scheduleUrgencyUpdate);
  }
}
// Menu choice (auto / light / dark); auto also reacts to the system changing.
function setThemePref(pref) {
  if (pref === 'auto') localStorage.removeItem('bb_theme'); else localStorage.setItem('bb_theme', pref);
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
// The info card reports the street under the viewport center (the
// mobile-friendly equivalent of hovering); the crosshair marks that point.
const _crosshair  = document.getElementById('center-crosshair');

// Query the feature under the viewport center (small pixel box so thin street
// lines are forgiving on touch). Returns the topmost hit or null.
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

function updateInfoCard() {
  if (!map) { _crosshair.classList.remove('visible'); renderInfoCard(null); return; }
  _crosshair.classList.add('visible');
  renderInfoCard(_centerFeature());
}

// ── App menu (⋯): account, appearance, map colors, about ────────────────────────
const _btnMenu = document.getElementById('btn-menu');
const _appMenu = document.getElementById('app-menu');
function setMenuOpen(on) {
  _appMenu.hidden = !on;
  document.body.classList.toggle('menu-open', on);
  _btnMenu.setAttribute('aria-expanded', String(on));
  if (!on) return;
  renderAccountSection();
  // Opens to the left of the button, kept inside the screen.
  const r = _btnMenu.getBoundingClientRect();
  _appMenu.style.right = (window.innerWidth - r.left + 8) + 'px';
  const h = _appMenu.offsetHeight;
  _appMenu.style.top = Math.max(12, Math.min(r.top, window.innerHeight - h - 12)) + 'px';
}
_btnMenu.addEventListener('click', e => { e.stopPropagation(); setMenuOpen(_appMenu.hidden); });
_appMenu.addEventListener('click', e => e.stopPropagation());
document.addEventListener('click', () => setMenuOpen(false));

// ── Zoom buttons (desktop) ────────────────────────────────────────────────────
document.getElementById('btn-zoom-in').addEventListener('click', () => map?.zoomIn());
document.getElementById('btn-zoom-out').addEventListener('click', () => map?.zoomOut());
