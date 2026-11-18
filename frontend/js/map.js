// ── Map init ──────────────────────────────────────────────────────────────────
function initMap(center) {
  const c = center || DEFAULT_CENTER;
  map = new maplibregl.Map({
    container: 'map',
    style: _mapStyle,
    center: [c.lon, c.lat],
    zoom: c.zoom,
    attributionControl: false,
    transformStyle: (prev, next) => tuneBasemap(next),
  });
  // An icon the basemap's sprite lacks ("wood-pattern"): a transparent pixel, no warning.
  map.setMissingStyleImageResolver(id => {
    if (!map.hasImage(id)) map.addImage(id, { width: 1, height: 1, data: new Uint8Array(4) });
  });
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  applyMapInsets(false);  // the sheet's padding: the target at the center of the free area
  _crosshair.classList.add('visible');
  map.on('load', () => {
    map.resize();
    attachMapListeners();
  });
  map.on('error', (e) => console.error('[MapLibre]', e.error));
}

let _mapListenersAttached = false;
let _tapTimer = null;  // deferred single-tap (canceled by a double-tap zoom)


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
    // A tapped marker (a charger, a market) or street comes under the target, so the card
    // and calendar read it (js/layers.js overlayMarkersAtTarget).
    const marker = markerTapped(e.point);
    if (marker || _streetAt(e.point)) map.easeTo({ center: marker || e.lngLat });
    if (_tapTimer) clearTimeout(_tapTimer);
    _tapTimer = setTimeout(() => { _tapTimer = null; clearSelection(); }, 280);
  });
  map.on('dblclick', () => { if (_tapTimer) { clearTimeout(_tapTimer); _tapTimer = null; } });

  // Signs and calendar: live-read the street under the target while the map pans (the
  // sign and the glow; zooming, the glow only, as every frame brings other streets under
  // it), and in full once it stops and after tiles finish streaming in (idle).
  map.on('move', () => {
    if (map.isZooming()) setActiveFeature(_centerFeature()?.id ?? null);
    else renderTargetInfo(undefined, true);
  });
  map.on('move', syncCrosshair);
  map.on('moveend', () => renderTargetInfo());
  map.on('idle', () => renderTargetInfo());

  // Follow the map center to its region (mounts its tiles) and recolor newly
  // loaded tile features once the map settles.
  map.on('moveend', syncRegion);
  map.on('zoomend', syncOverlaysForZoom);  // a layer the zoom hid loads once shown (js/layers.js)
  map.on('zoom', () => { if (_here) updateHereMarker(); });  // the accuracy halo is in meters
  map.on('idle', scheduleUrgencyUpdate);
  // Color tiles as they arrive, not only once the whole map is idle: until then
  // their streets are transparent (see _urgCase).
  map.on('sourcedata', (e) => { if (e.sourceId === TILES_SOURCE && e.tile) scheduleUrgencyUpdate(); });
  // Street tiles still loading after a second: the chip says so (ui.js), until the map settles.
  let slowTimer = 0;
  map.on('sourcedataloading', (e) => {
    if (e.sourceId !== TILES_SOURCE || slowTimer || _streetsSlow) return;
    slowTimer = setTimeout(() => { _streetsSlow = true; updateNetChip(); renderTargetInfo(); }, 1000);
  });
  map.on('idle', () => {
    clearTimeout(slowTimer); slowTimer = 0;
    if (_streetsSlow) { _streetsSlow = false; updateNetChip(); renderTargetInfo(); }
  });
  syncRegion();
}

// Ground meters per screen pixel at latitude `lat` and the map's zoom (512 px tiles).
function metersPerPx(lat) { return 40075016.686 * Math.cos(lat * Math.PI / 180) / (512 * 2 ** map.getZoom()); }

// Whether a street line is drawn within 8 px of a screen point.
function _streetAt(p) {
  const layers = ['zones-line', 'zones-side-even', 'zones-side-odd'].filter(l => map.getLayer(l));
  if (!layers.length) return false;
  try { return map.queryRenderedFeatures([[p.x - 8, p.y - 8], [p.x + 8, p.y + 8]], { layers }).length > 0; }
  catch (_) { return false; }
}

