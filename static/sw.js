self.addEventListener('install', (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('message', (event) => {
    const data = event.data || {};
    if (data.type !== 'SHOW_NOTIFICATION') {
        return;
    }

    event.waitUntil(
        self.registration.showNotification(data.title || 'Dashboard', {
            body: data.body || '',
            tag: data.tag || 'dashboard-alert',
            renotify: false,
        })
    );
});

self.addEventListener('push', (event) => {
    let payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch (error) {
        payload = { title: 'Dashboard', body: event.data ? event.data.text() : '' };
    }

    event.waitUntil(
        self.registration.showNotification(payload.title || 'Dashboard', {
            body: payload.body || '',
            tag: payload.tag || 'dashboard-push',
            renotify: true,
        })
    );
});
