/* Service Worker: nur damit die App installierbar ist und die Huelle
   ohne Netz erscheint. Die API wird NIE aus dem Speicher beantwortet -
   ein "Tuer offen" aus dem Cache waere eine Luege. */
const SPEICHER = "tuer-v10";
const HUELLE = ["./", "static/app.js", "static/style.css", "static/icon-192.png", "manifest.webmanifest"];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(SPEICHER).then((c) => c.addAll(HUELLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(
    caches.keys()
      .then((namen) => Promise.all(namen.filter((n) => n !== SPEICHER).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (ev) => {
  const url = new URL(ev.request.url);
  // Fremdes (die Kartenkacheln) geht direkt ans Netz: fuer den Service
  // Worker selbst gilt "default-src 'self'", er duerfte es gar nicht holen
  if (ev.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.includes("/api/")) return;
  // Netz zuerst, damit eine neue Fassung sofort gilt; Speicher nur ohne Netz
  ev.respondWith(
    fetch(ev.request)
      .then((antwort) => {
        const kopie = antwort.clone();
        if (antwort.ok) caches.open(SPEICHER).then((c) => c.put(ev.request, kopie));
        return antwort;
      })
      .catch(() => caches.match(ev.request)),
  );
});
