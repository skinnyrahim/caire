// CAIRE Cover service worker: makes the app installable and lets the screens
// open on a weak connection. Member data (/api) is never cached.
const CACHE = 'caire-shell-v2';
const SHELL = ['/', '/app.js', '/style.css', '/manifest.json', '/icons/icon-192.png', '/icons/mark.png', '/icons/logo.png', '/icons/favicon-32.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Network first, so updates show straight away; fall back to the cached shell offline.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin ||
      url.pathname.startsWith('/api/') || url.pathname === '/healthz') return;
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then(r => r || caches.match('/')))
  );
});
