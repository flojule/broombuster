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
  _btnDark.textContent = on ? '☀️' : '🌙';
  _btnDark.title = on ? 'Switch to light' : 'Switch to dark';
  if (persist) localStorage.setItem('bb_dark', on ? '1' : '0');
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
      if (PMTILES_MODE) { _tilesRegion = null; ensureTiles(); }
      else if (_currentGeojson) addZoneLayers(_currentGeojson);
      updateCarMarkers();
    });
  }
}
(function _initDark() { _applyDark(_wantDark(), false); })();
_btnDark.addEventListener('click', () => _applyDark(!document.body.classList.contains('dark'), true));

// ── Center banner ─────────────────────────────────────────────────────────────
// Always-on pill that reports the street under the viewport center (the
// mobile-friendly equivalent of hovering). Multi-line: street/zone on line 1,
// the schedule (even/odd on their own lines) below — same lines the hover shows.
const _snapChip   = document.getElementById('snap-chip');
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

function updateCenterBanner() {
  if (!map) { _snapChip.classList.remove('visible'); _crosshair.classList.remove('visible'); return; }
  // The center target is always on once the map is up — it marks the point the
  // banner reads (the nearest projected street), independent of any hit.
  _crosshair.classList.add('visible');
  const f = _centerFeature();
  if (!f) { _snapChip.classList.remove('visible'); return; }
  const props = f.properties || {};
  const isPoly = props.render_type === 'polygon';
  const street = props.street || '';
  const lines  = PMTILES_MODE ? tileSchedLines(props) : [];
  // Line 1: the street/zone (no distance). Following lines: the schedule, one
  // per line (even/odd already arrive on their own lines from tileSchedLines).
  const head = isPoly ? `📍 Zone: ${esc(street)}` : `📍 ${esc(street)}`;
  _snapChip.innerHTML = `<div class="sc-line sc-head">${head}</div>`
    + lines.map(l => `<div class="sc-line sc-sched">${esc(l)}</div>`).join('');
  _snapChip.classList.add('visible');
}
