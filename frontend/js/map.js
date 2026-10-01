// ── Map init ──────────────────────────────────────────────────────────────────
function initMap(center) {
  const c = center || DEFAULT_CENTER;
  map = new maplibregl.Map({
    container: 'map',
    style: _mapStyle,
    center: [c.lon, c.lat],
    zoom: c.zoom,
    attributionControl: false,
  });
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  map.on('load', () => {
    map.resize();
    attachMapListeners();
  });
  map.on('error', (e) => console.error('[MapLibre]', e.error));
}

let _mapListenersAttached = false;
let _tapTimer = null;  // deferred single-tap (cancelled by a double-tap zoom)

// Resolve a settled single tap: open the zone detail popup for a hit feature,
// else dismiss any open transient window. Split out of the click listener so the
// double-tap defer can call it after the timer fires.
function handleMapTap(point, lngLat) {
  const features = map.queryRenderedFeatures(point, { layers: HOVER_LAYERS.filter(l => !!map.getLayer(l)) });
  // Tiles carry no detail_html; fetch the full-year popup for clicked zones.
  const poly = features.find(f => f.properties && f.properties.render_type === 'polygon');
  if (poly) fetchZoneDetail(poly.properties, lngLat);
  else dismissMapWindows();
}

function attachMapListeners() {
  // Event listeners live on the map instance and survive style changes, so only
  // attach once. Calling setStyle() does NOT remove them.
  if (_mapListenersAttached) return;
  _mapListenersAttached = true;

  // Hover tooltip
  map.on('mousemove', (e) => {
    if (_hoverSuppressed) return;
    const features = map.queryRenderedFeatures(e.point, { layers: HOVER_LAYERS.filter(l => !!map.getLayer(l)) });
    if (!features.length) { customHoverEl.style.display = 'none'; return; }
    const html = tileHoverHtml(features[0].properties || {});
    if (!html) { customHoverEl.style.display = 'none'; return; }
    customHoverEl.innerHTML = html;
    customHoverEl.style.display = 'block';
    // Clamp using the box's actual width (it now sizes to its widest line).
    const w = customHoverEl.offsetWidth;
    const x = Math.min(e.originalEvent.clientX + 14, window.innerWidth - w - 10);
    const y = Math.max(e.originalEvent.clientY - 10, 10);
    customHoverEl.style.left = Math.max(x, 6) + 'px';
    customHoverEl.style.top  = y + 'px';
  });

  map.getCanvas().addEventListener('mouseleave', () => { customHoverEl.style.display = 'none'; });
  map.on('movestart', () => { customHoverEl.style.display = 'none'; });

  // Tap → show zone detail (Chicago section schedule + PDF link) when a zone is
  // hit; a tap away from a zone dismisses any open window (zone popup, GPS pin
  // popup, car selection) — same as pressing Esc. The schedule/dismiss action is
  // deferred ~280 ms so a double-tap-to-zoom can cancel it (no window flash);
  // placement commits immediately, and a tap while the name box is open cancels it.
  map.on('click', (e) => {
    // Placement commits via the center target ("Place here") or right-click, not
    // a stray tap — a tap during place mode is ignored so panning can't misfire.
    if (placingCar || placingHome) return;
    if (_namePopup) { closeNameBox(); return; }
    if (_tapTimer) clearTimeout(_tapTimer);
    const point = e.point, lngLat = e.lngLat;
    _tapTimer = setTimeout(() => { _tapTimer = null; handleMapTap(point, lngLat); }, 280);
  });
  map.on('dblclick', () => { if (_tapTimer) { clearTimeout(_tapTimer); _tapTimer = null; } });

  // GPS popup position update on move
  map.on('move', updateGpsPinPopup);
  map.on('moveend', updateGpsPinPopup);

  // Center banner: live-read the street under the viewport center while the map
  // moves (drag/zoom), and once more after tiles finish streaming in (idle).
  map.on('move', updateCenterBanner);
  map.on('moveend', updateCenterBanner);
  map.on('idle', updateCenterBanner);

  // Mount the selected region's tiles (it may change without a map event) and
  // recolour newly loaded tile features once the map settles.
  map.on('moveend', ensureTiles);
  map.on('idle', scheduleUrgencyUpdate);
  ensureTiles();
}

// Run `cb` once the style can accept sources/layers. `style.load` is a one-shot
// event: if it already fired before we register, map.once() never calls back.
// isStyleLoaded() can also be briefly false AFTER style.load while the basemap
// loads sprites/tiles. Gating on a styledata listener that re-checks
// isStyleLoaded() covers both cases — the fix for Chicago zones not appearing
// until the user pans/zooms.
function whenStyleReady(cb) {
  if (!map) return;
  if (map.isStyleLoaded()) { cb(); return; }
  const onData = () => {
    if (map.isStyleLoaded()) { map.off('styledata', onData); cb(); }
  };
  map.on('styledata', onData);
}

// ── Vector-tile rendering (PMTiles) ───────────────────────────────────────────
let _tilesRegion      = null;   // region currently mounted as a tile source
let _pmtilesProtocol  = false;  // pmtiles:// protocol registered once
let _featStateCache   = new Map();  // feature id -> [urgency, y-m-d-minute it was computed]
let _featSchedCache   = new Map();  // feature id -> parsed sched entries
let _urgencyTimer     = null;

