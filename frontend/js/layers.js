// ── Map layers: permit areas, meters, EV charging, … (the Layers button) ─────
// Each region lists its layers in /cities (id, name, a content-versioned GeoJSON url,
// scripts/build_layers.py). Turned on, a layer is drawn under the street colors (points
// above them, and an area's thin rim), so sweeping stays the first thing the map says.
// The parking rules start on, places (EV charging, markets) off; what is switched is
// remembered on this device. A tap on a layer's feature names it (and a market's days, a
// closure's dates); an area the target is in shows on the street card (overlayAreasAtTarget).
// color: the layer's color; a feature's own `color` (each permit area its own: the
// city's, else one its neighbors lack; scripts/build_layers.py) wins, edged in its
// `edge` when it is pale. key: the Layers
// menu swatch when features differ. icon: the glyph of a point layer's marker (a 24 px
// SVG path, white on the color); fill: the glyph is filled, else stroked; line: more of
// the glyph, stroked (a carrot's leaves).
// minzoom: hidden zoomed out (meters are many). An area is a thin line in its color (no
// fill: red, orange and green on the map mean sweeping); dash: the line dashed;
// focus: the area the target is in is outlined (permit areas).
// Red, orange and green are the street colors' (today, tomorrow, clear): no layer takes them.
const LAYER_STYLE = {
  parking: { color: '#7c3aed', focus: true, key: 'conic-gradient(#7c3aed 0 25%, #0d9488 0 50%, #c026d3 0 75%, #2563eb 0)' },
  meters:  { color: cssVar('--rule-meter'), minzoom: 16 },  // sign colors, as the card
  winter:  { color: cssVar('--rule-snow') },
  ev:      { color: '#0369a1', minzoom: 12, fill: true, icon: 'M13.5 3 6 13.5h5L10 21l8-11h-5.2l.7-7z' },
  fire:    { color: '#9d174d', dash: true },  // dark magenta, dashed: not a street swept today
  markets: { color: '#7e22ce', fill: true, icon: 'M3.5 20.5 12 9.6a3.3 3.3 0 0 1 4.9 4.1z', line: 'M15.4 8.6l.9-4.6M16.4 9.6l4.4-1.2M15.9 9.1l3.6-3.6' },
  closures: { color: '#334155' },  // slate, dashed (every line layer is)
};
const LAYER_PREFIX = 'lyr-';
const LAYER_GRAY = '#475569';  // a layer with no color of its own
const layersMenu = document.getElementById('layers-menu');
const btnLayers = document.getElementById('btn-layers');
const LAYERS_OFF_DEFAULT = ['ev', 'markets'];
let _layersOff = new Set(LAYERS_OFF_DEFAULT);
try { const s = _read('bb_layers_off'); if (s) _layersOff = new Set(JSON.parse(s)); } catch (_) {}
const _isOn = id => !_layersOff.has(id);
let _layersRegion = null;  // region whose overlays are on the map
let _layerPopup = null;

function _regionLayers() { return regions[_tilesRegion || _region]?.layers || []; }

function _saveLayersOff() {
  _store('bb_layers_off', JSON.stringify([..._layersOff]));  // "[]" too: all on is a choice
}

// A point layer's marker: a disc in its color with a white rim and glyph (2x pixels).
const _ICON_PX = 44;
const _iconCanvases = {};
function _iconCanvas(id) {
  if (_iconCanvases[id]) return _iconCanvases[id];
  const st = LAYER_STYLE[id], c = document.createElement('canvas');
  c.width = c.height = _ICON_PX;
  const g = c.getContext('2d'), r = _ICON_PX / 2;
  g.beginPath(); g.arc(r, r, r - 1, 0, 2 * Math.PI);
  g.fillStyle = '#fff'; g.fill();
  g.beginPath(); g.arc(r, r, r - 4, 0, 2 * Math.PI);
  g.fillStyle = st.color; g.fill();
  g.translate(r - 13, r - 13); g.scale(26 / 24, 26 / 24);
  const glyph = new Path2D(st.icon);
  g.fillStyle = g.strokeStyle = '#fff';
  g.lineWidth = 2.2; g.lineCap = g.lineJoin = 'round';
  if (st.fill) g.fill(glyph); else g.stroke(glyph);
  if (st.line) g.stroke(new Path2D(st.line));
  return (_iconCanvases[id] = c);
}
const _iconName = id => `${LAYER_PREFIX}ic-${id}`;
function _ensureIcon(id) {
  const name = _iconName(id);
  if (map.hasImage(name)) return;
  const c = _iconCanvas(id);
  map.addImage(name, c.getContext('2d').getImageData(0, 0, c.width, c.height), { pixelRatio: 2 });
}

