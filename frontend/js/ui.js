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
const _btnDark = document.getElementById('btn-dark-mode');
// Switch UI chrome AND basemap together. persist=true pins an explicit override
// (the toggle); otherwise the choice follows local time (night -> dark).
function _applyDark(on, persist) {
  document.body.classList.toggle('dark', on);
  _btnDark.innerHTML = icon(on ? 'sun' : 'moon');
  _btnDark.title = on ? 'Switch to light' : 'Switch to dark';
  if (persist) localStorage.setItem('bb_dark', on ? '1' : '0');
  document.querySelector('meta[name="theme-color"]').content = on ? '#1e293b' : '#ffffff';
  const style = on ? DARK_STYLE : LIGHT_STYLE;
  const styleChanged = _mapStyle !== style;
  _mapStyle = style;
  if (map && styleChanged) {
    map.setStyle(_mapStyle);
    // Re-mount only after the NEW style settles. whenStyleReady() can't be used:
    // right after setStyle() isStyleLoaded() still reports the OLD style as
    // loaded, so the re-add fires early and the new style then wipes it (street/
    // zone layers vanishing on toggle). 'style.load' is unreliable on setStyle
    // here; 'idle' fires once the swapped style + basemap have settled.
    map.once('idle', () => {
      map.dragRotate.disable();
      map.touchZoomRotate.disableRotation();
      // setStyle() drops all sources/layers; force the tile source to remount.
      _tilesRegion = null; ensureTiles();
      updateCarMarkers();
    });
  }
}
(function _initDark() { _applyDark(_wantDark(), false); })();
_btnDark.addEventListener('click', () => _applyDark(!document.body.classList.contains('dark'), true));

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

// ── Map color key (ⓘ) ──────────────────────────────────────────────────
const _btnLegend = document.getElementById('btn-legend');
const _legendPop = document.getElementById('legend-pop');
function setLegendOpen(on) {
  _legendPop.hidden = !on;
  _btnLegend.setAttribute('aria-expanded', String(on));
}
_btnLegend.addEventListener('click', e => { e.stopPropagation(); setLegendOpen(_legendPop.hidden); });
document.addEventListener('click', () => setLegendOpen(false));

// ── Zoom buttons (desktop) ────────────────────────────────────────────────────
document.getElementById('btn-zoom-in').addEventListener('click', () => map?.zoomIn());
document.getElementById('btn-zoom-out').addEventListener('click', () => map?.zoomOut());
