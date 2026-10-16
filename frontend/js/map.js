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
  applyMapInsets(false);  // the sheet's padding: the target at the centre of the free area
  _crosshair.classList.add('visible');
  map.on('load', () => {
    map.resize();
    attachMapListeners();
  });
  map.on('error', (e) => console.error('[MapLibre]', e.error));
}

let _mapListenersAttached = false;
let _tapTimer = null;  // deferred single-tap (cancelled by a double-tap zoom)


function attachMapListeners() {
  // Event listeners live on the map instance and survive style changes, so only
  // attach once. Calling setStyle() does NOT remove them.
  if (_mapListenersAttached) return;
  _mapListenersAttached = true;

  // A tap closes the open card, like Esc. The action is deferred ~280 ms so a
  // double-tap-to-zoom can cancel it; a tap while placing or naming is ignored.
  map.on('click', (e) => {
    // Placement commits via the center target ("Place here") or right-click, not
    // a stray tap — a tap during place mode is ignored so panning can't misfire.
    if (placing) return;
    if (_namePopup) return;  // name box open: taps don't open windows or close it
    if (_tapTimer) clearTimeout(_tapTimer);
    _tapTimer = setTimeout(() => { _tapTimer = null; clearSelection(); }, 280);
  });
  map.on('dblclick', () => { if (_tapTimer) { clearTimeout(_tapTimer); _tapTimer = null; } });

  // Signs and calendar: live-read the street under the target while the map
  // moves (drag/zoom), and once more after tiles finish streaming in (idle).
  map.on('move', () => renderTargetInfo());
  map.on('moveend', () => renderTargetInfo());
  map.on('idle', () => renderTargetInfo());

  // Follow the map center to its region (mounts its tiles) and recolour newly
  // loaded tile features once the map settles.
  map.on('moveend', syncRegion);
  map.on('zoom', () => { if (_here) updateHereMarker(); });  // the accuracy halo is in metres
  map.on('idle', scheduleUrgencyUpdate);
  // Colour tiles as they arrive, not only once the whole map is idle: until then
  // their streets are transparent (see _urgCase).
  map.on('sourcedata', (e) => { if (e.sourceId === TILES_SOURCE && e.tile) scheduleUrgencyUpdate(); });
  syncRegion();
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
let _featStateCache   = new Map();  // feature id -> [{urgency, urgency_even?, urgency_odd?}, y-m-d-minute it was computed]
let _featSchedCache   = new Map();  // feature id -> { parsed sched entries, whether the street has no sweeping }
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

// Zoomed in this far, a line whose sides differ (tile property even_left) is drawn as
// two, one per side, each in its own urgency colour; the single line hides.
const SIDE_ZOOM = 15;
const _isSplit = ['==', ['get', 'even_left'], true];  // even side left of the drawing direction
// Offset (px) of one side's line: left of the drawing direction is negative.
function _sideOffset(side) {
  const px = { 15: 2.5, 16: 3.5, 18: 6 }, s = side === 'even' ? 1 : -1;
  return ['interpolate', ['linear'], ['zoom'],
    ...Object.entries(px).flatMap(([z, o]) => [Number(z), ['case', _isSplit, -o * s, o * s]])];
}

function addTilePaintLayers() {
  map.addLayer({
    id: 'zones-fill', type: 'fill', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['get', 'render_type'], 'polygon'],
    paint: { 'fill-color': _urgCase('fill') },
  });
  // The street (or zone) at the target: a soft blue glow under its colour (setActiveFeature).
  map.addLayer({
    id: 'zones-active', type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
    filter: ['==', ['id'], -1],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': 'rgba(59,130,246,0.35)', 'line-blur': 2,
             'line-width': ['interpolate', ['linear'], ['zoom'], 11, 6, 16, 14, 18, 20] },
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
    paint: { 'line-color': _urgCase('line'), 'line-width': _lineWidth(),
             'line-opacity': ['step', ['zoom'], 1, SIDE_ZOOM, ['case', ['has', 'even_left'], 0, 1]] },
  });
  for (const side of ['even', 'odd']) {
    map.addLayer({
      id: `zones-side-${side}`, type: 'line', source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER,
      minzoom: SIDE_ZOOM, filter: ['all', ['==', ['get', 'render_type'], 'line'], ['has', 'even_left']],
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: { 'line-color': _urgCase('line', `urgency_${side}`), 'line-width': _lineWidth(`urgency_${side}`, 0.6),
               'line-offset': _sideOffset(side) },
    });
  }
  _activeId = null;
}

