const CACHE_NAME = 'blog-cms-v3';
const OFFLINE_URL = '/offline';

const PRECACHE_URLS = [
  '/',
  '/offline',
  '/static/css/style.css',
  '/dynamic-styles.css',
  '/static/manifest.json',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css'
];

// Install: pre-cache shell assets
self.addEventListener('install', function (event) {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(PRECACHE_URLS);
    })
  );
});

// Activate: remove old caches
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (cacheNames) {
      return Promise.all(
        cacheNames
          .filter(function (name) { return name !== CACHE_NAME; })
          .map(function (name) { return caches.delete(name); })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// Fetch: network-first for HTML/API, cache-first for static assets
self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);

  // Skip non-GET requests and chrome-extension requests
  if (event.request.method !== 'GET' || url.protocol === 'chrome-extension:') {
    return;
  }

  // Admin routes: always network (never serve cached admin pages)
  if (url.pathname.startsWith('/admin')) {
    return;
  }

  // Static assets: cache-first
  if (url.pathname.startsWith('/static') || url.hostname.includes('cdn.jsdelivr') || url.hostname.includes('cdnjs.cloudflare')) {
    event.respondWith(
      caches.match(event.request).then(function (cached) {
        return cached || fetch(event.request).then(function (response) {
          if (response && response.status === 200) {
            var clone = response.clone();
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(event.request, clone);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // HTML pages: network-first, fall back to cache, then offline page
  event.respondWith(
    fetch(event.request)
      .then(function (response) {
        if (response && response.status === 200) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function (cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      })
      .catch(function () {
        return caches.match(event.request).then(function (cached) {
          return cached || caches.match(OFFLINE_URL);
        });
      })
  );
});
