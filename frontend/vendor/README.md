# Vendored libraries

Committed so the PWA works offline and needs no CDN or build step.

| File | Library | Version | Source |
|---|---|---|---|
| `maplibre-gl.mjs`, `maplibre-gl-worker.mjs`, `maplibre-gl.css` | MapLibre GL JS | 6.14.0 | https://github.com/maplibre/maplibre-gl-js (`dist/`) |
| `pmtiles.js` | PMTiles | see header of `pmtiles.js` | https://github.com/protomaps/PMTiles (`js/dist/`) |

Both are BSD-3-Clause; their licence text is in the file headers. To upgrade: replace the files from the release's `dist/` folder, run the app and
check the map. The server hashes `vendor/*`, so cache URLs and the service
worker update by themselves.
