// ── Map layers: permit areas, meters, EV charging, … (the Layers button) ─────
// Each region lists its layers in /cities (id, name, a content-versioned GeoJSON url,
// scripts/build_layers.py). Turned on, a layer is drawn under the street colours (points
// above them), so sweeping stays the first thing the map says. Which layers are on is
// remembered on this device. A tap on a layer's feature names it; an area the target is
// in shows on the street card (overlayAreasAtTarget).
// color: the layer's colour; a feature's own `color` (each permit area its own: the
// city's, else one its neighbours lack; scripts/build_layers.py) wins, edged in its
// `edge` when it is pale. key: the Layers
// menu swatch when features differ. icon: the glyph of a point layer's marker (a 24 px
// SVG path, white on the colour); fill: the glyph is filled, else stroked.
// minzoom: hidden zoomed out (meters are many).
const LAYER_STYLE = {
  parking: { color: '#7c3aed', key: 'conic-gradient(#7c3aed 0 25%, #0d9488 0 50%, #db2777 0 75%, #a16207 0)' },
  meters:  { color: '#0f766e', minzoom: 15 },
  winter:  { color: '#0369a1' },
  ev:      { color: '#16a34a', minzoom: 12, fill: true, icon: 'M13.5 3 6 13.5h5L10 21l8-11h-5.2l.7-7z' },
  fire:    { color: '#dc2626' },
  markets: { color: '#c2410c', icon: 'M4 9.5h16M5 9.5 6.5 5h11L19 9.5M6 9.5V19h12V9.5M10 19v-5h4v5' },
  closures: { color: '#b91c1c' },
};
const LAYER_PREFIX = 'lyr-';
const layersMenu = document.getElementById('layers-menu');
const btnLayers = document.getElementById('btn-layers');
let _layersOn = new Set();
try { _layersOn = new Set(JSON.parse(_read('bb_layers') || '[]')); } catch (_) {}
let _layersRegion = null;  // region whose overlays are on the map
let _layerPopup = null;

function _regionLayers() { return regions[_tilesRegion || _region]?.layers || []; }

function _saveLayersOn() {
  _store('bb_layers', JSON.stringify([..._layersOn]));
}

// A point layer's marker: a disc in its colour with a white rim and glyph (2x pixels).
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
  return (_iconCanvases[id] = c);
}
const _iconName = id => `${LAYER_PREFIX}ic-${id}`;
function _ensureIcon(id) {
  const name = _iconName(id);
  if (map.hasImage(name)) return;
  const c = _iconCanvas(id);
  map.addImage(name, c.getContext('2d').getImageData(0, 0, c.width, c.height), { pixelRatio: 2 });
}

// Style layer ids of an overlay: [area fill, area edge, line, point].
const _ids = id => ['fill', 'edge', 'line', 'pt'].map(k => `${LAYER_PREFIX}${id}-${k}`);
function overlayLayerIds() {
  return _regionLayers().flatMap(l => _ids(l.id)).filter(id => map?.getLayer(id));
}

function _removeOverlay(id) {
  for (const lid of _ids(id)) if (map.getLayer(lid)) map.removeLayer(lid);
  if (map.getSource(LAYER_PREFIX + id)) map.removeSource(LAYER_PREFIX + id);
}

