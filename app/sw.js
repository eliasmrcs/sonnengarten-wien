// Offline support: app shell and shadow data are cached; map tiles are cached
// as they are viewed; the weather is always fetched fresh.
const VERSION = 'v1';
const SHELL = [
  './', 'index.html', 'style.css', 'app.js', 'sun.js', 'manifest.webmanifest', 'icon.svg',
  'vendor/leaflet.js', 'vendor/leaflet.css', 'data/places.json', 'data/horizons.bin',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION && k !== 'tiles').map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.hostname === 'api.open-meteo.com') return;
  if (url.hostname === 'mapsneu.wien.gv.at') {
    e.respondWith(caches.open('tiles').then(async (c) => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok || res.type === 'opaque') c.put(e.request, res.clone());
      return res;
    }));
    return;
  }
  if (url.origin === location.origin) {
    // network first so updates arrive, cache as fallback for offline use
    e.respondWith(fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(VERSION).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request)));
  }
});
