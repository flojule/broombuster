// Caches the app shell; API calls always go to the network.
// The server fills in the three double-underscore names below (api/web.py, service_worker_response).
const CACHE = 'broombuster-__VERSION__';
const API_PATHS = __API_PATHS__;
const SHELL = __SHELL__;

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
  // Other origins (the basemap), API calls and our street tiles skip the worker: the tiles
  // are range requests, which Safari has mishandled through a worker, and never offline.
  if (url.origin !== location.origin || url.pathname.startsWith('/tiles/')
      || API_PATHS.some(p => url.pathname.startsWith(p))) return;
  // Network first so a deploy is never masked by a stale cached shell; the cache is the
  // fallback offline and while the server answers 5xx (a restart). A page falls back to "/".
  const nav = event.request.mode === 'navigate', get = event.request.method === 'GET';
  const cached = () => caches.match(event.request, { ignoreSearch: true })
    .then(hit => hit || (nav ? caches.match('/') : undefined));
  event.respondWith(
    fetch(event.request)
      .then(res => (get && res.status >= 500 ? cached().then(hit => hit || res) : res))
      .catch(cached)
  );
});