let _iconListener = false;
function _addOverlay(layer) {
  const src = LAYER_PREFIX + layer.id, st = LAYER_STYLE[layer.id] || {};
  const color = ['coalesce', ['get', 'color'], st.color || '#475569'];
  if (!map.getSource(src)) map.addSource(src, { type: 'geojson', data: layer.url });
  const [fill, edge, line, pt] = _ids(layer.id);
  const below = map.getLayer('zones-fill') ? 'zones-fill' : undefined;  // under the street colours
  const zoom = st.minzoom ? { minzoom: st.minzoom } : {};
  const is = (...types) => ['in', ['geometry-type'], ['literal', types]];
  const poly = is('Polygon', 'MultiPolygon');
  const meters = layer.id === 'meters';
  // Areas: a tint and an edge in their colour, like Chicago's sweeping zones (the edge
  // tells neighbouring areas apart).
  if (!map.getLayer(fill)) map.addLayer({ id: fill, type: 'fill', source: src, filter: poly, ...zoom,
    paint: { 'fill-color': color, 'fill-opacity': meters ? 0.1 : 0.2 } }, below);
  if (!meters && !map.getLayer(edge)) map.addLayer({ id: edge, type: 'line', source: src, filter: poly, ...zoom,
    paint: { 'line-color': ['coalesce', ['get', 'edge'], color], 'line-opacity': 0.85, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.8, 16, 2] } }, below);
  if (!map.getLayer(line)) map.addLayer({ id: line, type: 'line', source: src, ...zoom,
    filter: is('LineString', 'MultiLineString'),
    paint: { 'line-color': color, 'line-opacity': 0.8, 'line-dasharray': [2, 1.5],
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
    map.addLayer({ id: pt, type: 'symbol', source: src, filter: points, ...zoom,
      layout: { 'icon-image': _iconName(layer.id), 'icon-allow-overlap': layer.id === 'markets',
                'icon-size': ['interpolate', ['linear'], ['zoom'], 11, 0.6, 15, 1] } });
  } else {
    // Many small points (meters): a dot that stays out of the way.
    map.addLayer({ id: pt, type: 'circle', source: src, filter: points, ...zoom,
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
    if (_layersOn.has(l.id)) _addOverlay(l); else _removeOverlay(l.id);
  }
}

// Under the layers, the legend: the streets' colours, then the marks the street card and
// the calendar use (CARD_MARKS, js/signs.js; a car's dot, a home's square).
function _legendHTML() {
  const colors = [['today', 'Today', 3], ['tomorrow', 'Tomorrow', 2], ['safe', 'Clear', 1], ['none', 'No sweeping', 0]]
    .map(([u, name, w]) => `<span class="urg-${u}"><i class="lg lg-${w}"></i>${name}</span>`).join('');
  const have = new Set(_regionLayers().map(l => l.id));
  const marks = CARD_MARKS.filter(m => !m.layer || have.has(m.layer)).map(m => `<span>${markHTML({ ...m, sweep: m.key === 'sweep' })}${esc(m.name)}</span>`).join('');
  const cal = '<span><i class="cal-key cal-round" style="background:var(--muted)"></i>Your car</span>'
            + '<span><i class="cal-key cal-square" style="background:var(--home)"></i>Home pickup</span>';
  return `<button class="ctx-item lyr-legend-btn" role="menuitem" aria-expanded="${_legendOpen}" aria-controls="lyr-legend">`
       + `Legend<svg class="ic lyr-chev" aria-hidden="true"><use href="#i-chevron-right"/></svg></button>`
       + `<div class="lyr-legend" id="lyr-legend" role="none"${_legendOpen ? '' : ' hidden'}><b>Sweeping</b><div class="legend">${colors}</div>`
       + `<b>Marks</b><div class="lyr-marks">${marks}${cal}</div></div>`;
}
// The legend folds under its own row (open or not is remembered on this device).
let _legendOpen = _read('bb_legend') === '1';

// A layer's swatch: its colours (permit areas: several), else its colour.
function _layerKey(id) { return LAYER_STYLE[id]?.key || LAYER_STYLE[id]?.color || '#475569'; }

function _renderLayersMenu() {
  const list = _regionLayers();
  layersMenu.innerHTML = (list.length
    ? list.map(l => `<button class="ctx-item lyr-item" role="menuitemcheckbox" data-layer="${esc(l.id)}"`
        + ` aria-checked="${_layersOn.has(l.id)}">` + (LAYER_STYLE[l.id]?.icon
          ? `<img class="lyr-key" alt="" src="${esc(_iconCanvas(l.id).toDataURL())}">`
          : `<i class="lyr-key" style="--k:${esc(_layerKey(l.id))}"></i>`)
        + `${esc(l.name)}<svg class="ic lyr-check" aria-hidden="true"><use href="#i-check"/></svg></button>`).join('')
    : '<p class="lyr-note">No extra layers here.</p><button class="ctx-item" role="menuitem" data-layer="">Close</button>')
    + _legendHTML();
}

menuButton(btnLayers, layersMenu, _renderLayersMenu);
layersMenu.addEventListener('click', e => {
  const lg = e.target.closest('.lyr-legend-btn');
  if (lg) {
    e.stopPropagation();
    _legendOpen = !_legendOpen;
    _store('bb_legend', _legendOpen ? '1' : null);
    lg.setAttribute('aria-expanded', String(_legendOpen));
    document.getElementById('lyr-legend').hidden = !_legendOpen;
    return;
  }
  const b = e.target.closest('[data-layer]');
  if (!b) return;
  e.stopPropagation();  // the menu stays open: several layers can be switched in a row
  const id = b.dataset.layer;
  if (!id) { closeMenu(true); return; }
  if (_layersOn.has(id)) _layersOn.delete(id); else _layersOn.add(id);
  b.setAttribute('aria-checked', String(_layersOn.has(id)));
  _saveLayersOn();
  whenStyleReady(syncOverlays);
});

// A tap on an overlay's feature: its name in a small popup (the street tap still clears
// the selection, map.js).
function overlayClick(e) {
  const ids = overlayLayerIds();
  if (!ids.length) return;
  const f = map.queryRenderedFeatures(e.point, { layers: ids })[0];
  _layerPopup?.remove();
  _layerPopup = null;
  if (!f) return;
  const el = document.createElement('div'), city = _regionCities() > 1 ? cityName(f.properties.city) : '';
  el.textContent = f.properties.label || f.properties.title || '';  // closures: a title
  if (city) {  // "Permit area A" is in Berkeley and in Oakland
    const c = el.appendChild(document.createElement('span'));
    c.className = 'lyr-city';
    c.textContent = city;
  }
  _layerPopup = new maplibregl.Popup({ closeButton: false, className: 'lyr-popup', offset: 8 })
    .setLngLat(e.lngLat).setDOMContent(el).addTo(map);
}

function _regionCities() { return Object.keys(regions[_tilesRegion || _region]?.cities || {}).length; }

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
                           city: f.properties.city, color: f.properties.color, rule: '' }));
}
