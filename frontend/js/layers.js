// ── Map layers: permit areas, meters, EV charging, … (the Layers button) ─────
// Each region lists its layers in /cities (id, name, a content-versioned GeoJSON url,
// scripts/build_layers.py). Turned on, a layer is drawn under the street colors (points
// above them, and an area's thin rim), so sweeping stays the first thing the map says.
// The parking rules start on, places (EV charging, markets) off; what is switched is
// remembered on this device. A tapped marker comes under the target; the street card names
// the areas and markers there (overlayAreasAtTarget, overlayMarkersAtTarget).
// color: the layer's color; a feature's own `color` (each permit area its own: the
// city's, else one its neighbors lack; scripts/build_layers.py) wins, edged in its
// `edge` when it is pale. key: the Layers
// menu swatch when features differ. icon: the glyph of a point layer's marker (a 24 px
// SVG path, white on the color); fill: the glyph is filled, else stroked; line: more of
// the glyph, stroked (a carrot's leaves).
// minzoom: hidden zoomed out (meters are many). An area is a thin line in its color;
// tint: instead a light fill of that opacity and a soft band inside the edge, both under
// the street colors (permit areas); dash: the line dashed;
// focus: the area the target is in is outlined (permit areas).
// Red, amber and green are the street colors' (today, tomorrow, clear): no layer takes them.
const LAYER_STYLE = {
  parking: { color: '#7c3aed', focus: true, tint: ['interpolate', ['linear'], ['zoom'], 12, 0.12, 17, 0.08], key: 'conic-gradient(#7c3aed 0 25%, #0d9488 0 50%, #c026d3 0 75%, #2563eb 0)' },
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
// line, point, area edge, the area at the target's outline].
const _ids = id => ['fill', 'line', 'pt', 'rim', 'here'].map(k => `${LAYER_PREFIX}${id}-${k}`);
// Permit areas zoomed in to CURB_ZOOM (js/map.js): each curb's permit as a dotted edge beside
// its street color (zones-permit-*) instead of the area's band and outline.
// A tinted area's band (px) inside its edge (a positive offset is inside, as map.js BAND_W).
const TINT_BAND_W = ['interpolate', ['linear'], ['zoom'], 11, 2, 14, 5, 16, 7];
const TINT_BAND_OFFSET = ['interpolate', ['linear'], ['zoom'], 11, 1, 14, 2.5, 16, 3.5];
const _fadeIn = (a, b) => ['interpolate', ['linear'], ['zoom'], CURB_ZOOM - 0.5, a, CURB_ZOOM + 0.5, b];
// The marker layers (EV charging, markets) turned on.
function _markerLayerIds() {
  return _regionLayers().filter(l => LAYER_STYLE[l.id]?.icon).map(l => _ids(l.id)[2]).filter(id => map?.getLayer(id));
}
// The marker drawn within `pad` px of screen point p (the nearest), else null.
function _markerAt(p, pad) {
  const ids = _markerLayerIds();
  if (!ids.length) return null;
  let fs;
  try { fs = map.queryRenderedFeatures([[p.x - pad, p.y - pad], [p.x + pad, p.y + pad]], { layers: ids }); } catch (_) { return null; }
  const d = f => { const q = map.project(f.geometry.coordinates); return Math.hypot(q.x - p.x, q.y - p.y); };
  return fs.filter(f => f.geometry.type === 'Point').sort((a, b) => d(a) - d(b))[0] || null;
}
// A tap: the marker under it, as [lon, lat] (the map moves it under the target), else null.
function markerTapped(p) { return _markerAt(p, 14)?.geometry.coordinates || null; }
// The marker at the target as a card rule ({theme, label, rule, color}; rule: a market's
// days and hours, a charger's ports), or none.
function overlayMarkersAtTarget() {
  if (!map) return [];
  const f = _markerAt(map.project(map.getCenter()), 10);
  if (!f) return [];
  const theme = f.layer.id.slice(LAYER_PREFIX.length, -'-pt'.length), p = f.properties;
  return [{ theme, label: p.label || p.title || '', rule: p.rule || '', city: p.city,
            color: p.color || LAYER_STYLE[theme]?.color || LAYER_GRAY }];
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
  const [fill, line, pt, rim, here] = _ids(layer.id);
  const below = map.getLayer('zones-fill') ? 'zones-fill' : undefined;  // under the street colors
  const over = firstLabelLayer();  // over them, under the basemap's names
  // The source (a tiled one: its layer, named as the theme) and the zoom it shows from.
  const base = { source: src, ...(layer.tiles ? { 'source-layer': layer.id } : {}), ...(st.minzoom ? { minzoom: st.minzoom } : {}) };
  const is = (...types) => ['in', ['geometry-type'], ['literal', types]];
  const lines = is('LineString', 'MultiLineString');
  const curb = ['==', ['get', 'kind'], 'curb'];
  const poly = ['all', is('Polygon', 'MultiPolygon'), ['!', curb]];
  const meters = layer.id === 'meters', permits = layer.id === 'parking';
  const edgeOpacity = a => permits ? _fadeIn(a, 0) : a;
  const add = (def, before) => { if (!map.getLayer(def.id)) map.addLayer({ ...base, ...def }, before); };
  // Areas: a fill (clear, or the tint) under the streets, which a tap or the target finds;
  // with a tint a soft band inside the edge under them, else a thin edge in the area's
  // color (darker when pale) over them; the area at the target outlined.
  const edge = ['coalesce', ['get', 'edge'], color];
  add({ id: fill, type: 'fill', filter: poly, paint: { 'fill-color': color, 'fill-opacity': st.tint || 0 } }, below);
  if (st.tint) {  // a soft band inside the edge, under the streets: never read as a street line
    add({ id: rim, type: 'line', filter: poly, layout: { 'line-join': 'round' },
          paint: { 'line-color': color, 'line-width': TINT_BAND_W, 'line-offset': TINT_BAND_OFFSET,
                   'line-opacity': edgeOpacity(0.35) } }, below);
  } else if (!meters) {
    add({ id: rim, type: 'line', filter: poly, layout: { 'line-join': 'round' },
          paint: { 'line-color': edge, 'line-width': EDGE_W, 'line-opacity': edgeOpacity(0.9),
                   ...(st.dash ? { 'line-dasharray': [3, 2] } : {}) } }, over);
  }
  if (st.focus) {
    add({ id: here, type: 'line', filter: poly, layout: { 'line-join': 'round' },
          paint: { 'line-color': edge, 'line-width': HERE_W, 'line-opacity': edgeOpacity(focusOn('here')) } }, over);
  }
  // Lines: dashed (a closure); meters' blocks solid, thin and gray, along the street.
  if (!map.getLayer(line)) map.addLayer({ id: line, type: 'line', ...base,
    filter: ['all', lines, ['!', curb]], layout: meters ? { 'line-cap': 'round' } : {},
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
  for (const id of ['zones-permit-l', 'zones-permit-r']) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', permitEdgesOn() ? 'visible' : 'none');
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
