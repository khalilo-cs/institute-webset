/* Service worker for the public storefront (PWA).
 *
 * Deliberately minimal and privacy-safe:
 *  - /api/*, /admin*, /uploads/* and every non-GET request go straight to the network: no orders, sessions,
 *    credentials, catalogue data or admin pages are ever stored in the cache.
 *  - Page navigations are network-first; only a static offline notice is cached as the fallback, so a customer
 *    never sees stale prices or stock.
 *  - Only the bundled, public brand images (/assets, /icons) are cached.
 */
const CACHE = 'fakhama-static-v2';
const OFFLINE_URL = '/offline.html';
const PRECACHE = [OFFLINE_URL, '/icons/icon-192.png'];
const NEVER_CACHE_PREFIXES = ['/api/', '/admin', '/uploads/'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith('fakhama-') && key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (NEVER_CACHE_PREFIXES.some(prefix => url.pathname.startsWith(prefix))) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/icons/')) {
    event.respondWith(
      caches.match(request).then(hit => hit || fetch(request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, copy));
        }
        return response;
      }))
    );
  }
});
