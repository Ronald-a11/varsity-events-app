/* Varsity Events service worker — build {{ version }}
 *
 * Rendered by core/pwa.py, not a static file: it needs the hashed stylesheet
 * URL and a version that moves on every deploy.
 *
 * The job, in order of how much it matters:
 *
 *   1. A ticket must open at a gate with no signal. Ticket pages are cached the
 *      moment they are first seen, in a cache that survives deploys, and the QR
 *      is inline in the page so there is no second request to fail.
 *   2. Nothing to do with money or identity is ever stored. See NEVER below.
 *   3. Everything else is a bonus: the shell loads instantly, and pages already
 *      seen still open on a dead connection.
 */

const VERSION = "{{ version }}";
const SHELL = `ve-shell-${VERSION}`;
const PAGES = `ve-pages-${VERSION}`;

// Not versioned, on purpose. A deploy doesn't invalidate somebody's ticket, and
// wiping this would delete the offline copy at exactly the wrong moment.
const TICKETS = "ve-tickets";

const OFFLINE_URL = "{{ offline_url }}";
const TICKET_PREFIX = "{{ ticket_prefix }}";

const PRECACHE = [
  OFFLINE_URL,
  {% if css_url %}"{{ css_url }}",{% endif %}
  {% if icon_192 %}"{{ icon_192 }}",{% endif %}
];

// Never stored, never answered from a cache. A stale payment page can tell
// somebody their money failed when it went through; a cached account page on a
// shared campus machine belongs to whoever used it last.
const NEVER = [{% for path in never_cache %}"{{ path }}",{% endfor %}];

const isPrivate = (pathname) => NEVER.some((prefix) => pathname.startsWith(prefix));
const isTicket = (pathname) => pathname.startsWith(TICKET_PREFIX);
const isAsset = (pathname) => pathname.startsWith("/static/");

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      // addAll is all-or-nothing: one 404 and the whole worker fails to install,
      // taking offline tickets down with it. Each file is allowed to fail alone.
      .then((cache) => Promise.allSettled(PRECACHE.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name.startsWith("ve-") && name !== SHELL && name !== PAGES && name !== TICKETS)
            .map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

/* Signing out has to take the cached pages with it.
 *
 * Phones and laptops get shared, and a cached ticket or profile page outliving
 * the session it belonged to is somebody else's private data sitting on the
 * device. The logout POST is the one reliable moment to catch that.
 */
async function forgetEverythingPrivate() {
  await Promise.all([caches.delete(PAGES), caches.delete(TICKETS)]);
}

self.addEventListener("message", (event) => {
  if (event.data === "signed-out") event.waitUntil(forgetEverythingPrivate());
});

async function networkFirst(request, cacheName, { fallback } = {}) {
  try {
    const response = await fetch(request);
    // Only store a real answer. A 302 to the sign-in page or a 500 cached as a
    // ticket would be worse than nothing.
    if (response.ok && response.type === "basic") {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (fallback) {
      const offline = await caches.match(fallback);
      if (offline) return offline;
    }
    throw err;
  }
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    const cache = await caches.open(cacheName);
    cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (url.origin !== self.location.origin) return;

  if (request.method === "POST" && url.pathname.startsWith("/accounts/logout")) {
    event.waitUntil(forgetEverythingPrivate());
    return; // let the request itself go to the network untouched
  }

  if (request.method !== "GET") return;
  if (isPrivate(url.pathname)) return;

  // The whole point. Fresh when there's a signal, from the device when there
  // isn't, and kept across deploys.
  if (isTicket(url.pathname)) {
    event.respondWith(networkFirst(request, TICKETS, { fallback: OFFLINE_URL }));
    return;
  }

  // Hash-named by WhiteNoise, so a cached copy is never the wrong one.
  if (isAsset(url.pathname)) {
    event.respondWith(cacheFirst(request, SHELL));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, PAGES, { fallback: OFFLINE_URL }));
  }
});

/* -------------------------------------------------------------------------
 * Push
 *
 * Only ever sent for something time-critical and actionable — a seat that
 * expires, money that landed, doors that open in an hour. See
 * notifications/push.py for why that rule exists and what it costs to break it.
 * ---------------------------------------------------------------------- */

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch (err) {
    payload = { title: "{{ SITE_NAME }}", body: event.data.text() };
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || "{{ SITE_NAME }}", {
      body: payload.body || "",
      icon: {% if icon_192 %}"{{ icon_192 }}"{% else %}undefined{% endif %},
      badge: {% if icon_192 %}"{{ icon_192 }}"{% else %}undefined{% endif %},
      // Same tag replaces rather than stacks: three reminders for one event is
      // how a permission gets revoked.
      tag: payload.tag || payload.url || "varsity",
      renotify: false,
      data: { url: payload.url || "/" },
      // A ticket about to be needed is worth a buzz; nothing else here is.
      vibrate: [80, 40, 80],
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;

  // Focus a tab that's already open on it rather than piling up new ones.
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((tabs) => {
      for (const tab of tabs) {
        if (tab.url === target && "focus" in tab) return tab.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
