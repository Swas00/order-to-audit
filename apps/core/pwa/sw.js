/* Service worker: lets a salesman open the order page with no network.
   - /orders/new/ (and /orders/) : network first, last good copy as fallback
   - /static/                    : cached copy first, refreshed in the background
   Orders themselves are queued by offline.js in the page, not here. */
const CACHE = "oas-shell-v1";
const PRECACHE = ["/static/sales/offline.js", "/static/core/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const PAGES = ["/orders/new/", "/orders/"];

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    const loggedOut = response.redirected && new URL(response.url).pathname.startsWith("/login/");
    if (response.ok && !loggedOut) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    return new Response(
      "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Offline</title>" +
      "<body style='font-family:system-ui;padding:24px'><h1>You are offline</h1>" +
      "<p>Open the order page once while online so it can be used without a network.</p>",
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const refresh = fetch(request).then((r) => { if (r.ok) cache.put(request, r.clone()); return r; }).catch(() => cached);
  return cached || refresh;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.mode === "navigate" && PAGES.includes(url.pathname)) event.respondWith(networkFirst(req));
  else if (url.pathname.startsWith("/static/")) event.respondWith(staleWhileRevalidate(req));
});

self.addEventListener("message", (event) => {
  if (event.data === "clear-cache") event.waitUntil(caches.delete(CACHE));
});
