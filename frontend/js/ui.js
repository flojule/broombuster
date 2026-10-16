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
  try { if (localStorage.getItem('bb_install_hint')) return; localStorage.setItem('bb_install_hint', '1'); } catch (_) { return; }
  showToast('Tip: Share › Add to Home Screen opens BroomBuster full screen.', false, 8000);
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
