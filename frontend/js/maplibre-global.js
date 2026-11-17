import * as maplibregl from '/vendor/maplibre-gl.mjs';
// The worker by its versioned URL too (the server versions both, api/web.py module_response).
maplibregl.setWorkerUrl('/vendor/maplibre-gl-worker.mjs');
window.maplibregl = maplibregl;
