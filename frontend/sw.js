// Caches the app shell; API calls always go to the network.
const CACHE = 'broombuster-v66';
const SHELL = [
  '/', '/styles.css',
  '/js/urgency.js', '/js/core.js', '/js/ui.js', '/js/map.js',
  '/js/markers.js', '/js/calendar.js', '/js/data.js', '/js/account.js', '/js/cars.js', '/js/homes.js', '/js/app.js',
  '/vendor/maplibre-gl.mjs', '/vendor/maplibre-gl-worker.mjs',
  '/vendor/maplibre-gl.css', '/vendor/pmtiles.js',
  '/manifest.json', '/icon-192.png', '/icon-512.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (['/check', '/address', '/cities', '/health', '/auth', '/me', '/locate'].some(p => url.pathname.startsWith(p))) return;
  event.respondWith(
    // Network first so a deploy is never masked by a stale cached shell; the cache is the offline fallback.
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
