/* MediKiosk service worker.
 *
 * Precaches the app shell so the interview loads with no network, and serves
 * navigations from cache when offline. Clinical API writes are NOT cached or
 * replayed here -- they go through the IndexedDB operation queue in
 * src/lib/offline.ts, where each carries an idempotency key the server
 * enforces. Replaying a POST from a generic SW cache would risk duplicating a
 * clinical fact, which is exactly what the queue exists to prevent.
 */

const VERSION = "medikiosk-v1";
const SHELL = [
  "/",
  "/kiosk",
  "/manifest.webmanifest",
  "/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never serve a stale clinical read from cache.
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(request, copy));
          return res;
        })
        .catch(() =>
          caches
            .match(request)
            .then((hit) => hit || caches.match("/kiosk"))
            .then((hit) => hit || new Response("Offline", { status: 503 })),
        ),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request)
          .then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(VERSION).then((c) => c.put(request, copy));
            }
            return res;
          })
          .catch(() => new Response("", { status: 504 })),
    ),
  );
});
