// Fonctions utilitaires pour l'application

// Formatage des montants
function formatMontant(montant) {
    return new Intl.NumberFormat('fr-FR').format(montant) + ' FCFA';
}

// Formatage des dates
function formatDate(date) {
    if (!date) return '-';
    const d = new Date(date);
    return d.toLocaleDateString('fr-FR');
}

// Confirmation de suppression
function confirmSuppression(message) {
    return confirm(message || 'Êtes-vous sûr de vouloir supprimer cet élément ?');
}

// Affichage des notifications
function showNotification(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        <i class="bi bi-${type === 'success' ? 'check-circle' : type === 'danger' ? 'exclamation-triangle' : 'info-circle'} me-2"></i>
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    const container = document.querySelector('main .container-fluid') || document.querySelector('main');
    container.insertBefore(alertDiv, container.firstChild);
    
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}

// Export des données
async function exporterDonnees() {
    try {
        const response = await fetch('/exporter/excel');
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `commandes_${new Date().toISOString().split('T')[0]}.xlsx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        showNotification('Export réussi', 'success');
    } catch (error) {
        console.error('Erreur export:', error);
        showNotification('Erreur lors de l\'export', 'danger');
    }
}

// Initialisation des tooltips Bootstrap
document.addEventListener('DOMContentLoaded', function() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
});

// Auto-refresh des données (optionnel)
let autoRefreshInterval = null;

function startAutoRefresh(interval = 30000) {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);
    autoRefreshInterval = setInterval(() => {
        location.reload();
    }, interval);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}