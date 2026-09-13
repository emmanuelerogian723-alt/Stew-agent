/* Stew Agent PWA service worker — offline shell + fast loads
   Strategy: NETWORK-FIRST for the app shell. A cache-first shell means a shipped
   bugfix can sit invisible on a user's phone indefinitely (exactly what happened
   with the /generate-image fix — deployed, but the phone kept serving the old
   cached index.html). Network-first fixes that permanently: online users always
   get the latest code; offline users still get the last-known-good cache. */
const CACHE = 'stew-app-v6';
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
  ).then(() => self.clients.claim()).then(() => {
    // Tell every open tab a fresh version just took over, so it can reload once.
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      clients.forEach((c) => c.postMessage({ type: 'SW_UPDATED' }));
    });
  }));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // API calls: network only (never cache live AI responses)
  if (url.pathname.startsWith('/v1') || url.pathname.startsWith('/skills') ||
      url.pathname.startsWith('/personas') || url.pathname.startsWith('/generate') ||
      url.pathname.startsWith('/agent') || url.pathname.startsWith('/api') ||
      url.pathname.startsWith('/browse')) return;
  // App shell: network-first, cache is only the offline fallback
  if (url.pathname.startsWith('/app')) {
    e.respondWith(
      fetch(e.request).then((resp) => {
        if (resp.ok) caches.open(CACHE).then((c) => c.put(e.request, resp.clone()));
        return resp;
      }).catch(() => caches.match(e.request))
    );
  }
});
