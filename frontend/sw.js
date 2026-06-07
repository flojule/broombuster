// Minimal service worker — caches the app shell for instant load.
// API calls (/check, /prefs) are always fetched from the network.

const CACHE = 'broombuster-v38';
const SHELL = ['/', '/styles.css', '/js/app.js', '/manifest.json', '/icon-192.png', '/icon-512.png'];

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

  // Always go to network for API calls
  if (url.pathname.startsWith('/check') ||
      url.pathname.startsWith('/prefs') ||
      url.pathname.startsWith('/cities') ||
      url.pathname.startsWith('/health')) {
    return;
  }

  // Cache-first for shell assets
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
