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

  if (API_PATHS.some(p => url.pathname.startsWith(p))) return;
  event.respondWith(
    // Network first so a deploy is never masked by a stale cached shell; the cache is the offline fallback.
    fetch(event.request).catch(() => caches.match(event.request, { ignoreSearch: true }))
  );
});