// Glow the feature at the target (id from the tiles; null: none).
let _activeId = null;
function setActiveFeature(id) {
  if (id === _activeId || !map?.getLayer('zones-active')) return;
  _activeId = id;
  map.setFilter('zones-active', ['==', ['id'], id ?? -1]);
}

// ── Areas ─────────────────────────────────────────────────────────────────────
// The region follows the map center (no picker). Zoomed out past AREA_ZOOM the
// map shows one label per available area; a click flies to it.
const _areaMarkers = new Map();  // region key -> maplibregl.Marker

function _ensureAreaLabels() {
  for (const [key, r] of Object.entries(regions)) {
    if (_areaMarkers.has(key)) continue;
    const el = document.createElement('button');
    el.className = 'area-label';
    el.textContent = r.name;
    el.addEventListener('click', e => {
      e.stopPropagation();
      map.flyTo({ center: [r.center.lon, r.center.lat], zoom: r.zoom || 11 });
    });
    _areaMarkers.set(key, new maplibregl.Marker({ element: el }).setLngLat([r.center.lon, r.center.lat]).addTo(map));
  }
}

function syncRegion() {
  if (!map) return;
  _ensureAreaLabels();
  const zoomedOut = map.getZoom() < AREA_ZOOM;
  for (const m of _areaMarkers.values()) m.getElement().hidden = !zoomedOut;
  const c = map.getCenter();
  const key = zoomedOut ? null : regionAt(c.lat, c.lng);
  if (key) _region = key;
  ensureTiles();
}

function ensureTiles() {
  if (!map) return;
  const region = _region;
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

// Throttled, not debounced: a stream of tile loads must not keep postponing the
// pass (applyUrgencyStates skips features it already coloured this minute).
function scheduleUrgencyUpdate() {
  if (_urgencyTimer) return;
  _urgencyTimer = setTimeout(() => { _urgencyTimer = null; applyUrgencyStates(); }, 50);
}

// Compute urgency for every in-view tile feature and push it to feature-state,
// which drives the paint expressions. A verdict is reused only within the
// region-local minute it was computed (a window may close at any minute), and
// feature-state is written only when it differs (a style switch can drop it).
function applyUrgencyStates() {
  if (!map || !_tilesRegion || !map.getSource(TILES_SOURCE)) return;
  const now = regionNow(_tilesRegion);
  const stamp = now.y + '-' + now.m + '-' + now.d + '-' + now.min;
  let feats;
  try { feats = map.querySourceFeatures(TILES_SOURCE, { sourceLayer: TILES_SRC_LAYER }); }
  catch (_) { return; }
  for (const f of feats) {
    if (f.id === undefined || f.id === null) continue;
    let u = _featStateCache.get(f.id);
    u = u && u[1] === stamp ? u[0] : null;
    if (u === null) {
      let sched = _featSchedCache.get(f.id);
      if (sched === undefined) {
        const entries = tileEntries(f.properties);
        // Same test as the calendar's "No sweeping": nothing real to display.
        sched = { entries, none: !tileSchedLines(f.properties).length, split: 'even_left' in f.properties };
        _featSchedCache.set(f.id, sched);
      }
      const verdict = es => sched.none || !es.length ? 'none' : BroomUrgency.checkDaySweeping(es, now);
      u = { urgency: verdict(sched.entries) };
      if (sched.split) {
        u.urgency_even = verdict(sched.entries.filter(e => e.side === 'even'));
        u.urgency_odd = verdict(sched.entries.filter(e => e.side === 'odd'));
      }
      _featStateCache.set(f.id, [u, stamp]);
    }
    const target = { source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id: f.id };
    const cur = map.getFeatureState(target);
    if (Object.keys(u).some(k => cur[k] !== u[k])) map.setFeatureState(target, u);
  }
}

// A tile feature's raw schedule entries [{code, time, desc, side}] ([] if unreadable).
function tileEntries(props) {
  try { return JSON.parse(props.sched || '[]') || []; } catch (_) { return []; }
}

// Canonical schedule lines for a tile feature (days merged per time: "Tue, Thu · 3AM–5AM",
// Mon->Sun order, merged next-cluster dates), as on the car card. Used by the
// calendar and the map colours.
function tileSchedLines(props) {
  const sched = tileEntries(props);
  const now = regionNow(_tilesRegion);
  const labels = [props.side_even || 'Even', props.side_odd || 'Odd'];
  return BroomUrgency.formatBothSides(
    sched.filter(e => e.side === 'even'), sched.filter(e => e.side === 'odd'), now, null, labels);
}
