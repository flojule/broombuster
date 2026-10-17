// ── Map layers: permit areas, meters, EV charging, … (the Layers button) ─────
// Each region lists its layers in /cities (id, name, a content-versioned GeoJSON url,
// scripts/build_layers.py). Turned on, a layer is drawn under the street colours (points
// above them), so sweeping stays the first thing the map says. Which layers are on is
// remembered on this device. A tap on a layer's feature names it.
const LAYER_STYLE = {
  parking: { color: '#7c3aed' },
  meters:  { color: '#0f766e' },
  winter:  { color: '#0369a1' },
  ev:      { color: '#16a34a' },
  fire:    { color: '#dc2626' },
  markets: { color: '#c2410c' },
};
const LAYER_PREFIX = 'lyr-';
const layersMenu = document.getElementById('layers-menu');
const btnLayers = document.getElementById('btn-layers');
let _layersOn = new Set();
try { _layersOn = new Set(JSON.parse(localStorage.getItem('bb_layers') || '[]')); } catch (_) {}
let _layersRegion = null;  // region whose overlays are on the map
let _layerPopup = null;

function _regionLayers() { return regions[_tilesRegion || _region]?.layers || []; }

function _saveLayersOn() {
  try { localStorage.setItem('bb_layers', JSON.stringify([..._layersOn])); } catch (_) {}
}

// Style layer ids of an overlay: [fill, line, circle].
const _ids = id => [`${LAYER_PREFIX}${id}-fill`, `${LAYER_PREFIX}${id}-line`, `${LAYER_PREFIX}${id}-pt`];
function overlayLayerIds() {
  return _regionLayers().flatMap(l => _ids(l.id)).filter(id => map?.getLayer(id));
}

function _removeOverlay(id) {
  for (const lid of _ids(id)) if (map.getLayer(lid)) map.removeLayer(lid);
  if (map.getSource(LAYER_PREFIX + id)) map.removeSource(LAYER_PREFIX + id);
}

function _addOverlay(layer) {
  const src = LAYER_PREFIX + layer.id, color = LAYER_STYLE[layer.id]?.color || '#475569';
  if (!map.getSource(src)) map.addSource(src, { type: 'geojson', data: layer.url });
  const [fill, line, pt] = _ids(layer.id);
  const below = map.getLayer('zones-fill') ? 'zones-fill' : undefined;  // under the street colours
  const poly = ['==', ['geometry-type'], 'Polygon'];
  if (!map.getLayer(fill)) map.addLayer({ id: fill, type: 'fill', source: src, filter: poly,
    paint: { 'fill-color': color, 'fill-opacity': 0.12 } }, below);
  if (!map.getLayer(line)) map.addLayer({ id: line, type: 'line', source: src,
    filter: ['!=', ['geometry-type'], 'Point'],
    paint: { 'line-color': color, 'line-opacity': 0.8, 'line-dasharray': [2, 1.5],
             'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 16, 2.5] } }, below);
  if (!map.getLayer(pt)) map.addLayer({ id: pt, type: 'circle', source: src,
    filter: ['==', ['geometry-type'], 'Point'], minzoom: layer.id === 'meters' ? 14 : 0,
    paint: { 'circle-color': color, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5,
             'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 3, 16, layer.id === 'meters' ? 4 : 6] } });
}

// Put the region's turned-on layers on the map (after a region or basemap change).
function syncOverlays() {
  if (!map || !map.isStyleLoaded()) return;
  const region = _tilesRegion || _region;
  if (_layersRegion && _layersRegion !== region) {
    for (const l of regions[_layersRegion]?.layers || []) _removeOverlay(l.id);
  }
  _layersRegion = region;
  for (const l of _regionLayers()) {
    if (_layersOn.has(l.id)) _addOverlay(l); else _removeOverlay(l.id);
  }
}

function _renderLayersMenu() {
  const list = _regionLayers();
  layersMenu.innerHTML = list.length
    ? list.map(l => `<button class="ctx-item lyr-item" role="menuitemcheckbox" data-layer="${esc(l.id)}"`
        + ` aria-checked="${_layersOn.has(l.id)}"><i class="lyr-key" style="--k:${esc(LAYER_STYLE[l.id]?.color || '#475569')}"></i>`
        + `${esc(l.name)}<svg class="ic lyr-check" aria-hidden="true"><use href="#i-check"/></svg></button>`).join('')
      + '<p class="lyr-note">From the cities’ open data; the signs on the street win.</p>'
    : '<p class="lyr-note">No extra layers here.</p><button class="ctx-item" data-layer="">Close</button>';
}

menuButton(btnLayers, layersMenu, _renderLayersMenu);
layersMenu.addEventListener('click', e => {
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
  const el = document.createElement('div');
  el.textContent = f.properties.label || '';
  _layerPopup = new maplibregl.Popup({ closeButton: false, className: 'lyr-popup', offset: 8 })
    .setLngLat(e.lngLat).setDOMContent(el).addTo(map);
}
