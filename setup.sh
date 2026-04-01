#!/bin/bash
# Setup et démarrage simple et rapide

set -e  # Exit on error

echo "==========================================="
echo "Setup Application Suivi Commandes"
echo "==========================================="
echo ""

# Config
VENV_DIR="venv"
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

# 1. Vérifier Python
echo "🔍 Vérification Python..."
if ! command -v python3.12 &> /dev/null; then
    echo "⚠️  Python 3.12 non trouvé, utilisation de python3..."
    if ! command -v python3 &> /dev/null; then
        echo "❌ Python 3 n'est pas installé"
        exit 1
    fi
    PYTHON="python3"
else
    PYTHON="python3.12"
fi
echo "✓ Python: $($PYTHON --version)"
echo ""

# 2. Créer venv
echo "🔧 Environnement virtuel..."
if [ ! -d "$VENV_DIR" ]; then
    echo "  Création de $VENV_DIR..."
    $PYTHON -m venv "$VENV_DIR"
else
    echo "  $VENV_DIR existe déjà"
fi

# Activer venv
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    source "$VENV_DIR/Scripts/activate"
else
    source "$VENV_DIR/bin/activate"
fi
echo "✓ Venv activé"
echo ""

# 3. Installer dépendances
echo "📦 Dépendances..."
if ! $PYTHON -c "import flask" 2>/dev/null; then
    echo "  Installation en cours..."
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1
    pip install -r requirements.txt > /dev/null 2>&1
    echo "✓ Dépendances installées"
else
    echo "✓ Dépendances déjà installées"
fi
echo ""

# 4. Initialiser BD
echo "🗄️  Base de données..."
if [ ! -f "instance/commandes.db" ]; then
    echo "  Création de la base..."
    $PYTHON init_app.py
    echo "✓ Base créée"
else
    echo "✓ Base existe déjà"
fi
echo ""

# 5. Démarrer
echo "==========================================="
echo "✓ Setup TERMINÉ!"
echo "==========================================="
echo ""
echo "🌐 URL: http://localhost:5000"
echo "👤 Admin: admin / admin123"
echo "🛑 Arrêt: Ctrl+C"
echo ""
echo "Démarrage de l'app..."
echo ""

$PYTHON app.py