// Run `cb` once the style can accept sources/layers: now, else at the first styledata
// or idle event that finds it loaded (isStyleLoaded() is false while tiles stream in,
// and after an instant jump no styledata may follow, only idle).
function whenStyleReady(cb) {
  if (!map) return;
  if (map.isStyleLoaded()) { cb(); return; }
  const check = () => {
    if (!map.isStyleLoaded()) return;
    map.off('styledata', check); map.off('idle', check);
    cb();
  };
  map.on('styledata', check);
  map.on('idle', check);
}

// ── Vector-tile rendering (PMTiles) ───────────────────────────────────────────
let _tilesRegion      = null;   // region currently mounted as a tile source
let _pmtilesProtocol  = false;  // pmtiles:// protocol registered once
let _tilesUrl         = null;   // archive URL mounted (a new tiles_version remounts)
let _verdicts         = new Map();  // city|split|sched -> {urgency, urgency_even?, urgency_odd?} for _verdictStamp
let _verdictStamp     = '';         // region-local y-m-d-minute the verdicts are for
let _featState        = new Map();  // feature id of the last pass -> verdict object written to it
let _urgencyTimer     = null;

// The region's street archive; ?v=<content hash> (immutable) when /api/cities gives one.
function _archiveUrl(region) {
  const v = regions[region]?.tiles_version;
  return 'pmtiles://' + window.location.origin + '/tiles/' + region + '.pmtiles' + (v ? '?v=' + encodeURIComponent(v) : '');
}

// The pmtiles protocol's tile loader with concurrent requests for one tile shared and the
// last tiles kept (TILE_CACHE_BYTES), so tiles MapLibre drops and asks again for are not refetched.
const TILE_CACHE_BYTES = 8 * 1024 * 1024;
function cachedTileLoader(load) {
  const done = new Map(), pending = new Map();
  let bytes = 0;
  const keep = (url, data) => {
    done.set(url, data); bytes += data.byteLength;
    for (const [k, d] of done) { if (bytes <= TILE_CACHE_BYTES) break; done.delete(k); bytes -= d.byteLength; }
  };
  return (params, abort) => {
    const url = params.url;
    const hit = done.get(url);
    if (hit) { done.delete(url); done.set(url, hit); return Promise.resolve({ data: hit.slice() }); }
    let p = pending.get(url);
    if (!p) {
      const ac = new AbortController();
      p = { ac, users: 0, res: load(params, ac).then(r => {
        if (r.data && params.type !== 'json') keep(url, r.data);
        return r;
      }).finally(() => { if (pending.get(url) === p) pending.delete(url); }) };
      pending.set(url, p);
    }
    p.users++;
    // A caller's abort ends its wait; the fetch stops when no caller is left.
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        if (--p.users === 0) { p.ac.abort(); pending.delete(url); }
        reject(new DOMException('Aborted', 'AbortError'));
      };
      abort.signal.addEventListener('abort', onAbort, { once: true });
      p.res.then(r => {
        abort.signal.removeEventListener('abort', onAbort);
        if (!abort.signal.aborted) resolve(params.type === 'json' ? r : { ...r, data: r.data && r.data.slice() });
      }, e => { abort.signal.removeEventListener('abort', onAbort); reject(e); });
    });
  };
}

