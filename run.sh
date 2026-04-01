#!/bin/bash
# Script de démarrage de l'application en développement

set -e

echo "=========================================="
echo "Démarrage de l'application Suivi Commandes"
echo "=========================================="

# Vérifier les dépendances
if ! command -v python3 &> /dev/null; then
    echo "✗ Python 3 n'est pas installé"
    exit 1
fi

# Créer venv s'il n'existe pas
if [ ! -d "venv" ]; then
    echo "📦 Création de l'environnement virtuel..."
    python3 -m venv venv
fi

# Activer venv
echo "🔧 Activation de l'environnement virtuel..."
source venv/bin/activate 2>/dev/null || . venv/Scripts/activate

# Installer les dépendances
if ! python -c "import flask" >/dev/null 2>&1; then
    echo "📥 Installation des dépendances..."
    pip install -r requirements.txt
fi

# Initialiser la base de données si nécessaire
if [ ! -f "instance/commandes.db" ]; then
    echo "🗄️  Initialisation de la base de données..."
    python init_app.py
fi

# Démarrer l'app
echo ""
echo "✓ Démarrage de l'application..."
echo "🌐 Accédez à: http://localhost:5000"
echo "👤 Logins: admin / admin123"
echo "Press Ctrl+C pour arrêter"
echo ""

python app.py