// Style layer ids of an overlay: [area fill (clear: a tap or the target finds the area),
// line, point, area edge, the area at the target's outline, a curb's second permit].
const _ids = id => ['fill', 'line', 'pt', 'rim', 'here', 'curb2'].map(k => `${LAYER_PREFIX}${id}-${k}`);
// Permit areas zoomed in to CURB_ZOOM: the permit curbs in their area's color (a second
// permit striped over it) instead of the area's edge and outline.
const CURB_ZOOM = 15;
const _fadeIn = (a, b) => ['interpolate', ['linear'], ['zoom'], CURB_ZOOM - 0.5, a, CURB_ZOOM + 0.5, b];
// The layers a tap names a feature of.
function overlayLayerIds() {
  return _regionLayers().flatMap(l => { const [fill, line, pt] = _ids(l.id); return [fill, line, pt]; })
    .filter(id => map?.getLayer(id));
}

function _removeOverlay(id) {
  for (const lid of _ids(id)) if (map.getLayer(lid)) map.removeLayer(lid);
  if (map.getSource(LAYER_PREFIX + id)) map.removeSource(LAYER_PREFIX + id);
  delete _hereId[id];
}

// A layer's feature (id) for feature-state: a tiled layer's (PMTiles) is in its source layer.
function _overlayAt(id) {
  const source = LAYER_PREFIX + id, sourceLayer = map.getSource(source)?.type === 'vector' ? id : undefined;
  return fid => ({ source, sourceLayer, id: fid });
}

// A turned-on GeoJSON layer not loaded yet because the zoom hides it (meters: one file for
// the whole region); a tiled layer (PMTiles) loads only the tiles in view by itself.
function _waitsForZoom(layer) {
  const min = LAYER_STYLE[layer.id]?.minzoom || 0;
  return !layer.tiles && !map.getSource(LAYER_PREFIX + layer.id) && map.getZoom() < min;
}
// Zoomed in: the turned-on layers that waited for it (map.js, zoomend).
function syncOverlaysForZoom() {
  if (_regionLayers().some(l => _isOn(l.id) && !map.getSource(LAYER_PREFIX + l.id))) whenStyleReady(syncOverlays);
}

