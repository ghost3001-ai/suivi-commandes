document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('dashboard-root');
    if (!root) {
        return;
    }

    const alertsUrl = root.dataset.alertsUrl || '';
    const pollSeconds = Math.max(parseInt(root.dataset.pollSeconds || '30', 10), 10);
    const embedCode = root.dataset.embedCode || '';
    const sectionLinks = Array.from(document.querySelectorAll('[data-dashboard-section-link]'));
    const sections = sectionLinks
        .map((link) => document.querySelector(link.getAttribute('href')))
        .filter(Boolean);
    const seenAlertIds = new Set(
        Array.from(document.querySelectorAll('#dashboardAlertFeed .list-group-item')).map((item) => item.dataset.alertId).filter(Boolean)
    );

    function getBadgeClass(level) {
        if (level === 'danger') return 'bg-danger';
        if (level === 'warning') return 'bg-warning text-dark';
        return 'bg-info text-dark';
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function resizeCharts() {
        if (!Array.isArray(window.dashboardCharts)) {
            return;
        }
        window.dashboardCharts.forEach((chart) => {
            try {
                chart.resize();
            } catch (error) {
                console.error('Resize chart error', error);
            }
        });
    }

    function setActiveSection(sectionId) {
        sectionLinks.forEach((link) => {
            link.classList.toggle('active', link.getAttribute('href') === `#${sectionId}`);
        });
    }

    function scrollToSection(section) {
        if (!section) return;
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        setActiveSection(section.id);
    }

    function nextSection(offset) {
        const activeIndex = sectionLinks.findIndex((link) => link.classList.contains('active'));
        const startIndex = activeIndex >= 0 ? activeIndex : 0;
        const targetIndex = Math.max(0, Math.min(sections.length - 1, startIndex + offset));
        scrollToSection(sections[targetIndex]);
    }

    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            return null;
        }
        try {
            return await navigator.serviceWorker.register('/sw.js');
        } catch (error) {
            console.error('Service worker registration failed', error);
            return null;
        }
    }

    async function notifyBrowser(alert) {
        if (!('Notification' in window) || Notification.permission !== 'granted') {
            return;
        }

        const registration = await navigator.serviceWorker.getRegistration();
        const payload = {
            type: 'SHOW_NOTIFICATION',
            title: alert.title,
            body: alert.message,
            tag: alert.id,
        };

        if (registration && registration.active) {
            registration.active.postMessage(payload);
        } else if (registration) {
            registration.showNotification(alert.title, {
                body: alert.message,
                tag: alert.id,
            });
        } else {
            new Notification(alert.title, { body: alert.message, tag: alert.id });
        }
    }

    async function requestNotifications() {
        if (!('Notification' in window)) {
            showNotification('Notifications navigateur non supportées sur cet appareil.', 'warning');
            return;
        }
        const permission = await Notification.requestPermission();
        if (permission === 'granted') {
            showNotification('Notifications activées.', 'success');
        } else {
            showNotification('Permission notifications refusée.', 'warning');
        }
    }

    async function captureDashboard() {
        if (typeof html2canvas !== 'function') {
            showNotification('Le module de capture n’est pas chargé.', 'danger');
            return;
        }

        try {
            const canvas = await html2canvas(root, {
                scale: 2,
                useCORS: true,
                backgroundColor: '#f8f9fc',
            });
            const link = document.createElement('a');
            link.href = canvas.toDataURL('image/png');
            link.download = `dashboard_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.png`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            showNotification('Capture téléchargée.', 'success');
        } catch (error) {
            console.error('Dashboard capture failed', error);
            showNotification('Erreur lors de la capture du dashboard.', 'danger');
        }
    }

    async function copyEmbedCode() {
        if (!embedCode) {
            showNotification('Code embed indisponible.', 'warning');
            return;
        }

        try {
            await navigator.clipboard.writeText(embedCode);
            showNotification('Code embed copié dans le presse-papiers.', 'success');
        } catch (error) {
            console.error('Clipboard error', error);
            showNotification('Impossible de copier le code embed.', 'danger');
        }
    }

    function renderAlerts(payload) {
        const feed = document.getElementById('dashboardAlertFeed');
        const count = document.getElementById('dashboardAlertCount');
        if (!feed || !payload || !Array.isArray(payload.alerts)) {
            return;
        }

        feed.innerHTML = payload.alerts.length
            ? payload.alerts.map((alert) => `
                <div class="list-group-item level-${escapeHtml(alert.level)}" data-alert-id="${escapeHtml(alert.id)}">
                    <div class="d-flex justify-content-between align-items-start gap-3">
                        <div>
                            <div class="fw-semibold">${escapeHtml(alert.title)}</div>
                            <div class="small text-muted">${escapeHtml(alert.message)}</div>
                        </div>
                        <span class="badge ${getBadgeClass(alert.level)}">${escapeHtml(alert.category)}</span>
                    </div>
                    ${alert.url ? `<a href="${escapeHtml(alert.url)}" class="small text-decoration-none">Ouvrir</a>` : ''}
                </div>
            `).join('')
            : '<div class="list-group-item text-muted">Aucune alerte active</div>';

        if (count) {
            count.textContent = payload.counts?.total ?? payload.alerts.length;
        }
    }

    async function pollAlerts(initial = false) {
        if (!alertsUrl) {
            return;
        }

        try {
            const response = await fetch(alertsUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok) {
                return;
            }
            const payload = await response.json();
            renderAlerts(payload);

            payload.alerts.forEach((alert) => {
                if (seenAlertIds.has(alert.id)) {
                    return;
                }
                seenAlertIds.add(alert.id);
                if (!initial) {
                    showNotification(`${alert.title}: ${alert.message}`, alert.level === 'danger' ? 'danger' : alert.level === 'warning' ? 'warning' : 'info');
                    notifyBrowser(alert);
                }
            });
        } catch (error) {
            console.error('Alert polling failed', error);
        }
    }

    sectionLinks.forEach((link) => {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            const target = document.querySelector(link.getAttribute('href'));
            scrollToSection(target);
        });
    });

    if ('IntersectionObserver' in window && sections.length) {
        const observer = new IntersectionObserver((entries) => {
            const visible = entries
                .filter((entry) => entry.isIntersecting)
                .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
            if (visible?.target?.id) {
                setActiveSection(visible.target.id);
            }
        }, { threshold: 0.35 });

        sections.forEach((section) => observer.observe(section));
    }

    let touchStartX = 0;
    let touchStartY = 0;
    root.addEventListener('touchstart', (event) => {
        if (!event.changedTouches.length) return;
        touchStartX = event.changedTouches[0].clientX;
        touchStartY = event.changedTouches[0].clientY;
    }, { passive: true });

    root.addEventListener('touchend', (event) => {
        if (!event.changedTouches.length) return;
        const deltaX = event.changedTouches[0].clientX - touchStartX;
        const deltaY = event.changedTouches[0].clientY - touchStartY;
        if (Math.abs(deltaX) < 60 || Math.abs(deltaX) < Math.abs(deltaY)) {
            return;
        }
        nextSection(deltaX < 0 ? 1 : -1);
    }, { passive: true });

    document.getElementById('dashboardCaptureBtn')?.addEventListener('click', captureDashboard);
    document.getElementById('dashboardNotificationsBtn')?.addEventListener('click', requestNotifications);
    document.getElementById('dashboardCopyEmbedBtn')?.addEventListener('click', copyEmbedCode);

    document.querySelectorAll('[data-dashboard-layout]').forEach((button) => {
        button.addEventListener('click', () => {
            const mode = button.dataset.dashboardLayout;
            root.classList.toggle('dashboard-landscape', mode === 'landscape');
            document.querySelectorAll('[data-dashboard-layout]').forEach((otherButton) => {
                otherButton.classList.toggle('active', otherButton === button);
            });
            resizeCharts();
        });
    });

    if ('ResizeObserver' in window) {
        const resizeObserver = new ResizeObserver(() => resizeCharts());
        document.querySelectorAll('.dashboard-chart-shell').forEach((shell) => resizeObserver.observe(shell));
    }

    window.addEventListener('resize', resizeCharts);
    window.addEventListener('orientationchange', resizeCharts);

    registerServiceWorker();
    pollAlerts(true);
    window.setInterval(() => pollAlerts(false), pollSeconds * 1000);
});