function removeTileLayers() {
  for (const id of ZONE_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  for (const id of [TILES_SOURCE, LABELS_SOURCE]) if (map.getSource(id)) map.removeSource(id);
}

// Paint that follows the theme, by the layer's metadata.bbTheme (also js/layers.js; the
// basemap switch re-applies it, js/ui.js): ward lines; a zone's thin edge (rim); the halo
// and outline of the zone at the target (focus); edge labels.
// Street lines: 'line:<key>' (key: the feature-state, 'urgency' or a side's), solid and
// clear where the verdict is Check sign (drawn dashed by 'check'); 'casing:<key>' under them.
function themePaint(kind) {
  const dark = document.body.classList.contains('dark');
  const halo = dark ? 'rgba(0,0,0,0.85)' : 'rgba(255,255,255,0.95)';
  const [k, key] = kind.split(':');
  if (k === 'line') return { 'line-color': _urgCase('line', key, u => u === 'check' ? 'rgba(0,0,0,0)' : lineColor(u)) };
  if (k === 'check') return { 'line-color': lineColor('check') };
  // Dark under today and tomorrow (red from amber by more than hue), light under Clear
  // (a light basemap's paths are the same thin green).
  if (k === 'casing') {
    return { 'line-color': ['match', ['coalesce', ['feature-state', key], ''],
      'safe', dark ? 'rgba(0,0,0,0)' : 'rgba(255,255,255,0.95)', dark ? 'rgba(0,0,0,0.75)' : 'rgba(15,23,42,0.55)'] };
  }
  return {
    ward:  { 'line-color': dark ? 'rgba(245,248,252,0.75)' : 'rgba(20,28,46,0.7)' },
    rim:   { 'line-color': dark ? 'rgba(255,255,255,0.6)' : 'rgba(30,41,59,0.7)' },
    halo:  { 'line-color': halo },
    focus: { 'line-color': dark ? '#e2e8f0' : '#0f172a' },
    label: { 'text-color': dark ? '#e2e8f0' : '#334155', 'text-halo-color': dark ? '#111827' : '#ffffff' },
    // Curb schedules: in the side's card color when swept today or tomorrow.
    sched: { 'text-color': ['match', ['get', 'u'], 'today', bodyVar('--urg-today'), 'tomorrow', bodyVar('--urg-tomorrow'),
                            dark ? '#cbd5e1' : '#334155'],
             'text-halo-color': dark ? '#111827' : '#ffffff' },
  }[kind];
}
const bodyVar = name => getComputedStyle(document.body).getPropertyValue(name).trim();  // per theme
const themed = (kind, paint = {}) => ({ metadata: { bbTheme: kind }, paint: { ...paint, ...themePaint(kind) } });

// A tile zone's edge (Chicago): a band of the zone's color just inside its edge, under the
// streets (where two zones meet, both colors show side by side), and a thin rim on the
// edge itself, over the streets. A positive offset is inside (tippecanoe orders the rings).
const BAND_W = { 11: 3, 14: 6, 16: 10, 18: 14 };
const bandWidth = () => ['interpolate', ['linear'], ['zoom'], ...Object.entries(BAND_W).flatMap(([z, w]) => [Number(z), w])];
const bandOffset = () => ['interpolate', ['linear'], ['zoom'], ...Object.entries(BAND_W).flatMap(([z, w]) => [Number(z), w / 2])];
const RIM_W = ['interpolate', ['linear'], ['zoom'], 11, 0.6, 16, 1.2];
// A map layer's area (js/layers.js): a thin edge, bolder for the area at the target.
const EDGE_W = ['interpolate', ['linear'], ['zoom'], 11, 1, 16, 1.5];
const HERE_W = ['interpolate', ['linear'], ['zoom'], 11, 2, 16, 3];
// The zone at the target: a bold outline on a halo, shown by feature-state `key`.
const focusOn = key => ['case', ['boolean', ['feature-state', key], false], 1, 0];
const FOCUS_W = ['interpolate', ['linear'], ['zoom'], 11, 2, 16, 4, 18, 5.5];
const HALO_W  = ['interpolate', ['linear'], ['zoom'], 11, 4, 16, 8, 18, 11];

// Zoomed in this far, every street is drawn as its two curbs, each in its side's urgency
// color, CURB_M from the center line (the single line hides); the tiles end each block
// short of the crossing streets (scripts/build_pmtiles.py trim_at_crossings).
const CURB_ZOOM = 16;
const CURB_M = 4.5;
const STREET_ZOOM = 12;  // tile lines are single streets from here (scripts/build_pmtiles.py _LOW_MAXZOOM + 1)
// Ground meters per px at zoom z and latitude lat (512 px tiles).
const _mPerPx = (z, lat) => 40075016.686 * Math.cos(lat * Math.PI / 180) / (512 * 2 ** z);
// Line offset (px) of `m` meters (plus `px`) to the `left` (an expression) of the drawing
// direction, else to its right, at the latitude of the region's tiles.
function curbOffset(left, m = CURB_M, px = 0) {
  const lat = map.getCenter().lat;
  return ['interpolate', ['linear'], ['zoom'], ...[CURB_ZOOM - 1, 16, 17, 18, 19, 20, 22].flatMap(z => {
    const o = m / _mPerPx(z, lat) + px;
    return [z, ['case', left, -o, o]];
  })];
}
// A side's curb is left of the drawing direction: the even side when even_left (a line whose
// sides share a schedule has none: either way).
const _evenLeft = ['!=', ['get', 'even_left'], false];
const curbLeft = side => side === 'even' ? _evenLeft : ['!', _evenLeft];

// Where the basemap's labels start: the layer after its last road, area or building (some
// styles put a water label among those). Street colors go there: over the roads, under
// the names, so the names stay readable. Our own layers are skipped (point markers sit on top).
function firstLabelLayer(layers = map.getStyle().layers) {
  const base = layers.filter(l => !ZONE_LAYERS.includes(l.id) && !l.id.startsWith(LAYER_PREFIX));
  return base[base.findLastIndex(l => l.type !== 'symbol') + 1]?.id;
}

// The basemap style as loaded (initMap; the theme switch, js/ui.js): place names end at
// street zoom (they would sit on the target); the dark style's street and place names,
// dim grey on black, are brighter with a dark halo; filters are made quiet.
const PLACE_MAXZOOM = 14;
const DARK_LABEL = { 'text-color': '#b8c0cc', 'text-halo-color': 'rgba(0,0,0,0.9)', 'text-halo-width': 1.5 };
function _darkLabel(l) { return l.type === 'symbol' && /^(highway_name|place_)/.test(l.id); }
function tuneBasemap(style) {
  const dark = document.body.classList.contains('dark');
  return { ...style, layers: style.layers.map(l => {
    if (l.filter) l = { ...l, filter: _quietFilter(l.filter) };
    if (l.type === 'symbol' && /^(place|label)_/.test(l.id)) l = { ...l, maxzoom: Math.min(l.maxzoom ?? 24, PLACE_MAXZOOM) };
    return dark && _darkLabel(l) ? { ...l, paint: { ...l.paint, ...DARK_LABEL } } : l;
  }) };
}
// A number comparison on a property some features lack (["<=", ["get", "ref_length"], 6]):
// those compare false, as before, without MapLibre's warning about the null.
const _MISSING = { '<': 1e9, '<=': 1e9, '>': -1e9, '>=': -1e9 };
function _quietFilter(f) {
  if (!Array.isArray(f)) return f;
  if (_MISSING[f[0]] && Array.isArray(f[1]) && f[1][0] === 'get' && f[1].length === 2 && typeof f[2] === 'number') {
    return [f[0], ['coalesce', f[1], _MISSING[f[0]]], f[2]];
  }
  return f.map(_quietFilter);
}

function addTilePaintLayers() {
  const below = firstLabelLayer();
  const addLayer = def => map.addLayer({ source: TILES_SOURCE, 'source-layer': TILES_SRC_LAYER, ...def }, below);
  const zone = ['==', ['get', 'render_type'], 'polygon'];
  // Zoomed out, the fills fade (a city's zones would flood the map).
  addLayer({ id: 'zones-fill', type: 'fill', filter: zone,
             paint: { 'fill-color': _urgCase('fill'), 'fill-opacity': ['interpolate', ['linear'], ['zoom'], AREA_ZOOM, 0.35, 13, 1] } });
  // The street at the target: a soft blue glow under its color. Every street has the
  // layer; feature-state `active` shows one (setActiveFeature). Zoomed out past
  // STREET_ZOOM a tile line is every street of one schedule (scripts/build_pmtiles.py): none.
  addLayer({
    id: 'zones-active', type: 'line', filter: ['!', zone], minzoom: STREET_ZOOM,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': 'rgba(59,130,246,0.35)', 'line-blur': 2, 'line-opacity': focusOn('active'),
             'line-width': ['interpolate', ['linear'], ['zoom'], 11, 6, 16, 14, 18, 20] },
  });
  // A zone's edge (bandWidth): its band in its urgency color, fainter with no sweeping.
  addLayer({
    id: 'zones-band', type: 'line', filter: zone, layout: { 'line-join': 'round' },
    paint: { 'line-color': _urgCase('band'), 'line-width': bandWidth(), 'line-offset': bandOffset(),
             'line-opacity': ['match', ['coalesce', ['feature-state', 'urgency'], ''], 'none', 0.3, 0.6] },
  });
  // Dissolved ward outlines: a heavy neutral line, over the zones' bands.
  addLayer({
    id: 'zones-ward', type: 'line', filter: ['==', ['get', 'render_type'], 'ward_boundary'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    ...themed('ward', { 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.0, 13, 2.0, 16, 3.2, 18, 4.5] }),
  });
  // Under a street swept today or tomorrow, a dark casing; under Clear, a light one (themePaint).
  const cased = key => ['match', ['coalesce', ['feature-state', key], ''], 'today', 1, 'tomorrow', 1, 'safe', 1, 0];
  // Zoomed out, the lines fade in (a city of colored streets would flood the map); from
  // CURB_ZOOM every street is drawn as its two curbs instead.
  // `on`: 1, or an expression that hides some (a zoom curve must be the outermost expression).
  const lineOpacity = (on = 1) => ['interpolate', ['linear'], ['zoom'],
    AREA_ZOOM, ['*', 0.45, on], 13, on, CURB_ZOOM - 0.01, on, CURB_ZOOM, 0];
  const isLine = ['==', ['get', 'render_type'], 'line'];
  const CURB_SCALE = 0.8;  // a curb's width, of a single line's
  addLayer({
    id: 'zones-casing', type: 'line', filter: isLine,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    ...themed('casing:urgency', { 'line-width': _lineWidth('urgency', 1, 2),
      'line-opacity': lineOpacity(cased('urgency')) }),
  });
  for (const side of ['even', 'odd']) {
    addLayer({
      id: `zones-casing-${side}`, type: 'line', minzoom: CURB_ZOOM, filter: isLine,
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      ...themed(`casing:urgency_${side}`, { 'line-width': _lineWidth(`urgency_${side}`, CURB_SCALE, 2), 'line-offset': curbOffset(curbLeft(side)),
                                            'line-opacity': cased(`urgency_${side}`) }),
    });
  }
  addLayer({
    id: 'zones-line', type: 'line', filter: isLine,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    ...themed('line:urgency', { 'line-width': _lineWidth(), 'line-opacity': lineOpacity() }),
  });
  for (const side of ['even', 'odd']) {
    addLayer({
      id: `zones-side-${side}`, type: 'line', minzoom: CURB_ZOOM, filter: isLine,
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      ...themed(`line:urgency_${side}`, { 'line-width': _lineWidth(`urgency_${side}`, CURB_SCALE), 'line-offset': curbOffset(curbLeft(side)) }),
    });
  }
  // Check sign: dashed, so it never reads as the solid grey of No sweeping.
  const isCheck = key => ['case', ['==', ['feature-state', key], 'check'], 1, 0];
  addLayer({
    id: 'zones-check', type: 'line', filter: isLine,
    ...themed('check', { 'line-width': _lineWidth(), 'line-dasharray': [2, 1.5],
      'line-opacity': lineOpacity(isCheck('urgency')) }),
  });
  for (const side of ['even', 'odd']) {
    addLayer({
      id: `zones-check-${side}`, type: 'line', minzoom: CURB_ZOOM, filter: isLine,
      ...themed('check', { 'line-width': _lineWidth(`urgency_${side}`, CURB_SCALE), 'line-offset': curbOffset(curbLeft(side)),
                           'line-dasharray': [2, 1.5], 'line-opacity': isCheck(`urgency_${side}`) }),
    });
  }
  // Each curb's permit area (tile properties pl / pr: left / right of the drawing direction):
  // a dotted line in the area's color just outside the curb's color, while Permit areas is on.
  for (const [side, left] of [['l', true], ['r', false]]) {
    addLayer({
      id: `zones-permit-${side}`, type: 'line', minzoom: CURB_ZOOM, filter: ['all', isLine, ['has', 'p' + side]],
      layout: { 'line-cap': 'round', 'line-join': 'round', visibility: permitEdgesOn() ? 'visible' : 'none' },
      paint: { 'line-color': ['get', 'p' + side], 'line-width': 2, 'line-dasharray': [0.1, 2],
               'line-offset': curbOffset(left, CURB_M, 5) },
    });
  }
  // Each curb's schedule as text beside it, close in (curbLabels: a GeoJSON source).
  if (!map.getSource(LABELS_SOURCE)) map.addSource(LABELS_SOURCE, { type: 'geojson', data: _noFeatures });
  map.addLayer({
    id: 'zones-sched', type: 'symbol', source: LABELS_SOURCE, minzoom: LABEL_ZOOM,
    layout: { 'symbol-placement': 'line-center', 'text-field': ['get', 't'], 'text-size': 11,
              'text-font': ['Noto Sans Bold'], 'text-max-angle': 30, 'text-padding': 4 },
    ...themed('sched', { 'text-halo-width': 1.6 }),
  }, below);
  _labelsKey = '';
  // Over the streets: each zone's rim, then the zone at the target outlined (feature-state
  // `active`), and the zones' names along the inside of their edges (Chicago's sections
  // have no other name on the map).
  addLayer({ id: 'zones-edge', type: 'line', filter: zone, ...themed('rim', { 'line-width': RIM_W }) });
  addLayer({ id: 'zones-here-halo', type: 'line', filter: zone, layout: { 'line-join': 'round' },
             ...themed('halo', { 'line-width': HALO_W, 'line-opacity': focusOn('active') }) });
  addLayer({ id: 'zones-here', type: 'line', filter: zone, layout: { 'line-join': 'round' },
             ...themed('focus', { 'line-width': FOCUS_W, 'line-opacity': focusOn('active') }) });
  addLayer({
    id: 'zones-label', type: 'symbol', filter: zone, minzoom: 14,
    layout: { 'symbol-placement': 'line', 'symbol-spacing': 260, 'text-field': ['upcase', ['coalesce', ['get', 'street'], '']],
              'text-font': ['Noto Sans Bold'], 'text-size': 11, 'text-offset': [0, 1.3], 'text-letter-spacing': 0.04 },
    ...themed('label', { 'text-halo-width': 1.8 }),
  });
  _activeId = null;
}