let _iconListener = false;
function _addOverlay(layer) {
  if (_waitsForZoom(layer)) return;
  const src = LAYER_PREFIX + layer.id, st = LAYER_STYLE[layer.id] || {};
  const color = ['coalesce', ['get', 'color'], st.color || LAYER_GRAY];
  if (!map.getSource(src)) {
    map.addSource(src, layer.tiles
      ? { type: 'vector', url: 'pmtiles://' + window.location.origin + layer.tiles }
      : { type: 'geojson', data: layer.url, generateId: true });
  }
  const [fill, line, pt, rim, here, curb2] = _ids(layer.id);
  const below = map.getLayer('zones-fill') ? 'zones-fill' : undefined;  // under the street colors
  const over = firstLabelLayer();  // over them, under the basemap's names
  // The source (a tiled one: its layer, named as the theme) and the zoom it shows from.
  const base = { source: src, ...(layer.tiles ? { 'source-layer': layer.id } : {}), ...(st.minzoom ? { minzoom: st.minzoom } : {}) };
  const is = (...types) => ['in', ['geometry-type'], ['literal', types]];
  const lines = is('LineString', 'MultiLineString');
  const curb = ['==', ['get', 'kind'], 'curb'];
  const poly = ['all', is('Polygon', 'MultiPolygon'), ['!', curb]];
  const meters = layer.id === 'meters', curbs = layer.id === 'parking';
  const edgeOpacity = a => curbs ? _fadeIn(a, 0) : a;
  const add = (def, before) => { if (!map.getLayer(def.id)) map.addLayer({ ...base, ...def }, before); };
  // Areas: a fill (clear, or the tint) under the streets, which a tap or the target finds,
  // and without a tint a thin edge in the area's color (darker when pale) over them; the
  // area at the target outlined.
  const edge = ['coalesce', ['get', 'edge'], color];
  add({ id: fill, type: 'fill', filter: poly, paint: { 'fill-color': color, 'fill-opacity': 0 } }, below);
  if (!meters) {
    add({ id: rim, type: 'line', filter: poly, layout: { 'line-join': 'round' },
          paint: { 'line-color': edge, 'line-width': EDGE_W, 'line-opacity': edgeOpacity(0.9),
                   ...(st.dash ? { 'line-dasharray': [3, 2] } : {}) } }, over);
  }
  if (st.focus) {
    add({ id: here, type: 'line', filter: poly, layout: { 'line-join': 'round' },
          paint: { 'line-color': edge, 'line-width': HERE_W, 'line-opacity': edgeOpacity(focusOn('here')) } }, over);
  }
  if (curbs) {  // thin and light: the street colors read first
    const width = ['interpolate', ['linear'], ['zoom'], CURB_ZOOM, 1.2, 18, 3];
    add({ id: line, type: 'line', filter: ['all', lines, curb], minzoom: CURB_ZOOM - 0.5,
          paint: { 'line-color': color, 'line-width': width, 'line-opacity': _fadeIn(0, 0.45) } }, below);
    add({ id: curb2, type: 'line', filter: ['all', lines, curb, ['has', 'color2']], minzoom: CURB_ZOOM - 0.5,
          paint: { 'line-color': ['get', 'color2'], 'line-width': width, 'line-opacity': _fadeIn(0, 0.45),
                   'line-dasharray': [1.5, 1.5] } }, below);
  }
  // Lines: dashed (a closure); meters' blocks solid, thin and gray, along the street.
  if (!map.getLayer(line)) map.addLayer({ id: line, type: 'line', ...base,
    filter: lines, layout: meters ? { 'line-cap': 'round' } : {},
    paint: meters
      ? { 'line-color': color, 'line-opacity': 0.6, 'line-width': ['interpolate', ['linear'], ['zoom'], 16, 1.5, 18, 3] }
      : { 'line-color': color, 'line-opacity': 0.8, 'line-dasharray': [2, 1.5],
          'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 16, 2.5] } }, below);
  if (map.getLayer(pt)) return;
  const points = is('Point', 'MultiPoint');
  if (st.icon) {
    // The style can drop its images (a basemap switch): put a marker back when asked for.
    if (!_iconListener) {
      _iconListener = true;
      map.on('styleimagemissing', e => {
        const id = e.id.startsWith(LAYER_PREFIX + 'ic-') && e.id.slice(LAYER_PREFIX.length + 3);
        if (LAYER_STYLE[id]?.icon) _ensureIcon(id);
      });
    }
    _ensureIcon(layer.id);
    map.addLayer({ id: pt, type: 'symbol', filter: points, ...base,
      layout: { 'icon-image': _iconName(layer.id), 'icon-allow-overlap': layer.id === 'markets',
                'icon-size': ['interpolate', ['linear'], ['zoom'], 11, 0.6, 15, 1] } });
  } else {
    // Many small points (meters): a dot that stays out of the way.
    map.addLayer({ id: pt, type: 'circle', filter: points, ...base,
      paint: { 'circle-color': color, 'circle-opacity': meters ? 0.85 : 1,
               'circle-stroke-color': '#fff', 'circle-stroke-width': meters ? 0.6 : 1.5,
               'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 3, 15, meters ? 1.6 : 5, 18, meters ? 3.5 : 6] } });
  }
}

// Put the region's turned-on layers on the map (after a region or basemap change).
// Called once the style is ready (whenStyleReady): right after the tiles' source is
// added isStyleLoaded() is false again, which must not keep the layers off.
function syncOverlays() {
  if (!map) return;
  const region = _tilesRegion || _region;
  if (_layersRegion && _layersRegion !== region) {
    for (const l of regions[_layersRegion]?.layers || []) _removeOverlay(l.id);
  }
  _layersRegion = region;
  for (const l of _regionLayers()) {
    if (_isOn(l.id)) _addOverlay(l); else _removeOverlay(l.id);
  }
}

// Under the layers, the legend: the streets' colors (the card's rows name their marks).
function _legendHTML() {
  return `<button class="ctx-item lyr-legend-btn" aria-expanded="${_legendOpen}" aria-controls="lyr-legend">`
       + `Legend<svg class="ic lyr-chev" aria-hidden="true"><use href="#i-chevron-right"/></svg></button>`
       + `<div class="lyr-legend" id="lyr-legend"${_legendOpen ? '' : ' hidden'}><b>Street colors</b>${legendHTML()}</div>`;
}
// The street colors as the map draws them (styles.css --line-*, .lg): a swatch each, its
// thickness as the map's (today thickest), Check sign dashed.
function legendHTML() {
  return '<ul class="legend">' + [['today', 'Today', 3], ['tomorrow', 'Tomorrow', 2], ['safe', 'Clear', 1],
                                  ['check', 'Check sign', 1], ['none', 'No sweeping', 0]]
    .map(([u, name, w]) => `<li><i class="lg lg-${w} lg-${u}"></i>${name}</li>`).join('') + '</ul>';
}
// The legend folds under its own row: open until folded (remembered on this device).
let _legendOpen = _read('bb_legend') !== '0';

// A turned-on layer the map's zoom hides (minzoom).
const _hiddenHere = id => _isOn(id) && map && map.getZoom() < (LAYER_STYLE[id]?.minzoom || 0);

// A layer's swatch: its colors (permit areas: several), else its color.
function _layerKey(id) { return LAYER_STYLE[id]?.key || LAYER_STYLE[id]?.color || LAYER_GRAY; }

function _renderLayersMenu() {
  const list = _regionLayers();
  layersMenu.innerHTML = (list.length
    ? list.map(l => `<button class="ctx-item lyr-item" role="switch" data-layer="${esc(l.id)}"`
        + ` aria-checked="${_isOn(l.id)}">` + (LAYER_STYLE[l.id]?.icon
          ? `<img class="lyr-key" alt="" src="${esc(_iconCanvas(l.id).toDataURL())}">`
          : `<i class="lyr-key" style="--k:${esc(_layerKey(l.id))}"></i>`)
        + `<span class="lyr-label">${esc(l.name)}${_hiddenHere(l.id) ? '<small>Zoom in to see</small>' : ''}</span>`
        + `<svg class="ic lyr-check" aria-hidden="true"><use href="#i-check"/></svg></button>`).join('')
    : '<p class="lyr-note">No extra layers here.</p><button class="ctx-item" data-layer="">Close</button>')
    + _legendHTML();
}

menuButton(btnLayers, layersMenu, _renderLayersMenu);
layersMenu.addEventListener('click', e => {
  const lg = e.target.closest('.lyr-legend-btn');
  if (lg) {
    e.stopPropagation();
    _legendOpen = !_legendOpen;
    _store('bb_legend', _legendOpen ? null : '0');
    lg.setAttribute('aria-expanded', String(_legendOpen));
    document.getElementById('lyr-legend').hidden = !_legendOpen;
    return;
  }
  const b = e.target.closest('[data-layer]');
  if (!b) return;
  e.stopPropagation();  // the menu stays open: several layers can be switched in a row
  const id = b.dataset.layer;
  if (!id) { closeMenu(true); return; }
  if (_layersOff.has(id)) _layersOff.delete(id); else _layersOff.add(id);
  b.setAttribute('aria-checked', String(_isOn(id)));
  _saveLayersOff();
  _renderLayersMenu();  // the "Zoom in to see" note follows the switch
  layersMenu.querySelector(`[data-layer="${CSS.escape(id)}"]`)?.focus();
  whenStyleReady(syncOverlays);
});

// A closure's dates in the region's time: "Mon Oct 20, 9AM–5PM", or "Mon Oct 20 – Wed Oct 22".
// start/end are ISO date-times, UTC when they end in Z or an offset (feeds.py).
function _closureWhen(p) {
  const at = iso => {
    if (/(Z|[+-]\d\d:?\d\d)$/.test(iso)) return regionNow(_tilesRegion || _region, new Date(iso));
    const p = BroomUrgency.parseIso(iso);
    return p && { ...p, min: p.min ?? 0 };
  };
  const a = p.start && at(p.start), b = p.end && at(p.end);
  if (!a) return '';
  const day = BroomUrgency.dayLabel;
  if (!b) return day(a);
  if (day(a) === day(b)) return `${day(a)}, ${BroomUrgency.clockText(a.min)}–${BroomUrgency.clockText(b.min)}`;
  return `${day(a)} – ${day(b)}`;
}

// A tap on an overlay's feature: its name, when it is on (a market's days and hours, a
// closure's dates) or what it has (a charger's ports), and its city when the region has
// several, in a small popup (the street tap still clears the selection, map.js). On a
// street (pointsOnly) the street card says the rest.
function overlayClick(e, pointsOnly = false) {
  const ids = overlayLayerIds();
  if (!ids.length) return;
  const f = map.queryRenderedFeatures(e.point, { layers: ids }).find(x => !pointsOnly || x.geometry.type === 'Point');
  _layerPopup?.remove();
  _layerPopup = null;
  if (!f) return;
  const p = f.properties, el = document.createElement('div');
  const line = (cls, text) => {
    if (!text) return;
    const s = el.appendChild(document.createElement('span'));
    s.className = cls;
    s.textContent = text;
  };
  line('lyr-name', p.label || p.title || '');  // closures: a title
  line('lyr-when', p.start ? _closureWhen(p) : p.rule);
  line('lyr-city', _regionCities() > 1 ? cityName(p.city) : '');  // "Permit area A" is in Berkeley and in Oakland
  _layerPopup = new maplibregl.Popup({ closeButton: false, className: 'lyr-popup', offset: 12, maxWidth: '260px' })
    .setLngLat(f.geometry.type === 'Point' ? f.geometry.coordinates : e.lngLat).setDOMContent(el).addTo(map);
}

function _regionCities() { return Object.keys(regions[_tilesRegion || _region]?.cities || {}).length; }

// The shaded areas the target is in: outlined (feature-state `here`, no filter: see
// setActiveFeature, js/map.js).
const _hereId = {};  // layer id -> feature id of the area at the target (null: none)
function markOverlayAtTarget() {
  if (!map) return;
  const c = map.project(map.getCenter());
  for (const l of _regionLayers()) {
    const [fill, , , , here] = _ids(l.id);
    if (!map.getLayer(here)) continue;
    let f;
    try { [f] = map.queryRenderedFeatures(c, { layers: [fill] }); } catch (_) {}
    const id = f?.id ?? null, at = _overlayAt(l.id);
    if (id === (_hereId[l.id] ?? null)) continue;
    swapState(at, 'here', _hereId[l.id], id);
    _hereId[l.id] = id;
  }
}
// The basemap switched: clear the marks so markOverlayAtTarget sets them afresh.
function resetOverlayMarks() {
  for (const [lid, id] of Object.entries(_hereId)) {
    if (map.getSource(LAYER_PREFIX + lid)) swapState(_overlayAt(lid), 'here', id, null);
    delete _hereId[lid];
  }
}

// The turned-on layers' areas the target is in, as /api/rules gives rules ({theme, label,
// city, color}; no rule text): the street card shows them at once, and those the server
// does not know (fire zones are a home's, not a car's).
function overlayAreasAtTarget() {
  if (!map) return [];
  const ids = _regionLayers().map(l => _ids(l.id)[0]).filter(id => map.getLayer(id));
  if (!ids.length) return [];
  let feats;
  try { feats = map.queryRenderedFeatures(map.project(map.getCenter()), { layers: ids }); } catch (_) { return []; }
  return feats.map(f => ({ theme: f.layer.id.slice(LAYER_PREFIX.length, -'-fill'.length), label: f.properties.label,
                           city: f.properties.city, color: f.properties.edge || f.properties.color, rule: '' }));  // pale: its edge
}
