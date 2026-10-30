// Minimal service worker: makes the dashboard installable on Android and keeps
// its shell (CSS, icons) available offline. Pages themselves are always
// fetched live -- they are the current state of every pillar, so a cached copy
// would be worse than an honest "offline" page.
const SHELL = "workshop-shell-v5";
const ASSETS = ["/static/app.css", "/static/icons/icon-192.png", "/offline"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    // Assets are requested with a ?v= cache-buster, so an exact hit is only
    // possible once that version was fetched. Fetch it, keep a copy, and on a
    // flaky network fall back to any cached version of the same file rather
    // than rendering the page unstyled.
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(SHELL).then((c) => c.put(req, copy));
        }
        return resp;
      }).catch(() => caches.match(req, { ignoreSearch: true }).then((old) => old || Response.error())))
    );
    return;
  }
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
  }
});
