const CACHE_NAME = 'sundarkand-shell-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/static/index.html',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/icon.svg'
];

// Install: pre-cache shell assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    }).then(() => self.skipWaiting())
  );
});

// Activate: clean up old caches and claim clients immediately
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: Network-first for HTML and API, Stale-While-Revalidate for cached static assets
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never cache API or SSE endpoints
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // Navigation requests: network first with offline fallback to cached index
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match('/static/index.html') || caches.match('/');
      })
    );
    return;
  }

  // Static assets: Stale-While-Revalidate
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      const fetchPromise = fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return networkResponse;
      }).catch(() => {
        // Network fail - cached response already returned if available
      });
      return cachedResponse || fetchPromise;
    })
  );
});

// Push notification handling
self.addEventListener('push', (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data = { title: 'Sundarkand Seva Team', body: event.data.text() };
    }
  }

  const title = data.title || 'Sundarkand Seva Team';
  const options = {
    body: data.body || 'New seva update available.',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    vibrate: [150, 60, 150],
    data: {
      url: data.url || '/'
    },
    tag: data.tag || 'sundarkand-notification',
    renotify: true
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

// Notification click handling
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = event.notification.data && event.notification.data.url ? event.notification.data.url : '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // If an existing tab is open, focus it and navigate
      for (let client of windowClients) {
        if ('focus' in client) {
          client.focus();
          client.postMessage({ type: 'NAVIGATE', url: targetUrl });
          return;
        }
      }
      // Otherwise open a new window
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