function _archiveUrl(region) {
  return 'pmtiles://' + window.location.origin + '/tiles/' + region + '.pmtiles';
}

function removeTileLayers() {
  for (const id of ZONE_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(TILES_SOURCE)) map.removeSource(TILES_SOURCE);
}

// Ward outline colour: light on the dark basemap, dark on the light basemap.
function _wardLineColor() {
  return document.body.classList.contains('dark')
    ? 'rgba(245,248,252,0.75)'
    : 'rgba(20,28,46,0.7)';
}

function addTilePaintLayers() {
  map.addLayer({
    id: 'zones-fill', type: 'fill', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'fill-color': _urgCase('fill') },
  });
  map.addLayer({
    id: 'zones-outline', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'line-color': _urgCase('border'), 'line-width': 1.5 },
  });
  // Dissolved ward outlines: a clear neutral line over the urgency fills, kept
  // distinct from (and heavier than) the per-section outlines above. Colour is
  // theme-aware (light line on the dark basemap, dark line on the light one) so
  // it stays visible in both; recomputed when the basemap switches.
  map.addLayer({
    id: 'zones-ward', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'ward_boundary'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': _wardLineColor(),
      'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.0, 13, 2.0, 16, 3.2, 18, 4.5],
    },
  });
  map.addLayer({
    id: 'zones-line', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'line'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': _urgCase('line'), 'line-width': ZONE_LINE_WIDTH },
  });
}

function ensureTiles() {
  if (!map) return;
  const region = regionSelect.value || _renderedRegion;
  if (!region) return;
  whenStyleReady(() => {
    if (!_pmtilesProtocol) {
      try { maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile); } catch (_) {}
      _pmtilesProtocol = true;
    }
    if (_tilesRegion !== region) {
      removeTileLayers();
      _featStateCache.clear(); _featSchedCache.clear();
      map.addSource(TILES_SOURCE, { type: 'vector', url: _archiveUrl(region) });
      addTilePaintLayers();
      _tilesRegion = region;
    }
    scheduleUrgencyUpdate();
  });
}

function scheduleUrgencyUpdate() {
  if (_urgencyTimer) clearTimeout(_urgencyTimer);
  _urgencyTimer = setTimeout(applyUrgencyStates, 150);
}

// Compute urgency for every in-view tile feature and push it to feature-state,
// which drives the paint expressions. A verdict is reused only within the
// region-local minute it was computed (a window may close at any minute), and
// feature-state is written only when the verdict changed.
function applyUrgencyStates() {
  if (!map || !_tilesRegion || !map.getSource(TILES_SOURCE)) return;
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  const stamp = now.y + '-' + now.m + '-' + now.d + '-' + now.min;
  let feats;
  try { feats = map.querySourceFeatures(TILES_SOURCE, { sourceLayer: TILES_SRC_LAYER }); }
  catch (_) { return; }
  for (const f of feats) {
    if (f.id === undefined || f.id === null) continue;
    const prev = _featStateCache.get(f.id);
    if (prev && prev[1] === stamp) continue;
    let entries = _featSchedCache.get(f.id);
    if (entries === undefined) {
      try { entries = JSON.parse(f.properties.sched || '[]') || []; } catch (_) { entries = []; }
      _featSchedCache.set(f.id, entries);
    }
    const u = BroomUrgency.checkDaySweeping(entries, now);
    _featStateCache.set(f.id, [u, stamp]);
    if (prev && prev[0] === u) continue;
    map.setFeatureState(
      { source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id: f.id },
      { urgency: u },
    );
  }
}

// Canonical schedule lines for a tile feature (day-first, "Every <Wd>" merge,
// Mon->Sun order, merged next-cluster dates) — identical to the card and the
// server hover. Shared by the hover tooltip and the center banner.
function tileSchedLines(props) {
  let sched = [];
  try { sched = JSON.parse(props.sched || '[]'); } catch (_) {}
  const tz  = REGION_TZ[_tilesRegion] || 'UTC';
  const now = BroomUrgency.nowForTimeZone(tz);
  const labels = [props.side_even || 'Even', props.side_odd || 'Odd'];
  return BroomUrgency.formatBothSides(
    sched.filter(e => e.side === 'even'), sched.filter(e => e.side === 'odd'), now, null, labels);
}

function tileHoverHtml(props) {
  const lines = tileSchedLines(props);
  // No real schedule (only N/A / no-sweep) → no hover at all.
  return lines.length ? `<b>${esc(props.street || '')}</b><br>${lines.map(esc).join('<br>')}` : '';
}

async function fetchZoneDetail(props, lngLat) {
  let codes = [];
  try { codes = JSON.parse(props.sched || '[]').map(e => e.code).filter(Boolean); } catch (_) {}
  if (!codes.length) return;
  const region = regionSelect.value || _renderedRegion || '';
  const qs = new URLSearchParams({ street: props.street || '', city: props.city || '', region });
  for (const c of new Set(codes)) qs.append('code', c);
  try {
    const res = await apiFetch('/zone/detail?' + qs.toString());
    if (!res.ok) return;
    const data = await res.json();
    if (data.detail_html) showZoneDetail(lngLat, data.detail_html);
  } catch (_) {}
}