// Mark the feature at the target (id from the tiles; null: none): a street glows, a zone is
// outlined, and so is a permit area there (markOverlayAtTarget, js/layers.js). Feature-state,
// not a filter: a filter change re-parses the source's tiles in a worker, which lagged
// the glow several frames behind a pan across many streets.
let _activeId = null;
// Move the true feature-state `key` from feature `from` to `to` (null: none); at(id): the feature.
function swapState(at, key, from, to) {
  if (from != null) map.setFeatureState(at(from), { [key]: false });
  if (to != null) map.setFeatureState(at(to), { [key]: true });
}
const _tileAt = id => ({ source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id });
function setActiveFeature(id) {
  markOverlayAtTarget();
  if (id === _activeId || !map?.getSource(TILES_SOURCE)) return;
  swapState(_tileAt, 'active', _activeId, id);
  _activeId = id;
}
// The basemap switched (js/ui.js): feature-state may be gone or kept; mark the target and
// check the street colors afresh.
function remarkTarget() {
  if (map.getSource(TILES_SOURCE)) swapState(_tileAt, 'active', _activeId, null);
  _activeId = null;
  _featState = new Map();
  resetOverlayMarks();
  renderTargetInfo();
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
    el.addEventListener('click', e => { e.stopPropagation(); flyToRegion(key); });
    _areaMarkers.set(key, new maplibregl.Marker({ element: el }).setLngLat([r.center.lon, r.center.lat]).addTo(map));
  }
}

