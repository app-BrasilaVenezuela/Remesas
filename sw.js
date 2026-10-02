/* Service worker de Remesas: la app abre aunque la conexión sea lenta o no haya.
 * - Página y datos (.json): primero la red; si tarda más de 4,5 s o falla, se usa la última copia guardada.
 * - Scripts de Firebase (versión fija): primero la copia guardada.
 * - Todo lo demás (Firestore, inicio de sesión, descargas) pasa directo, sin tocar. */
const VERSION = "2026-10-02-1";
const CACHE = "remesas-" + VERSION;
const NET_TIMEOUT = 4500;
const SCOPE = self.registration.scope;

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    try { const r = await fetch(SCOPE, { cache: "reload" }); if (r.ok) await cache.put(SCOPE, r); } catch (e) { /* se guardará en la primera visita */ }
    await self.skipWaiting();
  })());
});
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.indexOf("remesas-") === 0 && k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

async function networkFirst(request, key) {
  const cache = await caches.open(CACHE);
  const network = fetch(request).then(async (res) => {
    if (res && res.ok && res.type !== "opaque") { try { await cache.put(key, res.clone()); } catch (e) { /* sin espacio */ } }
    return res;
  });
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), NET_TIMEOUT));
  try {
    const first = await Promise.race([network, timeout]);
    if (first && (first.ok || !(await cache.match(key)))) return first; // la red respondió a tiempo
  } catch (e) { /* sin red: se usa la copia */ }
  const cached = await cache.match(key);
  if (cached) { network.catch(() => {}); return cached; } // abre con la copia; la red sigue actualizándola
  return network;
}
async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request.url);
  if (hit) return hit;
  const res = await fetch(request.url, { mode: "cors" });
  if (res.ok) { try { await cache.put(request.url, res.clone()); } catch (e) { /* sin espacio */ } }
  return res;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.indexOf("/firebasejs/") !== -1 && /\.js$/.test(url.pathname)) {
    event.respondWith(cacheFirst(req).catch(() => fetch(req)));
    return;
  }
  if (url.origin !== self.location.origin) return;
  const isNav = req.mode === "navigate" && !/\.(apk|zip)$/i.test(url.pathname);
  if (isNav) event.respondWith(networkFirst(req, SCOPE));
  else if (/\.json$/i.test(url.pathname)) event.respondWith(networkFirst(req, url.origin + url.pathname));
});
