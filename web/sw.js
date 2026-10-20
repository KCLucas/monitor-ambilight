// Minimaler Service Worker: macht die Seite installierbar. Kein Cache - die Seite zeigt Live-Daten
// vom PC und ist ohne laufendes PC-Programm ohnehin nutzlos.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => event.respondWith(fetch(event.request)));