function flyToRegion(key) {
  const r = regions[key];
  map.flyTo({ center: [r.center.lon, r.center.lat], zoom: r.zoom || 11 });
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

// Waits for /api/cities to answer or fail (_citiesSettled, js/data.js): the archive URL
// carries its tiles_version.
function ensureTiles() {
  if (!map || !_region || !_citiesSettled) return;
  whenStyleReady(() => {
    const region = _region;  // now, not when queued: an older callback must not switch back
    if (!_pmtilesProtocol) {
      try { maplibregl.addProtocol('pmtiles', cachedTileLoader(new pmtiles.Protocol().tile)); } catch (_) {}
      _pmtilesProtocol = true;
    }
    if (_tilesUrl !== _archiveUrl(region)) {
      removeTileLayers();
      _verdicts = new Map(); _verdictStamp = ''; _featState = new Map();
      _tilesUrl = _archiveUrl(region);
      map.addSource(TILES_SOURCE, { type: 'vector', url: _tilesUrl });
      addTilePaintLayers();
      _tilesRegion = region;
    }
    syncOverlays();  // the region's map layers that are on (js/layers.js)
    scheduleUrgencyUpdate();
  });
}

// Throttled, not debounced: a stream of tile loads must not keep postponing the
// pass (applyUrgencyStates skips features it already colored this minute).
function scheduleUrgencyUpdate() {
  if (_urgencyTimer) return;
  _urgencyTimer = setTimeout(() => { _urgencyTimer = null; applyUrgencyStates(); }, 50);
}

// A tile feature's colors now ({urgency, urgency_even?, urgency_odd?}): one verdict per
// city, schedule and side split, reused within the region-local minute it is for.
function _verdictFor(props, now) {
  const split = 'even_left' in props, key = props.city + '|' + split + '|' + (props.sched || '');
  let u = _verdicts.get(key);
  if (u) return u;
  const entries = tileEntries(props);
  // Same test as the calendar's "No sweeping": nothing real to display.
  const none = !tileSchedLines(props).length;
  const skip = sweepSkip(_tilesRegion, props.city);
  // No line to show is no sweeping, unless the data can't tell (Chicago's dates ran out).
  const verdict = es => !es.length || (none && !es.some(e => BroomUrgency.unknownReason(e.code, now))) ? 'none'
    : BroomUrgency.checkDaySweeping(es, now, skip);
  u = { urgency: verdict(entries) };
  // A curb's color (CURB_ZOOM): its side's, or the line's when the sides share a schedule.
  if (split) {
    u.urgency_even = verdict(entries.filter(e => e.side === 'even'));
    u.urgency_odd = verdict(entries.filter(e => e.side === 'odd'));
  } else if (props.render_type === 'line') {
    u.urgency_even = u.urgency_odd = u.urgency;
  }
  _verdicts.set(key, u);
  return u;
}

// Push every loaded tile feature's colors to feature-state, which drives the paint
// expressions. A feature is checked again only when its verdict object changed (a new
// minute, a remount, a basemap switch: remarkTarget), and written only when it differs.
function applyUrgencyStates() {
  if (!map || !_tilesRegion || !map.getSource(TILES_SOURCE)) return;
  const now = regionNow(_tilesRegion);
  const stamp = now.y + '-' + now.m + '-' + now.d + '-' + now.min;
  if (stamp !== _verdictStamp) { _verdicts = new Map(); _verdictStamp = stamp; }
  let feats;
  try { feats = map.querySourceFeatures(TILES_SOURCE, { sourceLayer: TILES_SRC_LAYER }); }
  catch (_) { return; }
  const seen = new Map();  // only the loaded features' ids are kept
  for (const f of feats) {
    const id = f.id;
    if (id === undefined || id === null || seen.has(id)) continue;
    const u = _verdictFor(f.properties, now);
    seen.set(id, u);
    if (_featState.get(id) === u) continue;
    const target = { source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id };
    const cur = map.getFeatureState(target);
    if (Object.keys(u).some(k => cur[k] !== u[k])) map.setFeatureState(target, u);
  }
  _featState = seen;
  updateCurbLabels();
}

// ── Curb schedules (zoomed in to LABEL_ZOOM) ──────────────────────────────────
// Each curb's schedule ("Tu Th 6–8AM", as the card words it with days shortened) along a
// copy of its block moved beside the curb's line: a GeoJSON source rebuilt from the tiles
// in view once the map settles (the words come from urgency.js, as the card's).
const LABEL_ZOOM = 17.5;
const LABELS_SOURCE = 'zones-sched-src';
const _noFeatures = { type: 'FeatureCollection', features: [] };
let _labelsKey = '';  // view, minute and loaded features the labels are for
const _M_PER_DEG = 111320;
const _shortDays = s => s.replace(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b/g, d => d.slice(0, 2)).replace(/, /g, ' ');

// A [lon, lat] polyline moved `m` meters to its left (negative: right).
function _shiftLine(c, m) {
  return c.map((pt, i) => {
    const a = c[Math.max(0, i - 1)], b = c[Math.min(c.length - 1, i + 1)];
    const kx = _M_PER_DEG * Math.cos(pt[1] * Math.PI / 180);
    const dx = (b[0] - a[0]) * kx, dy = (b[1] - a[1]) * _M_PER_DEG, n = Math.hypot(dx, dy) || 1;
    return [pt[0] - dy / n * m / kx, pt[1] + dx / n * m / _M_PER_DEG];
  });
}

function updateCurbLabels() {
  const src = map?.getSource(LABELS_SOURCE);
  if (!src || !_tilesRegion || map.getZoom() < LABEL_ZOOM - 0.5) return;
  const now = regionNow(_tilesRegion), b = map.getBounds();
  const key = [b.toArray().flat().map(v => v.toFixed(4)), map.getZoom().toFixed(1), _verdictStamp, _featState.size].join('|');
  if (key === _labelsKey) return;
  _labelsKey = key;
  // Beside the curb's line, past its permit edge (5 px out): half the text and a gap further.
  const off = CURB_M + 14 * _mPerPx(map.getZoom(), map.getCenter().lat);
  const inView = c => {  // the line's box overlaps the view
    const xs = c.map(q => q[0]), ys = c.map(q => q[1]);
    return Math.max(...xs) >= b.getWest() && Math.min(...xs) <= b.getEast()
        && Math.max(...ys) >= b.getSouth() && Math.min(...ys) <= b.getNorth();
  };
  const feats = [], seen = new Set(), texts = new Map();
  let all;
  try { all = map.querySourceFeatures(TILES_SOURCE, { sourceLayer: TILES_SRC_LAYER, filter: ['==', ['get', 'render_type'], 'line'] }); }
  catch (_) { return; }
  for (const f of all) {
    const st = _featState.get(f.id);
    if (!st) continue;
    const parts = f.geometry.type === 'LineString' ? [f.geometry.coordinates] : f.geometry.coordinates;
    for (const c of parts) {
      if (c.length < 2 || !inView(c)) continue;
      for (const side of ['even', 'odd']) {
        const k = `${f.id}|${side}|${c[0][0].toFixed(5)},${c[0][1].toFixed(5)}`;
        if (seen.has(k)) continue;
        seen.add(k);
        const tk = f.properties.sched + '|' + side;
        if (!texts.has(tk)) {
          const es = tileEntries(f.properties).filter(e => e.side === side);
          texts.set(tk, sourceLines(es, now).map(l => `${_shortDays(l.days)} ${l.time}`.trim()).join(' / '));
        }
        const t = texts.get(tk);
        if (!t) continue;
        const left = (f.properties.even_left !== false) === (side === 'even');
        feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: _shiftLine(c, left ? off : -off) },
                     properties: { t, u: st[`urgency_${side}`] || st.urgency } });
      }
    }
  }
  src.setData({ type: 'FeatureCollection', features: feats });
}

// Permit areas' curb edges show while that layer is on (js/layers.js syncOverlays).
function permitEdgesOn() { return typeof _isOn === 'function' && _isOn('parking'); }

// A tile feature's raw schedule entries [{code, time, desc, side}] ([] if unreadable).
function tileEntries(props) {
  try { return JSON.parse(props.sched || '[]') || []; } catch (_) { return []; }
}

// Canonical schedule lines for a tile feature (days merged per time: "Tue, Thu · 3AM–5AM",
// Mon->Sun order, merged next-cluster dates), as on the car card. Used by the
// calendar and the map colors.
function tileSchedLines(props) {
  const sched = tileEntries(props);
  const now = regionNow(_tilesRegion);
  const labels = [props.side_even || 'Even', props.side_odd || 'Odd'];
  return BroomUrgency.formatBothSides(
    sched.filter(e => e.side === 'even'), sched.filter(e => e.side === 'odd'), now, null, labels);
}
