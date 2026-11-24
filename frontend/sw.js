// Caches the app shell; API calls always go to the network.
// The server fills in the three double-underscore names below (api/web.py, service_worker_response).
const CACHE = 'broombuster-__VERSION__';
const API_PATHS = __API_PATHS__;
const SHELL = __SHELL__;
const PAGE_WAIT_MS = 2500;

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
  const nav = event.request.mode === 'navigate', get = event.request.method === 'GET';
  const cached = () => caches.match(event.request, { ignoreSearch: true })
    .then(hit => hit || (nav ? caches.match('/') : undefined));
  // A file named by its content hash (?v=, api/web.py) never changes: cache first, kept
  // once fetched; offline without it, any cached copy.
  if (get && url.searchParams.has('v')) {
    event.respondWith(caches.match(event.request).then(hit => hit || fetch(event.request).then(res => {
      if (res.status === 200) {
        const copy = res.clone();
        event.waitUntil(caches.open(CACHE).then(cache => cache.put(event.request, copy)));
      }
      return res;
    }, cached)));
    return;
  }
  // The page and other files: network first so a deploy is never masked by a stale cached
  // shell; the cache is the fallback offline and while the server answers 5xx (a restart).
  // A page falls back to "/", and to it also when the network takes over PAGE_WAIT_MS
  // (a weak signal), so a cached app opens in seconds rather than waiting out the request.
  const net = fetch(event.request)
    .then(res => (get && res.status >= 500 ? cached().then(hit => hit || res) : res))
    .catch(cached);
  if (!nav) { event.respondWith(net); return; }
  const slow = new Promise(resolve => setTimeout(resolve, PAGE_WAIT_MS)).then(cached).then(hit => hit || net);
  event.respondWith(Promise.race([net, slow]));
});
