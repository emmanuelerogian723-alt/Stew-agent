/* Stew Agent PWA service worker — offline shell + fast loads */
const CACHE = 'stew-app-v2';
const SHELL = [
  '/app/',
  '/app/index.html',
  '/app/manifest.webmanifest',
  '/app/icons/icon-192.png',
  '/app/icons/icon-512.png',
  '/app/icons/icon-440.png'
];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // API calls: network only (never cache live AI responses)
  if (url.pathname.startsWith('/v1') || url.pathname.startsWith('/skills') ||
      url.pathname.startsWith('/personas') || url.pathname.startsWith('/generate-image') ||
      url.pathname.startsWith('/browse')) return;
  // App shell: cache-first, refresh in background
  if (url.pathname.startsWith('/app')) {
    e.respondWith(
      caches.match(e.request).then((cached) => {
        const fresh = fetch(e.request).then((resp) => {
          if (resp.ok) caches.open(CACHE).then((c) => c.put(e.request, resp.clone()));
          return resp;
        }).catch(() => cached);
        return cached || fresh;
      })
    );
  }
});
